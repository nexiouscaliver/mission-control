"""T-1/T-2/T-3/T-6 collect tests: AC-API-1, AC-LAUNCH-1/2, AC-FAIL-1/4/8/9,
DegradedLog ordering, AC-RO-2, AC-DET-1/2, AC-E2E-1."""

import dataclasses
import hashlib
import json
import os
from datetime import datetime

from mc_wall.tower import NetCache, ProgramConfig, RepoConfig, TowerConfig, collect_state
from mc_wall.tower import contract
from mc_wall.tower import netcache as netcache_module
from mc_wall.tower.collect import DegradedLog
from tests.tower.conftest import (MCWALLT_HEADER_A, MCWALLT_SEP, MCWALLT_WORLD_LANE,
                                  MCWALLT_WORLD_MASTER, MCWALLT_WORLD_NOW,
                                  MCWALLT_WORLD_UNMAPPED, mcwallt_clock, mcwallt_fake_cmd,
                                  mcwallt_make_db, mcwallt_make_note,
                                  mcwallt_make_session_db, mcwallt_settable_clock,
                                  mcwallt_tag_input, mcwallt_world,
                                  mcwallt_world_default_handler)


def test_mcwallt_api_signature_and_defaults(tmp_path):
    # Defaults exist on a bare config (db_path/programs are the only required fields).
    cfg = TowerConfig(db_path="x", programs=())
    assert cfg.uptime_s_provider() == 0
    assert cfg.banner_provider() is None
    assert isinstance(cfg.network_cache, NetCache)

    cfg2 = TowerConfig(db_path="x", programs=())
    assert cfg2.network_cache is not cfg.network_cache  # fresh cache per config build

    # collect_state returns the contract-shaped document with provider passthrough.
    ccfg = TowerConfig(db_path=mcwallt_make_session_db(tmp_path), programs=(),
                       now_s=lambda: 1234.5)
    state = collect_state(ccfg)
    assert isinstance(state, dict)
    assert state["schema_version"] == 1
    assert state["server"] == {"uptime_s": 0, "generated_ts": int(ccfg.now_s()),
                               "degraded": [], "banner": None}
    assert list(state["server"].keys()) == ["uptime_s", "generated_ts", "degraded", "banner"]
    assert list(state.keys()) == ["schema_version", "server", "programs", "verify_queue",
                                  "human_actions", "sessions_unmapped", "launch_pending"]


def test_mcwallt_launch_passthrough(tmp_path):
    db_path = mcwallt_make_session_db(tmp_path)
    launch = tmp_path / "mcwallt_launch.json"
    launch.write_text('{"slug": "mcwall-tower", "n": 3}', encoding="utf-8")
    state = collect_state(TowerConfig(db_path=db_path, programs=(),
                                       pending_launch_path=str(launch)))
    assert state["launch_pending"] == {"slug": "mcwall-tower", "n": 3}

    # Non-dict values pass through verbatim too.
    launch.write_text("[1,2]", encoding="utf-8")
    state = collect_state(TowerConfig(db_path=db_path, programs=(),
                                       pending_launch_path=str(launch)))
    assert state["launch_pending"] == [1, 2]


def test_mcwallt_launch_missing_null(tmp_path):
    cfg = TowerConfig(db_path=mcwallt_make_session_db(tmp_path), programs=(),
                      pending_launch_path=str(tmp_path / "mcwallt_absent_launch.json"))
    state = collect_state(cfg)
    assert state["launch_pending"] is None
    assert state["server"]["degraded"] == []


def test_mcwallt_failopen_launch_corrupt(tmp_path):
    launch = tmp_path / "mcwallt_launch.json"
    launch.write_text("not json", encoding="utf-8")
    cfg = TowerConfig(db_path=mcwallt_make_session_db(tmp_path), programs=(),
                      pending_launch_path=str(launch))
    state = collect_state(cfg)
    assert state["launch_pending"] is None
    assert "launch state degraded: pending-launch unreadable" in state["server"]["degraded"]


def test_mcwallt_failopen_launch_binary(tmp_path):
    # Present but not decodable UTF-8: same fail-open as any unparseable payload.
    launch = tmp_path / "mcwallt_launch.bin"
    launch.write_bytes(b"\xff\xfe\x00mcwallt-not-utf8\xfd")
    cfg = TowerConfig(db_path=mcwallt_make_session_db(tmp_path), programs=(),
                      pending_launch_path=str(launch))
    state = collect_state(cfg)
    assert state["launch_pending"] is None
    assert state["server"]["degraded"] == ["launch state degraded: pending-launch unreadable"]
    contract.assert_shape(state)
    # The doc was NOT zeroed by the backstop: the marking entry is absent and
    # the rest of the document is intact.
    assert "internal error: collect_state failed" not in state["server"]["degraded"]
    assert state["server"]["uptime_s"] == 0
    assert state["programs"] == []


def test_mcwallt_failopen_notes_missing(tmp_path, monkeypatch):
    # Hermeticity: the >1-matches world below maps a lane to a configured repo
    # with a branch + an !5 artifacts ref; T-5's signals wiring would spawn
    # real git/glab subprocesses. Signals are T-5's subject (covered
    # fixture-only by test_mcwallt_signals.py); neutralize them here.
    from mc_wall.tower import collect as collect_module
    monkeypatch.setattr(collect_module, "_read_signals", lambda *args: None)
    db_path = mcwallt_make_session_db(tmp_path)

    # 0 glob matches: program row zeroed per §4.2 + entry 3 exactly.
    cfg = TowerConfig(db_path=db_path,
                      programs=(ProgramConfig(program="secfix", tag="secfix",
                                              note_glob=str(tmp_path / "mcwallt_none_*.md")),))
    state = collect_state(cfg)
    assert state["programs"][0] == {"program": "secfix", "note_path": "", "note_mtime": 0,
                                    "objective": "",
                                    "master": {"session_id": None, "title": None,
                                               "last_active_ago_s": None},
                                    "lanes": []}
    assert state["server"]["degraded"] == ["notes degraded: secfix"]

    # >1 matches: newest mtime wins (§4.2), no degradation.
    newest = tmp_path / "mcwallt_newest"
    newest.mkdir()
    header = "| id | wave | lane | repo/branch | slug | base | session/MR artifacts | status |"
    sep = "|---|---|---|---|---|---|---|---|"
    old_p = mcwallt_make_note(newest, "mcwallt_note_old.md", [
        header, sep, "| W9-L1 | W9 | L1 | n/a | mcwallt-old | n/a | sess_00000000 | done |"])
    repo_cell = str(tmp_path / "mcwallt_repo_dir")
    os.mkdir(repo_cell)  # healthy repo root: no T-4 entry 4 in this test's world
    new_p = mcwallt_make_note(newest, "mcwallt_note_new.md", [
        "objective: ship the wall", header, sep,
        f"| W9-L1 | W9 | L1 | {repo_cell} loop/mcwall-tower | mcwallt-new | n/a | merge !5; sess_00000001 | launched |"])
    os.utime(old_p, (1000.0, 1000.0))
    os.utime(new_p, (2000.0, 2000.0))
    cfg2 = TowerConfig(
        db_path=db_path,
        programs=(ProgramConfig(program="secfix", tag="secfix",
                                note_glob=str(newest / "mcwallt_note_*.md")),),
        repos=(RepoConfig(name="mcwallt-repo", path=repo_cell, host="gitlab"),),
    )
    state2 = collect_state(cfg2)
    prog = state2["programs"][0]
    assert prog["note_mtime"] == 2000
    assert prog["note_path"].endswith("mcwallt_note_new.md")
    assert prog["objective"] == "ship the wall"
    assert [l["row_id"] for l in prog["lanes"]] == ["W9-L1"]
    assert prog["lanes"][0]["slug"] == "mcwallt-new"
    assert prog["lanes"][0]["repo"] == "mcwallt-repo"  # token -> configured repo name
    assert state2["server"]["degraded"] == []

    # mtime tie: lexicographically smallest path wins.
    tie = tmp_path / "mcwallt_tie"
    tie.mkdir()
    a_p = mcwallt_make_note(tie, "mcwallt_a.md", ["objective: from a", header, sep,
                                                  "| W9-L1 | W9 | L1 | n/a | a | n/a | n/a | done |"])
    b_p = mcwallt_make_note(tie, "mcwallt_b.md", ["objective: from b", header, sep,
                                                  "| W9-L1 | W9 | L1 | n/a | b | n/a | n/a | done |"])
    os.utime(a_p, (3000.0, 3000.0))
    os.utime(b_p, (3000.0, 3000.0))
    cfg3 = TowerConfig(db_path=db_path,
                       programs=(ProgramConfig(program="secfix", tag="secfix",
                                               note_glob=str(tie / "mcwallt_*.md")),))
    assert collect_state(cfg3)["programs"][0]["objective"] == "from a"


def test_mcwallt_degraded_log_order_unit():
    log = DegradedLog()
    # Fed in shuffled order: every rank group present, plus an exact duplicate.
    feed = [
        ((6, 0, 0, ""), "launch state degraded: pending-launch unreadable"),   # entry 8
        ((3, 1, 2, "!7"), "precondition state unknown: !7"),                   # entry 11
        ((1, 0, 0, ""), "notes degraded: p0"),                                 # entry 3, prog 0
        ((4, 0, 0, "sess-b"), "join degraded: ambiguous tags sess-b"),         # entry 7
        ((3, 1, 0, ""), "network degraded: git r1"),                           # entry 5, repo 1
        ((1, 1, 0, ""), "notes degraded: p1"),                                 # entry 3, prog 1
        ((2, 0, 0, ""), "goals degraded: r0"),                                 # entry 4, repo 0
        ((5, 0, 0, "tok"), "join ambiguous session: tok"),                     # entry 9
        ((1, 0, 1, ""), "note rows skipped: 2"),                               # entry 10, prog 0
        ((3, 1, 2, "!3"), "precondition state unknown: !3"),                   # entry 11
        ((4, 0, 0, "sess-a"), "join degraded: ambiguous tags sess-a"),         # entry 7
        ((3, 1, 1, ""), "network degraded: mr r1"),                            # entry 6, repo 1
        ((0, 0, 0, ""), "tracking degraded: session store unreadable"),        # entry 1
        ((0, 0, 0, ""), "tracking degraded: session store unreadable"),        # duplicate
    ]
    for key, text in feed:
        log.add(key, text)
    assert log.emit() == [
        "tracking degraded: session store unreadable",   # db
        "notes degraded: p0",                            # prog 0, entry 3
        "note rows skipped: 2",                          # prog 0, entry 10
        "notes degraded: p1",                            # prog 1, entry 3
        "goals degraded: r0",                            # repo 0, entry 4
        "network degraded: git r1",                      # repo 1, entry 5
        "network degraded: mr r1",                       # repo 1, entry 6
        "precondition state unknown: !3",                # repo 1, entry 11 (ref asc)
        "precondition state unknown: !7",
        "join degraded: ambiguous tags sess-a",          # entry 7 (sid asc)
        "join degraded: ambiguous tags sess-b",
        "join ambiguous session: tok",                   # entry 9
        "launch state degraded: pending-launch unreadable",  # entry 8 last
    ]


def test_mcwallt_failopen_db_missing(tmp_path):
    # AC-FAIL-1 (healthy path asserted FIRST per plan F2 — these assertions
    # fail under always-null sessions, keeping this test genuinely red before
    # the T-3 reader exists).
    NOW = 2_000_000_000.0

    def ms(age):
        return int((NOW - age) * 1000)

    master_id = "sess_11111111-1111-4111-8111-111111111111"
    lane_id = "sess_9a690ab2-cde8-4e9a-bc4e-177fcc68545f"
    unmapped_id = "sess_22222222-2222-4222-8222-222222222222"
    db = mcwallt_make_db(tmp_path, sessions=[
        {"id": master_id, "title": "mcwallt master", "directory": "/mcwallt/m",
         "time_updated": ms(100), "time_created": ms(100)},
        {"id": lane_id, "title": "mcwallt lane", "directory": "/mcwallt/l",
         "time_updated": ms(200), "time_created": ms(200)},
        {"id": unmapped_id, "title": "mcwallt unmapped", "directory": "/mcwallt/u",
         "time_updated": ms(50), "time_created": ms(50)},
    ], inputs=[mcwallt_tag_input(master_id, ["secfix-master"], ms(100))])
    note = mcwallt_make_note(tmp_path, "mcwallt_note.md", [
        "| id | wave | lane | repo/branch | slug | base | session/MR artifacts | status |",
        "|---|---|---|---|---|---|---|---|",
        "| W4-L1 | W4 | L1 | n/a | n/a | n/a | sess_9a690ab2 | forged |",
    ])
    cfg = TowerConfig(db_path=db,
                      programs=(ProgramConfig(program="secfix", tag="secfix",
                                              note_glob=note,
                                              master_tag="secfix-master"),),
                      now_s=mcwallt_clock(NOW))
    # Healthy: the note-joined lane HAS its session, master populated, unmapped
    # non-empty.
    state = collect_state(cfg)
    assert state["programs"][0]["lanes"][0]["session"]["id"] == lane_id
    assert state["programs"][0]["master"]["session_id"] == master_id
    assert len(state["sessions_unmapped"]) == 1
    assert state["server"]["degraded"] == []

    # THEN move the db away and re-collect over the SAME config.
    os.rename(db, str(tmp_path / "mcwallt_moved_away.db"))
    state2 = collect_state(cfg)
    assert all(lane["session"] is None
               for prog in state2["programs"] for lane in prog["lanes"])
    assert all(prog["master"] == contract.null_master() for prog in state2["programs"])
    assert state2["sessions_unmapped"] == []
    assert state2["server"]["degraded"][0] == "tracking degraded: session store unreadable"
    contract.assert_shape(state2)  # doc intact, no exception


# --- T-6: derivations end-to-end, invariants, determinism -------------------

def _mcwallt_tree_snapshot(root):
    """AC-RO-2 snapshot: every file (relpath, size, mtime_ns, sha256) plus every
    directory listing under root — catches stray -wal/-shm/lock files and
    touched mtimes alike."""
    snap = {}
    for dirpath, dirnames, filenames in os.walk(root):
        snap["d:" + os.path.relpath(dirpath, root)] = sorted(dirnames)
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            st = os.stat(p)
            with open(p, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
            snap["f:" + os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns, digest)
    return snap


def test_mcwallt_collect_writes_nothing(tmp_path, monkeypatch):
    # AC-RO-2 (expected-green invariant pin): over the FULL fixture world, a
    # collect leaves every fixture tree byte- and stat-identical.
    cfg, _set = mcwallt_world(tmp_path, monkeypatch)
    before = _mcwallt_tree_snapshot(tmp_path)
    collect_state(cfg)
    assert _mcwallt_tree_snapshot(tmp_path) == before


def test_mcwallt_failopen_all_sources_dead(tmp_path, monkeypatch):
    # AC-FAIL-9: db gone + notes gone + repo gone + pending-launch corrupt +
    # a RAISING _run_cmd at the seam — no exception escapes, the document
    # survives with the expected degraded set, assert_shape passes, and it is
    # NOT the backstop zero document. The network entries 5/6 are structurally
    # unreachable here (lane-driven lookups need a lane, and the note kill
    # removed them all); the raising-seam degradation is pinned by
    # test_mcwallt_failopen_git_error instead.
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, include=("net",))

    def raising(argv, cwd, timeout_s=10.0):
        raise RuntimeError("mcwallt everything down")

    monkeypatch.setattr(netcache_module, "_run_cmd", raising)
    with open(cfg.pending_launch_path, "wb") as fh:
        fh.write(b"not-json{")
    state = collect_state(cfg)
    contract.assert_shape(state)
    assert state["server"]["degraded"] == [
        "tracking degraded: session store unreadable",        # 1: db gone
        "notes degraded: secfix",                             # 3: glob empty
        "goals degraded: mcwallt-repo",                       # 4: repo path gone
        "launch state degraded: pending-launch unreadable",   # 8: corrupt launch
    ]
    # NOT zeroed by the backstop: real generated_ts, no internal-error entry.
    assert state["server"]["generated_ts"] == int(MCWALLT_WORLD_NOW)
    assert all("internal error" not in e for e in state["server"]["degraded"])
    assert state["programs"] == [{"program": "secfix", "note_path": "", "note_mtime": 0,
                                  "objective": "",
                                  "master": {"session_id": None, "title": None,
                                             "last_active_ago_s": None},
                                  "lanes": []}]
    assert state["launch_pending"] is None
    assert state["verify_queue"] == []
    assert state["human_actions"] == []
    assert state["sessions_unmapped"] == []


def test_mcwallt_deterministic_identical_json(tmp_path, monkeypatch):
    # AC-DET-1 (expected-green invariant pin): same config fields + fixed
    # clock; a FRESH NetCache (per-config cache) yields byte-identical JSON —
    # no sort_keys, so key ORDER is pinned too — and re-collecting the same
    # config (shared cache) is equally identical.
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, lane1_age_s=25200.0)
    cfg2 = dataclasses.replace(cfg, network_cache=NetCache())
    a = collect_state(cfg)
    assert json.dumps(a) == json.dumps(collect_state(cfg2))
    assert json.dumps(a) == json.dumps(collect_state(cfg))
    contract.assert_shape(a)


def test_mcwallt_degraded_order_dedup(tmp_path, monkeypatch):
    # AC-DET-2: one world producing every reachable entry kind, emitted in the
    # exact §4 order: program 3/10 groups (repo-config order interleaves at 4),
    # 4, per-repo 5/6 with 11 sorted by ref, 7 by sid, 9 by token, 8 last.
    # Programs p0/p1 each skip exactly one row (equal N) -> the identical
    # entry-10 text collapses to ONE line (dedup keep-first).
    NOW = MCWALLT_WORLD_NOW

    def ms(age):
        return int((NOW - age) * 1000)

    amb1 = "sess_33333333-3333-4333-8333-333333333333"
    amb2 = "sess_44444444-4444-4444-8444-444444444444"
    tok_a, tok_b = "sess_a0000001", "sess_b0000000"
    sessions = [
        {"id": amb1, "title": "amb1", "directory": "/mcwallt/amb1",
         "time_updated": ms(70), "time_created": ms(70)},
        {"id": amb2, "title": "amb2", "directory": "/mcwallt/amb2",
         "time_updated": ms(70), "time_created": ms(70)},
        {"id": tok_a + "-1111-4111-8111-111111111111", "title": "ta1",
         "directory": "/mcwallt/ta1", "time_updated": ms(70), "time_created": ms(70)},
        {"id": tok_a + "-2222-4222-8222-222222222222", "title": "ta2",
         "directory": "/mcwallt/ta2", "time_updated": ms(70), "time_created": ms(70)},
        {"id": tok_b + "-1111-4111-8111-111111111111", "title": "tb1",
         "directory": "/mcwallt/tb1", "time_updated": ms(70), "time_created": ms(70)},
        {"id": tok_b + "-2222-4222-8222-222222222222", "title": "tb2",
         "directory": "/mcwallt/tb2", "time_updated": ms(70), "time_created": ms(70)},
    ]
    inputs = [mcwallt_tag_input(amb1, ["mcwallt-amb", "mcwallt-two"], ms(60)),
              mcwallt_tag_input(amb2, ["mcwallt-amb", "mcwallt-two"], ms(60))]

    r0 = tmp_path / "mcwallt_det2_r0"
    gd = r0 / "regenloop" / "local" / "orchestrator" / "goals" / "mcwallt-det2b"
    gd.mkdir(parents=True)
    (gd / "prompt.md").write_text("mcwallt prompt\n", encoding="utf-8")
    (gd / "manifest.json").write_text(json.dumps(
        {"stall_t_hours": 0, "precondition_mrs": ["!3", "!7"]}), encoding="utf-8")

    p0_note = mcwallt_make_note(tmp_path, "mcwallt_det2_p0.md", [
        MCWALLT_HEADER_A, MCWALLT_SEP,
        "| short row |",                                            # skipped (p0)
        f"| W1-LA | W1 | L1 | {r0} loop/mcwallt-det2a | mcwallt-slug-a | n/a | n/a | launched |",
        f"| W1-LB | W1 | L2 | {r0} | mcwallt-det2b | n/a | !5 | launched |",
        f"| W1-T2 | W1 | L3 | n/a | n/a | n/a | {tok_b} | forged |",  # reverse order on
        f"| W1-T1 | W1 | L3 | n/a | n/a | n/a | {tok_a} | forged |",  # purpose (sort pin)
    ])
    p1_note = mcwallt_make_note(tmp_path, "mcwallt_det2_p1.md", [
        MCWALLT_HEADER_A, MCWALLT_SEP,
        "| tiny row |",                                             # skipped (p1): SAME text
        "| W1-L9 | W1 | L9 | n/a | n/a | n/a | n/a | launched |",
    ])

    def handler(argv, cwd):
        if argv[0] == "git":
            return (1, "")                       # entry 5 (r0, lane A branch)
        if argv[0] == "glab" and argv[2] == "view" and argv[3] == "5":
            return (0, json.dumps({"iid": 5, "state": "opened", "title": "mcwallt det2 MR",
                                   "created_at": "2026-09-19T09:00:00Z",
                                   "head_pipeline": {"status": "success"}}))
        if argv[0] == "glab" and argv[2] == "view":
            return (1, "")                       # !3/!7 precondition unknown
        return (1, "")                           # by-branch list fails: entry 6

    fake, _calls = mcwallt_fake_cmd(handler)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    launch = tmp_path / "mcwallt_det2_launch.json"
    launch.write_text("not json", encoding="utf-8")
    cfg = TowerConfig(
        db_path=mcwallt_make_db(tmp_path, name="mcwallt_det2.db",
                                sessions=sessions, inputs=inputs),
        programs=(ProgramConfig(program="mcwallt-p0", tag="t0", note_glob=p0_note),
                  ProgramConfig(program="mcwallt-p1", tag="t1", note_glob=p1_note),
                  ProgramConfig(program="mcwallt-p2", tag="t2",
                                note_glob=str(tmp_path / "mcwallt_det2_p2_none_*.md"))),
        repos=(RepoConfig(name="mcwallt-rmiss", path=str(tmp_path / "mcwallt_det2_missing"),
                          host="gitlab"),
               RepoConfig(name="mcwallt-r0", path=str(r0), host="gitlab")),
        pending_launch_path=str(launch),
        now_s=mcwallt_settable_clock(NOW)[0])
    state = collect_state(cfg)
    assert state["server"]["degraded"] == [
        "note rows skipped: 1",                                # 10: p0 (p1's equal text deduped)
        "notes degraded: mcwallt-p2",                          # 3: p2
        "goals degraded: mcwallt-rmiss",                       # 4: repo 0
        "network degraded: git mcwallt-r0",                    # 5: repo 1
        "network degraded: mr mcwallt-r0",                     # 6: repo 1
        "precondition state unknown: !3",                      # 11: repo 1, ref asc
        "precondition state unknown: !7",
        f"join degraded: ambiguous tags {amb1}",               # 7: sid asc
        f"join degraded: ambiguous tags {amb2}",
        f"join ambiguous session: {tok_a}",                    # 9: token asc
        f"join ambiguous session: {tok_b}",
        "launch state degraded: pending-launch unreadable",    # 8: last
    ]
    # The precondition-unknown lane keeps its merge row with ready=False.
    assert state["human_actions"] == [{"kind": "merge", "ref": "!5", "repo": "mcwallt-r0",
                                       "repo_host": "gitlab", "title": "mcwallt det2 MR",
                                       "pipeline": "green", "ready": False}]
    contract.assert_shape(state)


def test_mcwallt_e2e_full_fixture_world(tmp_path, monkeypatch):
    # AC-E2E-1: the consumer path — collect_state over the complete fixture
    # world, asserting the JOINED document (not per-source internals).
    cfg, _set = mcwallt_world(tmp_path, monkeypatch)
    state = collect_state(cfg)
    contract.assert_shape(state)

    prog = state["programs"][0]
    assert prog["program"] == "secfix"
    assert prog["objective"] == "mcwallt e2e objective"
    assert prog["master"] == {"session_id": MCWALLT_WORLD_MASTER, "title": "mcwallt master",
                              "last_active_ago_s": 100}
    lanes = {l["row_id"]: l for l in prog["lanes"]}

    # The done lane is fully joined end-to-end.
    l1 = lanes["W1-L1"]
    assert l1["repo"] == "mcwallt-repo"
    assert l1["session"] == {"id": MCWALLT_WORLD_LANE, "title": "mcwallt lane",
                             "title_pending": False, "dir": "/mcwallt/l",
                             "last_active_ago_s": 400, "parent_session_id": None}
    assert l1["goal"] == {"state": "active", "queue_tail": "q-last",
                          "budget": {"slug": "mcwallt-slug", "whole_run": 3,
                                     "gates": ["python-test"]}}
    gd = str(tmp_path / "mcwallt_world_repo" / "regenloop" / "local" / "orchestrator"
             / "goals" / "mcwallt-slug")
    assert l1["manifest"] == {"path": gd, "prompt_md": gd + "/prompt.md",
                              "goal_md": gd + "/goal.md",
                              "precondition_mrs": ["!7", "!8"], "stall_t_hours": 6}
    assert l1["signals"]["pushed"] == {"value": True, "age_s": 0}
    created = datetime.fromisoformat("2026-09-19T09:00:00+00:00").timestamp()
    assert l1["signals"]["mr"] == {"ref": "!5", "repo_host": "gitlab", "state": "open",
                                   "title": "mcwallt MR five", "pipeline": "green",
                                   "age_s": int(MCWALLT_WORLD_NOW - created)}
    assert l1["suggest_verify"] == {"because": ["status=done", "finished_ago_s=400"]}
    assert l1["stalled"] is None

    # The unconfigured-repo lane and the no-token done lane keep their nulls.
    assert lanes["W1-L2"]["repo"] is None
    assert lanes["W1-L2"]["signals"] == {"pushed": None, "mr": None}
    l3 = lanes["W1-L3"]
    assert l3["session"] is None
    assert l3["goal"]["state"] == "absent"
    assert l3["manifest"] is None
    assert l3["signals"]["pushed"] == {"value": False, "age_s": 0}  # rc-0 miss

    # Document-level derivations.
    assert state["verify_queue"] == [
        {"row_id": "W1-L1", "program": "secfix", "finished_ago_s": 400,
         "master_hint": MCWALLT_WORLD_MASTER,
         "verify_cmd": f"/mission-control-verify {MCWALLT_WORLD_LANE}"},
        {"row_id": "W1-L3", "program": "secfix", "finished_ago_s": 0,
         "master_hint": MCWALLT_WORLD_MASTER, "verify_cmd": ""}]
    merge_row = {"kind": "merge", "ref": "!5", "repo": "mcwallt-repo",
                 "repo_host": "gitlab", "title": "mcwallt MR five", "pipeline": "green"}
    assert state["human_actions"] == [dict(merge_row, ready=False)]  # !8 unknown
    assert state["server"]["degraded"] == ["precondition state unknown: !8"]

    # Unmapped enumeration excludes the tag-mapped master, the token-joined
    # lane session, and the subagent child.
    assert state["sessions_unmapped"] == [
        {"id": MCWALLT_WORLD_UNMAPPED, "title": "mcwallt unmapped",
         "dir": "/mcwallt/u", "last_active_ago_s": 50, "parent_session_id": None}]
    assert state["launch_pending"] == {"slug": "mcwall-tower", "n": 3}

    # The ready variant: same world, fresh cache, !8 now resolvable (merged)
    # -> the identical row flips to ready=True and the degraded list empties.
    def handler(argv, cwd):
        if argv[0] == "glab" and argv[2] == "view" and argv[3] == "8":
            return (0, json.dumps({"iid": 8, "state": "merged", "title": "mcwallt MR eight",
                                   "created_at": "2026-09-19T08:30:00Z"}))
        return mcwallt_world_default_handler(argv, cwd)

    fake, _calls = mcwallt_fake_cmd(handler)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    cfg2 = dataclasses.replace(cfg, network_cache=NetCache())
    state2 = collect_state(cfg2)
    assert state2["human_actions"] == [dict(merge_row, ready=True)]
    assert state2["server"]["degraded"] == []
