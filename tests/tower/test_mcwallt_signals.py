"""T-5 signal tests: AC-SIG-1..3, AC-FAIL-6/7.

Every collect-level case drives ``collect_state`` over a fixture world (temp
db + temp note + temp repo dirs) with ``mc_wall.tower.netcache._run_cmd``
monkeypatched via ``mcwallt_fake_cmd`` — ZERO real subprocess/git/glab/gh
execution, zero network. Normalization units call ``signals.adapt_cli_mr`` /
``normalize_mr`` directly. The CLI argv shapes are recorded in
``netcache.py``'s docstring (verified 2026-09-19 against the installed glab
1.67.0 / gh 2.78.0 help texts); tests key on argv[0] + subcommand, so flag
changes never break the suite.
"""

import json
from datetime import datetime

from mc_wall.tower import (NetCache, NetworkSettings, ProgramConfig, RepoConfig,
                           TowerConfig, collect_state, contract)
from mc_wall.tower import netcache as netcache_module
from tests.tower.conftest import (mcwallt_fake_cmd, mcwallt_make_note,
                                  mcwallt_make_session_db, mcwallt_settable_clock)

# signals is imported INSIDE the tests that touch it directly: collect-level
# cases go through collect_state, so a missing module surfaces as per-test
# failures (a real red), never a collection-interrupting module import.

NOW = 2_000_000_050.0
HEADER_A_LINE = "| id | wave | lane | repo/branch | slug | base | session/MR artifacts | status |"
SEP_LINE = "|---|---|---|---|---|---|---|---|"


def _world(tmp_path, rows, repos, name="mcwallt_signals"):
    """collect_state world: one program whose variant-A note carries ``rows``
    and the given repos; settable NOW clock; fresh NetCache per config.
    Returns (config, set_now)."""
    note = mcwallt_make_note(tmp_path, name + "_note.md", [HEADER_A_LINE, SEP_LINE, *rows])
    now_s, set_now = mcwallt_settable_clock(NOW)
    cfg = TowerConfig(
        db_path=mcwallt_make_session_db(tmp_path, name=name + ".db"),
        programs=(ProgramConfig(program="mcwallt-prog", tag="t", note_glob=note),),
        repos=tuple(repos),
        now_s=now_s,
        network_cache=NetCache())
    return cfg, set_now


def test_mcwallt_pushed_values(tmp_path, monkeypatch):
    # AC-SIG-1: hit -> true; rc-0 miss -> false (a VALUE, not degradation);
    # branch None -> null; unconfigured repo -> nulls; no degraded entries.
    repo_path = tmp_path / "mcwallt_repo"
    repo_path.mkdir()
    repo = RepoConfig(name="mcwallt-repo", path=str(repo_path), host="gitlab")

    def handler(argv, cwd):
        if argv[:2] == ["git", "ls-remote"] and argv[3] == "loop/mcwallt-hit":
            return (0, "sha000\trefs/heads/loop/mcwallt-hit\n")
        if argv[:2] == ["git", "ls-remote"] and argv[3] == "main":
            return (0, "sha000\trefs/heads/other-branch\n")  # rc 0, no matching ref
        return (0, "[]")  # glab/gh: empty open list — valid empty, no entry

    fake, _calls = mcwallt_fake_cmd(handler)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    rows = [
        f"| W1-L1 | W1 | L0 | {repo_path} loop/mcwallt-hit | n/a | n/a | n/a | launched |",
        f"| W1-L2 | W1 | L0 | {repo_path} main | n/a | n/a | n/a | launched |",
        f"| W1-L3 | W1 | L0 | {repo_path} | n/a | n/a | n/a | launched |",
        "| W1-L4 | W1 | L0 | plugin cache 1.3.0 (plain lane) | n/a | n/a | n/a | forged |",
    ]
    cfg, _set = _world(tmp_path, rows, [repo])
    state = collect_state(cfg)
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    assert lanes["W1-L1"]["signals"]["pushed"] == {"value": True, "age_s": 0}
    assert lanes["W1-L2"]["signals"]["pushed"] == {"value": False, "age_s": 0}
    assert lanes["W1-L3"]["signals"]["pushed"] is None    # no branch -> no lookup
    assert lanes["W1-L4"]["signals"] == {"pushed": None, "mr": None}  # unconfigured
    assert state["server"]["degraded"] == []
    contract.assert_shape(state)


def test_mcwallt_pushed_age(tmp_path, monkeypatch):
    # AC-SIG-2: the output object exposes EXACTLY {"value","age_s"} (no sha);
    # age_s = now - the NetCache ENTRY's observation timestamp — 0 on a fresh
    # fetch, 40 after a 40 s clock advance within TTL, one spawn total. The
    # ls-remote stdout carries only sha+ref: no local object-store derivation
    # exists anywhere on this path.
    repo_path = tmp_path / "mcwallt_repo"
    repo_path.mkdir()
    repo = RepoConfig(name="mcwallt-repo", path=str(repo_path), host="gitlab")

    def handler(argv, cwd):
        if argv[:2] == ["git", "ls-remote"]:
            return (0, "sha000\trefs/heads/loop/mcwallt-hit\n")
        return (0, "[]")

    fake, calls = mcwallt_fake_cmd(handler)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    rows = [f"| W1-L1 | W1 | L0 | {repo_path} loop/mcwallt-hit | n/a | n/a | n/a | launched |"]
    cfg, set_now = _world(tmp_path, rows, [repo])
    state = collect_state(cfg)
    pushed = state["programs"][0]["lanes"][0]["signals"]["pushed"]
    assert set(pushed.keys()) == {"value", "age_s"}   # EXACTLY these keys
    assert pushed == {"value": True, "age_s": 0}      # fresh fetch: age 0
    set_now(NOW + 40.0)                               # within the 120 s TTL
    state2 = collect_state(cfg)                       # same config -> shared cache
    assert state2["programs"][0]["lanes"][0]["signals"]["pushed"] == \
        {"value": True, "age_s": 40}                  # now - observation ts
    assert [a for a in calls["argv"] if a[0] == "git"] == \
        [["git", "ls-remote", "origin", "loop/mcwallt-hit"]]  # ONE spawn, reused


def test_mcwallt_mr_normalize_and_precedence(tmp_path, monkeypatch):
    # AC-SIG-3: normalization maps, age clamping, host refs; artifacts ref
    # beats by-branch; by-branch newest created_at, tie -> higher iid.
    from mc_wall.tower import signals

    created_iso = "2026-09-19T10:00:00Z"
    created_ts = datetime.fromisoformat(created_iso.replace("Z", "+00:00")).timestamp()

    # --- units: adapt + normalize (gitlab view JSON) ---
    gl = {"iid": 7, "state": "opened", "title": "T", "created_at": created_iso,
          "head_pipeline": {"status": "success"}}
    mr = signals.normalize_mr(signals.adapt_cli_mr(gl, "gitlab"), "gitlab", NOW)
    assert mr == {"ref": "!7", "repo_host": "gitlab", "state": "open", "title": "T",
                  "pipeline": "green", "age_s": int(NOW - created_ts)}
    # Pipeline fallback key (head_pipeline absent -> pipeline.status).
    gl_fb = dict(gl, head_pipeline=None, pipeline={"status": "failed"})
    assert signals.normalize_mr(signals.adapt_cli_mr(gl_fb, "gitlab"),
                                 "gitlab", NOW)["pipeline"] == "failed"
    for raw_state, want in (("merged", "merged"), ("closed", "closed"), ("locked", "")):
        adapted = signals.adapt_cli_mr(dict(gl, state=raw_state), "gitlab")
        assert signals.normalize_mr(adapted, "gitlab", NOW)["state"] == want
    for raw_pipe, want in (("passed", "green"), ("running", "running"),
                           ("pending", "running"), ("weird", "")):
        adapted = signals.adapt_cli_mr(dict(gl, head_pipeline={"status": raw_pipe}), "gitlab")
        assert signals.normalize_mr(adapted, "gitlab", NOW)["pipeline"] == want
    # Unparseable created_at -> age 0; a future created_at never goes negative
    # (NOW = 2e9 epoch is 2033-05, so 2040 is genuinely in the future).
    for bad in ("not-a-date", "2040-01-01T00:00:00Z"):
        adapted = signals.adapt_cli_mr(dict(gl, created_at=bad), "gitlab")
        assert signals.normalize_mr(adapted, "gitlab", NOW)["age_s"] == 0
    # github: number/state(OPEN)/createdAt keys, "#" refs; pipeline "" in v1
    # (statusCheckRollup deferral, recorded in netcache.py's docstring).
    gh = {"number": 7, "state": "OPEN", "title": "GH", "createdAt": created_iso}
    m = signals.normalize_mr(signals.adapt_cli_mr(gh, "github"), "github", NOW)
    assert (m["ref"], m["repo_host"], m["state"], m["pipeline"]) == \
        ("#7", "github", "open", "")

    # --- precedence + by-branch selection through collect_state ---
    repo_path = tmp_path / "mcwallt_repo"
    repo_path.mkdir()
    gh_repo_path = tmp_path / "mcwallt_gh_repo"
    gh_repo_path.mkdir()
    gl_repo = RepoConfig(name="mcwallt-gl", path=str(repo_path), host="gitlab")
    gh_repo = RepoConfig(name="mcwallt-gh", path=str(gh_repo_path), host="github")

    gl_5 = {"iid": 5, "state": "opened", "title": "artifacts-ref MR",
            "created_at": "2026-09-19T09:00:00Z", "head_pipeline": {"status": "success"}}
    gl_9 = {"iid": 9, "state": "opened", "title": "by-branch MR",
            "created_at": "2026-09-19T09:30:00Z"}
    gl_3 = {"iid": 3, "state": "opened", "title": "newest by-branch MR",
            "created_at": "2026-09-19T10:30:00Z"}
    gl_4 = {"iid": 4, "state": "opened", "title": "tie loser",
            "created_at": "2026-09-19T08:00:00Z"}
    gl_11 = {"iid": 11, "state": "opened", "title": "tie winner",
             "created_at": "2026-09-19T08:00:00Z"}
    gh_5 = {"number": 5, "state": "OPEN", "title": "github PR",
            "createdAt": "2026-09-19T10:00:00Z"}

    def handler(argv, cwd):
        if argv[0] == "glab" and argv[2] == "view":
            return (0, json.dumps(gl_5))
        if argv[0] == "glab" and argv[2] == "list":
            per_branch = {"loop/mcwallt-newest": [gl_9, gl_3],
                          "loop/mcwallt-tie": [gl_4, gl_11]}
            return (0, json.dumps(per_branch.get(argv[4], [gl_9])))
        if argv[0] == "gh" and argv[2] == "view":
            return (0, json.dumps(gh_5))
        return (0, "[]")

    fake, calls = mcwallt_fake_cmd(handler)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    rows = [
        # artifacts !5 beats the by-branch match for the same repo:
        f"| W1-L1 | W1 | L0 | {repo_path} loop/mcwallt-ref | n/a | n/a | !5 | launched |",
        # by-branch, newest created_at wins (gl_3 newer than gl_9):
        f"| W1-L2 | W1 | L0 | {repo_path} loop/mcwallt-newest | n/a | n/a | n/a | launched |",
        # by-branch tie on created_at -> higher iid (gl_11):
        f"| W1-L3 | W1 | L0 | {repo_path} loop/mcwallt-tie | n/a | n/a | n/a | launched |",
        # a "#5" artifacts token on a GITLAB repo is ignored -> by-branch (gl_9):
        f"| W1-L4 | W1 | L0 | {repo_path} loop/mcwallt-other | n/a | n/a | #5 | launched |",
        # "#5" on a GITHUB repo resolves by ref:
        f"| W1-L5 | W1 | L0 | {gh_repo_path} main | n/a | n/a | #5 | launched |",
    ]
    cfg, _set = _world(tmp_path, rows, [gl_repo, gh_repo])
    state = collect_state(cfg)
    mrs = {l["row_id"]: l["signals"]["mr"] for l in state["programs"][0]["lanes"]}
    assert mrs["W1-L1"]["ref"] == "!5"            # artifacts ref wins
    assert mrs["W1-L1"]["title"] == "artifacts-ref MR"
    assert mrs["W1-L1"]["state"] == "open"
    assert mrs["W1-L2"]["ref"] == "!3"            # by-branch newest created_at
    assert mrs["W1-L3"]["ref"] == "!11"           # created_at tie -> higher iid
    assert mrs["W1-L4"]["ref"] == "!9"            # host-mismatched token ignored
    assert mrs["W1-L5"]["ref"] == "#5"            # github artifacts ref
    assert mrs["W1-L5"]["state"] == "open"        # gh "OPEN" casefolds in
    assert state["server"]["degraded"] == []
    contract.assert_shape(state)
    # Precedence pinned by absence: the !5 lane never ran a by-branch list.
    list_argv = [a for a in calls["argv"] if a[:3] == ["glab", "mr", "list"]]
    assert sorted(a[4] for a in list_argv) == \
        ["loop/mcwallt-newest", "loop/mcwallt-other", "loop/mcwallt-tie"]


def test_mcwallt_failopen_git_error(tmp_path, monkeypatch):
    # AC-FAIL-6: git rc != 0 -> pushed null + entry 5; an in-backoff second
    # collect re-emits the entry (fresh log every collect) without a new spawn.
    repo_path = tmp_path / "mcwallt_repo"
    repo_path.mkdir()
    repo = RepoConfig(name="mcwallt-repo", path=str(repo_path), host="gitlab")

    def handler(argv, cwd):
        if argv[0] == "git":
            return (1, "")
        return (0, "[]")

    fake, calls = mcwallt_fake_cmd(handler)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    rows = [f"| W1-L1 | W1 | L0 | {repo_path} loop/mcwallt-b1 | n/a | n/a | n/a | launched |"]
    cfg, set_now = _world(tmp_path, rows, [repo])
    state = collect_state(cfg)
    lane = state["programs"][0]["lanes"][0]
    assert lane["signals"]["pushed"] is None           # rc != 0 -> null + entry 5
    assert lane["signals"]["mr"] is None               # "[]" list: valid empty
    assert state["server"]["degraded"] == ["network degraded: git mcwallt-repo"]
    contract.assert_shape(state)

    git_before = [a for a in calls["argv"] if a[0] == "git"]
    set_now(NOW + 10.0)                                # inside the 30 s backoff
    state2 = collect_state(cfg)
    assert state2["programs"][0]["lanes"][0]["signals"]["pushed"] is None
    assert state2["server"]["degraded"] == \
        ["network degraded: git mcwallt-repo"]         # fresh entry every collect
    assert [a for a in calls["argv"] if a[0] == "git"] == git_before  # no new spawn

    # Plan F1 at the collect seam: a RAISING _run_cmd degrades per key — the
    # document survives with entries 5/6 and the backstop stays unreached.
    def raising(argv, cwd, timeout_s=10.0):
        raise RuntimeError("mcwallt net down")

    monkeypatch.setattr(netcache_module, "_run_cmd", raising)
    cfg3, _set3 = _world(tmp_path, rows, [repo], name="mcwallt_signals_f1")
    state3 = collect_state(cfg3)
    assert state3["programs"][0]["lanes"][0]["signals"]["pushed"] is None
    assert state3["server"]["degraded"] == ["network degraded: git mcwallt-repo",
                                            "network degraded: mr mcwallt-repo"]
    assert "internal error" not in " ".join(state3["server"]["degraded"])
    contract.assert_shape(state3)


def test_mcwallt_failopen_mr_error(tmp_path, monkeypatch):
    # AC-FAIL-7: glab rc != 0 -> mr null + entry 6; branch None / unconfigured
    # repo -> mr null with NO entry; rc-0 empty list is a valid empty result.
    repo_path = tmp_path / "mcwallt_repo"
    repo_path.mkdir()
    repo = RepoConfig(name="mcwallt-repo", path=str(repo_path), host="gitlab")
    mode = {"rc": 1}

    def handler(argv, cwd):
        if argv[0] == "git":
            return (0, "sha000\trefs/heads/loop/mcwallt-b1\n")
        if argv[0] == "glab":
            return (mode["rc"], "[]" if mode["rc"] == 0 else "")
        return (0, "[]")

    fake, _calls = mcwallt_fake_cmd(handler)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    rows = [
        f"| W1-L1 | W1 | L0 | {repo_path} loop/mcwallt-b1 | n/a | n/a | n/a | launched |",
        f"| W1-L2 | W1 | L0 | {repo_path} | n/a | n/a | n/a | forged |",
        "| W1-L3 | W1 | L0 | plugin cache 1.3.0 (plain lane) | n/a | n/a | n/a | forged |",
    ]
    cfg, _set = _world(tmp_path, rows, [repo])
    state = collect_state(cfg)
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    assert lanes["W1-L1"]["signals"]["pushed"] == {"value": True, "age_s": 0}  # git healthy
    assert lanes["W1-L1"]["signals"]["mr"] is None       # by-branch lookup failed
    assert lanes["W1-L2"]["signals"]["mr"] is None       # branch None: no lookup
    assert lanes["W1-L3"]["signals"]["mr"] is None       # unconfigured repo
    assert state["server"]["degraded"] == ["network degraded: mr mcwallt-repo"]
    contract.assert_shape(state)

    # rc 0 + parseable EMPTY list is a valid empty result (mr None, NO entry).
    mode["rc"] = 0
    cfg2, _set2 = _world(tmp_path, rows, [repo], name="mcwallt_signals_empty")
    state2 = collect_state(cfg2)
    assert state2["programs"][0]["lanes"][0]["signals"]["mr"] is None
    assert state2["server"]["degraded"] == []

    # Recorded v1 choice: a by-ref rc-0 NOT-FOUND (unusable stdout) is a lookup
    # FAILURE (unknown != met) — never a valid empty result.
    from mc_wall.tower import signals

    now_s, _setn = mcwallt_settable_clock(NOW)
    mr, failed = signals.lookup_mr_by_ref(NetCache(), NetworkSettings(), repo,
                                          "!404", now_s, NOW)
    assert (mr, failed) == (None, True)
