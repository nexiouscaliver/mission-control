"""W4-L4 MR/PR registry tests (mr1-): list -> lane-branch filter -> live-row /
forge-manifest join -> registry entry shape, conflicts derivation, TTL 300 s,
repo-set derivation, and the contract-v3 ``merges`` key.

Every case drives ``collect_state`` over a fixture world (temp db + temp note +
temp repo dirs + a forge tree) with ``mc_wall.tower.netcache._run_cmd``
monkeypatched — ZERO real subprocess/git/glab/gh execution, zero network (the
hermeticity rule the corpus tests document for T-5 applies to the registry
too). CLI payload field names were verified LIVE (read-only) 2026-10-07:
``gh pr list --state all --json number,...,mergeable,url,author`` (gh 2.78.0)
and ``glab mr list --all -F json -P 100`` (glab 1.67.0; raw API objects with
iid/source_branch/merged_at/draft/web_url/detailed_merge_status/author.username).
"""

import json
import os

from mc_wall.tower import (NetCache, ProgramConfig, RepoConfig, TowerConfig,
                           collect_state, contract)
from mc_wall.tower import netcache as netcache_module
from tests.tower.conftest import (mcwallt_fake_cmd, mcwallt_make_note,
                                  mcwallt_make_session_db,
                                  mcwallt_settable_clock)

NOW = 2_000_000_050.0
HEADER_A_LINE = "| id | wave | lane | repo/branch | slug | base | session/MR artifacts | status |"
SEP_LINE = "|---|---|---|---|---|---|---|---|"

GH_FIELDS = ("number,title,headRefName,isDraft,createdAt,updatedAt,mergedAt,"
             "state,mergeable,url,author")

# A github "git remote -v" stdout for the probe seam.
GH_REMOTE_OUT = ("origin\thttps://github.com/nexiouscaliver/mission-control.git (fetch)\n"
                 "origin\thttps://github.com/nexiouscaliver/mission-control.git (push)\n")
GL_REMOTE_OUT = ("origin\tgit@gitlab.com:regenai-gitlab/regenai-digital/cleo.git (fetch)\n"
                 "origin\tgit@gitlab.com:regenai-gitlab/regenai-digital/cleo.git (push)\n")


def gh_mr(number, branch, state="OPEN", mergeable="MERGEABLE", draft=False,
          created="2026-01-02T03:04:05Z", updated="2026-01-03T03:04:05Z",
          merged=None, author="nexiouscaliver"):
    return {"number": number, "title": f"mr {number}", "headRefName": branch,
            "isDraft": draft, "createdAt": created, "updatedAt": updated,
            "mergedAt": merged, "state": state, "mergeable": mergeable,
            "url": f"https://github.com/nexiouscaliver/mission-control/pull/{number}",
            "author": {"login": author, "name": "Some One"}}


def gl_mr(iid, branch, state="opened", detailed="mergeable", draft=False,
          created="2026-01-02T03:04:05.123Z", updated="2026-01-03T03:04:05.71Z",
          merged=None, author="shahilkadia"):
    return {"iid": iid, "title": f"mr {iid}", "source_branch": branch,
            "state": state, "detailed_merge_status": detailed, "draft": draft,
            "created_at": created, "updated_at": updated, "merged_at": merged,
            "web_url": f"https://gitlab.com/regenai-gitlab/regenai-digital/cleo/-/merge_requests/{iid}",
            "author": {"username": author, "name": "Shahil kadia"}}


def _world(tmp_path, rows, repos, name="mcwallt_mr1", clock_start=NOW):
    """collect_state world for the registry: one program whose variant-A note
    carries ``rows``, the given repos, a pending-launch path (its dirname is
    the forge root), a settable clock and a fresh NetCache."""
    note = mcwallt_make_note(tmp_path, name + "_note.md",
                             ["objective: mr1 registry", HEADER_A_LINE, SEP_LINE, *rows])
    now_s, set_now = mcwallt_settable_clock(clock_start)
    cfg = TowerConfig(
        db_path=mcwallt_make_session_db(tmp_path, name=name + ".db"),
        programs=(ProgramConfig(program="mr1-prog", tag="t", note_glob=note),),
        repos=tuple(repos),
        pending_launch_path=str(tmp_path / (name + "_launch.json")),
        now_s=now_s,
        network_cache=NetCache())
    return cfg, set_now


def _spawn(handler):
    fake, calls = mcwallt_fake_cmd(handler)
    return fake, calls


def test_mr1_contract_example_schema3_merges():
    # EXPECT-1: the contract example carries `merges` and schema_version 3,
    # and validates against SHAPES (the merge_row shape is exercised too).
    ex = contract.CONTRACT_EXAMPLE
    assert ex["schema_version"] == 3
    assert isinstance(ex["merges"], list) and len(ex["merges"]) == 1
    row = ex["merges"][0]
    assert list(row.keys()) == ["host", "repo", "number", "title", "branch",
                                "program", "row_id", "session", "state",
                                "conflicts", "draft", "created_at",
                                "updated_at", "merged_at", "url", "author"]
    assert contract.SHAPES["state"]["merges"] == ["merge_row"]
    contract.assert_shape(ex)
    assert contract.zero_document(["p"], 0, 0, None)["schema_version"] == 3
    assert contract.zero_document(["p"], 0, 0, None)["merges"] == []


def test_mr1_collect_emits_schema3_merges_key(tmp_path, monkeypatch):
    # Empty world: no repos, no rows -> merges [] with the root key PRESENT and
    # schema_version 3; no spawn happens at all (empty repo set).
    fake, calls = _spawn(lambda argv, cwd: (0, "[]"))
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    note = mcwallt_make_note(tmp_path, "empty.md",
                             ["objective: x", HEADER_A_LINE, SEP_LINE])
    cfg = TowerConfig(db_path=mcwallt_make_session_db(tmp_path),
                      programs=(ProgramConfig(program="p", tag="t", note_glob=note),),
                      now_s=lambda: NOW, network_cache=NetCache())
    state = collect_state(cfg)
    assert state["schema_version"] == 3
    assert state["merges"] == []
    assert list(state.keys()) == ["schema_version", "server", "programs",
                                  "verify_queue", "human_actions", "needs_me",
                                  "merges", "sessions_unmapped", "launch_pending"]
    assert calls["n"] == 0, "an empty repo set must spawn nothing"
    contract.assert_shape(state)


def test_mr1_gh_list_filter_join(tmp_path, monkeypatch):
    # EXPECT-2 core: CLI list output -> the ^(loop|fix)/ filter drops non-lane
    # MRs -> the live-row join sets program/row_id/session. argv pins the gh
    # template loosely (argv[0:3] + -R with the probed owner/repo slug).
    repo_path = tmp_path / "gh_repo"
    repo_path.mkdir()
    repo = RepoConfig(name="mission-control", path=str(repo_path), host="github")

    def handler(argv, cwd):
        if argv[:2] == ["git", "remote"]:
            return (0, GH_REMOTE_OUT)
        if argv[:3] == ["gh", "pr", "list"]:
            assert "-R" in argv and "nexiouscaliver/mission-control" in argv
            return (0, json.dumps([
                gh_mr(18, "loop/wall-web-timeline", state="OPEN",
                      mergeable="MERGEABLE"),
                gh_mr(19, "feature/noise"),          # dropped: not a lane branch
                gh_mr(20, "main"),                   # dropped
                gh_mr(14, "fix/unmapped-recency-order", state="MERGED",
                      mergeable="UNKNOWN", merged="2026-01-04T03:04:05Z"),
                gh_mr(21, "dev/scratch"),            # dropped
            ]))
        return (0, "[]")

    fake, _calls = _spawn(handler)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    rows = [
        f"| W2-L2 | W2 | L2 | {repo_path} loop/wall-web-timeline | wall-web | b5 | sess_aaaa0000 | launched |",
        f"| W1-L0 | W1 | L0 | {repo_path} fix/unmapped-recency-order | pr14 | d9 | sess_bbbb0000 | done |",
    ]
    cfg, _set = _world(tmp_path, rows, [repo])
    state = collect_state(cfg)
    merges = state["merges"]
    assert len(merges) == 2, "only lane branches survive the filter"
    by_number = {m["number"]: m for m in merges}
    m18 = by_number[18]
    assert m18["host"] == "github" and m18["repo"] == "mission-control"
    assert m18["branch"] == "loop/wall-web-timeline"
    assert m18["program"] == "mr1-prog" and m18["row_id"] == "W2-L2"
    assert m18["session"] == "sess_aaaa0000"
    assert m18["state"] == "open" and m18["conflicts"] is False
    assert m18["draft"] is False and m18["merged_at"] is None
    assert m18["url"] == ("https://github.com/nexiouscaliver/mission-control"
                          "/pull/18")
    assert m18["author"] == "nexiouscaliver"
    assert isinstance(m18["created_at"], int) and m18["created_at"] > 0
    assert isinstance(m18["updated_at"], int)
    contract.assert_shape(state)


def test_mr1_gh_epochs_and_merged_state(tmp_path, monkeypatch):
    # gh MERGED: state merged, merged_at epoch set; ISO -> epoch exactness.
    repo_path = tmp_path / "gh_repo"
    repo_path.mkdir()
    repo = RepoConfig(name="mission-control", path=str(repo_path), host="github")

    def handler(argv, cwd):
        if argv[:2] == ["git", "remote"]:
            return (0, GH_REMOTE_OUT)
        if argv[:3] == ["gh", "pr", "list"]:
            return (0, json.dumps([
                gh_mr(14, "fix/unmapped-recency-order", state="MERGED",
                      mergeable="UNKNOWN", merged="2026-01-04T03:04:05Z")]))
        return (0, "[]")

    fake, _calls = _spawn(handler)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    rows = [f"| W1-L0 | W1 | L0 | {repo_path} fix/unmapped-recency-order | pr14 | d9 | n/a | done |"]
    cfg, _set = _world(tmp_path, rows, [repo])
    m = collect_state(cfg)["merges"][0]
    from datetime import datetime
    expect_merged = int(datetime.fromisoformat(
        "2026-01-04T03:04:05+00:00").timestamp())
    assert m["state"] == "merged" and m["merged_at"] == expect_merged
    assert m["conflicts"] is None, "gh mergeable UNKNOWN -> conflicts null"


def test_mr1_glab_adapter_states_and_draft(tmp_path, monkeypatch):
    # The glab path: raw API objects via `glab mr list --all -F json`; state
    # vocabulary, draft, millis ISO timestamps, web_url, author.username.
    repo_path = tmp_path / "gl_repo"
    repo_path.mkdir()
    repo = RepoConfig(name="cleo", path=str(repo_path), host="gitlab")

    def handler(argv, cwd):
        if argv[:2] == ["git", "remote"]:
            return (0, GL_REMOTE_OUT)
        if argv[:3] == ["glab", "mr", "list"]:
            assert "-R" in argv and "regenai-gitlab/regenai-digital/cleo" in argv
            return (0, json.dumps([
                gl_mr(1800, "loop/cleo-lane", state="opened", detailed="mergeable"),
                gl_mr(1801, "fix/cleo-hot", state="merged",
                      merged="2026-01-05T03:04:05.000Z"),
                gl_mr(1802, "loop/cleo-draft", state="closed", draft=True),
            ]))
        return (0, "[]")

    fake, _calls = _spawn(handler)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    rows = [f"| A1-L1 | A1 | L1 | {repo_path} loop/cleo-lane | s | b | sess_cccc0000 | launched |"]
    cfg, _set = _world(tmp_path, rows, [repo])
    merges = collect_state(cfg)["merges"]
    assert len(merges) == 3
    by_iid = {m["number"]: m for m in merges}
    open_m = by_iid[1800]
    assert open_m["host"] == "gitlab" and open_m["repo"] == "cleo"
    assert open_m["state"] == "open" and open_m["conflicts"] is False
    assert open_m["program"] == "mr1-prog" and open_m["row_id"] == "A1-L1"
    assert open_m["session"] == "sess_cccc0000"
    assert open_m["author"] == "shahilkadia"
    assert open_m["url"] == ("https://gitlab.com/regenai-gitlab/regenai-digital/"
                             "cleo/-/merge_requests/1800")
    from datetime import datetime
    assert open_m["created_at"] == int(datetime.fromisoformat(
        "2026-01-02T03:04:05.123+00:00").timestamp())
    merged_m = by_iid[1801]
    assert merged_m["state"] == "merged" and merged_m["merged_at"] is not None
    closed_m = by_iid[1802]
    assert closed_m["state"] == "closed" and closed_m["draft"] is True


def test_mr1_conflicts_derivation(tmp_path, monkeypatch):
    # EXPECT-3: gh mergeable CONFLICTING -> True; UNKNOWN/missing -> None;
    # MERGEABLE -> False. glab detailed_merge_status/merge_status vocabulary.
    gh_path = tmp_path / "gh_repo"
    gh_path.mkdir()
    gh = RepoConfig(name="mission-control", path=str(gh_path), host="github")
    gl_path = tmp_path / "gl_repo"
    gl_path.mkdir()
    gl = RepoConfig(name="cleo", path=str(gl_path), host="gitlab")

    def handler(argv, cwd):
        if argv[:2] == ["git", "remote"]:
            return (0, GH_REMOTE_OUT if cwd.startswith(str(gh_path)) else GL_REMOTE_OUT)
        if argv[:3] == ["gh", "pr", "list"]:
            payload = [
                gh_mr(1, "loop/a", mergeable="CONFLICTING"),
                gh_mr(2, "loop/b", mergeable="UNKNOWN"),
                gh_mr(3, "loop/c"),
                {"number": 4, "title": "x", "headRefName": "loop/d",
                 "isDraft": False, "createdAt": "2026-01-02T03:04:05Z",
                 "updatedAt": "2026-01-03T03:04:05Z", "mergedAt": None,
                 "state": "OPEN", "url": "u4", "author": {"login": "a"}},  # no mergeable key
            ]
            return (0, json.dumps(payload))
        if argv[:3] == ["glab", "mr", "list"]:
            payload = [
                gl_mr(10, "loop/e", detailed="unresolved_conflicts"),
                gl_mr(11, "loop/f", detailed="mergeable"),
                gl_mr(12, "loop/g", detailed="not_approved"),
                {"iid": 13, "title": "x", "source_branch": "loop/h",
                 "state": "opened", "merge_status": "cannot_be_merged",
                 "created_at": "2026-01-02T03:04:05Z",
                 "updated_at": "2026-01-03T03:04:05Z", "web_url": "u13",
                 "author": {"username": "a"}},  # legacy merge_status only
            ]
            return (0, json.dumps(payload))
        return (0, "[]")

    fake, _calls = _spawn(handler)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    rows = [
        f"| R1 | W1 | L1 | {gh_path} loop/a | s | b | n/a | launched |",
        f"| R2 | W1 | L1 | {gl_path} loop/e | s | b | n/a | launched |",
    ]
    cfg, _set = _world(tmp_path, rows, [gh, gl])
    merges = {m["number"]: m for m in collect_state(cfg)["merges"]}
    assert merges[1]["conflicts"] is True, "gh CONFLICTING -> True"
    assert merges[2]["conflicts"] is None, "gh UNKNOWN -> null"
    assert merges[3]["conflicts"] is False, "gh MERGEABLE -> False"
    assert merges[4]["conflicts"] is None, "gh mergeable key absent -> null"
    assert merges[10]["conflicts"] is True, "glab unresolved_conflicts -> True"
    assert merges[11]["conflicts"] is False, "glab mergeable -> False"
    assert merges[12]["conflicts"] is None, "glab not_approved says nothing -> null"
    assert merges[13]["conflicts"] is True, "glab legacy cannot_be_merged -> True"


def test_mr1_manifest_fallback_and_session_null(tmp_path, monkeypatch):
    # EXPECT-2 fallback: branch absent from live rows joins via the forge
    # manifest (program/row_id) with session null — NEVER guessed. A live row
    # WITHOUT a sess token joins program/row but session stays null.
    repo_path = tmp_path / "gh_repo"
    repo_path.mkdir()
    repo = RepoConfig(name="mission-control", path=str(repo_path), host="github")
    manifest = {"row_id": "W2-L1", "program": "mr1-prog",
                "branch": "loop/wall-tower-contract",
                "slug": "x", "base_sha": "d", "controller_session": "sess_ctrl"}
    fdir = tmp_path / "forge" / "mr1-prog" / "W2-L1"
    fdir.mkdir(parents=True)
    (fdir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def handler(argv, cwd):
        if argv[:2] == ["git", "remote"]:
            return (0, GH_REMOTE_OUT)
        if argv[:3] == ["gh", "pr", "list"]:
            return (0, json.dumps([
                gh_mr(16, "loop/wall-tower-contract", state="MERGED",
                      mergeable="UNKNOWN", merged="2026-01-05T03:04:05Z"),
                gh_mr(17, "loop/no-row-lane"),
                gh_mr(18, "loop/tokenless-row"),
            ]))
        return (0, "[]")

    fake, _calls = _spawn(handler)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    rows = [f"| W9 | W9 | L9 | {repo_path} loop/tokenless-row | s | b | n/a | launched |"]
    cfg, _set = _world(tmp_path, rows, [repo])
    merges = {m["number"]: m for m in collect_state(cfg)["merges"]}
    m16 = merges[16]
    assert m16["program"] == "mr1-prog" and m16["row_id"] == "W2-L1"
    assert m16["session"] is None, "manifest fallback never guesses a session"
    m17 = merges[17]
    assert m17["program"] is None and m17["row_id"] is None
    assert m17["session"] is None, "an unjoinable lane MR lists with nulls"
    m18 = merges[18]
    assert m18["row_id"] == "W9" and m18["session"] is None


def test_mr1_ttl_300_registry_cache(tmp_path, monkeypatch):
    # EXPECT: the registry TTL is 300 s and does NOT reuse the 120 s signals
    # TTL — at +150 s (signals TTL long expired) the list/probe entries are
    # still fresh (no spawn); past 300 s they re-spawn. Keys are distinct from
    # the signals namespace (no collision with mr_by_branch etc.).
    repo_path = tmp_path / "gh_repo"
    repo_path.mkdir()
    repo = RepoConfig(name="mission-control", path=str(repo_path), host="github")

    def handler(argv, cwd):
        if argv[:2] == ["git", "remote"]:
            return (0, GH_REMOTE_OUT)
        if argv[:3] == ["gh", "pr", "list"]:
            return (0, json.dumps([gh_mr(18, "loop/wall-web-timeline")]))
        return (0, "[]")

    fake, calls = _spawn(handler)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    rows = [f"| W2-L2 | W2 | L2 | {repo_path} loop/wall-web-timeline | s | b | sess_aaaa0000 | launched |"]
    cfg, set_now = _world(tmp_path, rows, [repo])

    def registry_spawns():
        # only registry-shaped argv (probe + --state list); lane-signal spawns
        # (ls-remote, by-branch --head list) run on the 120 s TTL by design
        return sum(1 for a in calls["argv"]
                   if a[:2] == ["git", "remote"]
                   or (a[:3] == ["gh", "pr", "list"] and "--state" in a))

    collect_state(cfg)
    n0 = registry_spawns()
    assert n0 >= 2, "first collect probes the remote and lists"
    set_now(NOW + 150)          # 120 s signals TTL boundary: registry still fresh
    collect_state(cfg)
    assert registry_spawns() == n0, "+150 s must NOT re-spawn the registry (TTL 300)"
    set_now(NOW + 301)          # strictly past 300 s: expired
    collect_state(cfg)
    assert registry_spawns() > n0, "+301 s re-spawns the registry list/probe"


def test_mr1_repo_set_union_note_token_and_order(tmp_path, monkeypatch):
    # Repo set = wall.json repos[] UNION live-note repo tokens: a token NOT in
    # config repos is still scanned (host from its own git remote probe). Host
    # "other" repos are skipped. Entries order newest updated_at first.
    declared_path = tmp_path / "declared_repo"
    declared_path.mkdir()
    declared = RepoConfig(name="mission-control", path=str(declared_path), host="github")
    other_path = tmp_path / "other_repo"
    other_path.mkdir()
    other = RepoConfig(name="other-repo", path=str(other_path), host="other")
    token_path = tmp_path / "token_repo"       # only in a note row
    token_path.mkdir()

    def handler(argv, cwd):
        if argv[:2] == ["git", "remote"]:
            if cwd.startswith(str(declared_path)):
                return (0, GH_REMOTE_OUT)
            if cwd.startswith(str(token_path)):
                return (0, GL_REMOTE_OUT)
            return (1, "")
        if argv[:3] == ["gh", "pr", "list"]:
            return (0, json.dumps([
                gh_mr(5, "loop/a", updated="2026-02-01T00:00:00Z"),
                gh_mr(6, "loop/b", updated="2026-03-01T00:00:00Z"),
            ]))
        if argv[:3] == ["glab", "mr", "list"]:
            return (0, json.dumps([
                gl_mr(70, "fix/c", updated="2026-04-01T00:00:00.000Z"),
            ]))
        return (0, "[]")

    fake, calls = _spawn(handler)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    rows = [f"| T1 | W1 | L1 | {token_path} fix/c | s | b | n/a | launched |"]
    cfg, _set = _world(tmp_path, rows, [declared, other])
    merges = collect_state(cfg)["merges"]
    repos_scanned = {m["repo"] for m in merges}
    assert repos_scanned == {"mission-control", "cleo"}, \
        "declared + note-token repos scan; host-other repo is skipped"
    numbers = [m["number"] for m in merges]
    assert numbers == [70, 6, 5], "newest updated_at first"
    gl_entry = merges[0]
    assert gl_entry["repo"] == "cleo" and gl_entry["host"] == "gitlab"
    listed = [a for a in calls["argv"] if a[:3] == ["glab", "mr", "list"]]
    assert len(listed) == 1, "ONE list call per repo"


def test_mr1_silent_on_scan_failure(tmp_path, monkeypatch):
    # A failing list call degrades SILENTLY: merges stays [] and no degraded
    # line appears (the registry is a best-effort read; lane-signal entries
    # own the network-degradation vocabulary).
    repo_path = tmp_path / "gh_repo"
    repo_path.mkdir()
    repo = RepoConfig(name="mission-control", path=str(repo_path), host="github")

    def handler(argv, cwd):
        if argv[:2] == ["git", "remote"]:
            return (0, GH_REMOTE_OUT)
        if argv[:2] == ["git", "ls-remote"]:
            return (0, "")  # lane pushed signal: rc-0 miss (a VALUE, no entry)
        if argv[:3] == ["gh", "pr", "list"] and "--state" in argv:
            return (1, "")  # the REGISTRY list call fails
        return (0, "[]")   # the by-branch lane-signal lookup: valid empty

    fake, _calls = _spawn(handler)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    rows = [f"| W2-L2 | W2 | L2 | {repo_path} loop/x | s | b | n/a | launched |"]
    cfg, _set = _world(tmp_path, rows, [repo])
    state = collect_state(cfg)
    assert state["merges"] == []
    assert state["server"]["degraded"] == []
    contract.assert_shape(state)


def test_mr1_live_row_wins_over_manifest(tmp_path, monkeypatch):
    # Live note rows take PRIORITY over forge manifests for the same branch
    # (the manifest is only the persistent fallback).
    repo_path = tmp_path / "gh_repo"
    repo_path.mkdir()
    repo = RepoConfig(name="mission-control", path=str(repo_path), host="github")
    manifest = {"row_id": "WRONG-L9", "program": "ghost-prog",
                "branch": "loop/wall-web-timeline"}
    fdir = tmp_path / "forge" / "mr1-prog" / "W2-L2"
    fdir.mkdir(parents=True)
    (fdir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def handler(argv, cwd):
        if argv[:2] == ["git", "remote"]:
            return (0, GH_REMOTE_OUT)
        if argv[:3] == ["gh", "pr", "list"]:
            return (0, json.dumps([gh_mr(18, "loop/wall-web-timeline")]))
        return (0, "[]")

    fake, _calls = _spawn(handler)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    rows = [f"| W2-L2 | W2 | L2 | {repo_path} loop/wall-web-timeline | s | b | sess_aaaa0000 | launched |"]
    cfg, _set = _world(tmp_path, rows, [repo])
    m = collect_state(cfg)["merges"][0]
    assert m["program"] == "mr1-prog" and m["row_id"] == "W2-L2"
    assert m["session"] == "sess_aaaa0000"
