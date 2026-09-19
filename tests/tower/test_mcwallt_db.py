"""T-3 zcode-db tests: AC-RO-1, AC-DB-MS, AC-FAIL-2/3, AC-JOIN-1..5,
AC-UNMAPPED-1, AC-MASTER-1/2, AC-DRIFT-1..4.

Fixture dbs are ms-magnitude (the verified live unit) unless a test proves the
per-collect unit probe with seconds-magnitude values. ZERO live-system reads:
no test touches /Users/shahil/.zcode/cli/db/db.sqlite; the live-evidence ids
below are copied verbatim from the orchestrator-held read-only query results.
"""

import sqlite3
from pathlib import Path

from mc_wall.tower import ProgramConfig, RepoConfig, TowerConfig, collect_state
from mc_wall.tower import contract
from tests.tower.conftest import (mcwallt_clock, mcwallt_make_db, mcwallt_make_note,
                                  mcwallt_tag_input)

NOW = 2_000_000_000.0


def ms_ago(age_s):
    """ms-magnitude stored timestamp for a value age_s seconds before NOW."""
    return int((NOW - age_s) * 1000)


def mcwallt_empty_note(tmp_path):
    """Header-only note: header found, zero rows, zero skips -> no degradation."""
    return mcwallt_make_note(tmp_path, "mcwallt_note.md", [
        "| id | wave | lane | repo/branch | slug | base | session/MR artifacts | status |",
        "|---|---|---|---|---|---|---|---|",
    ])


def test_mcwallt_db_readonly_uri(tmp_path, monkeypatch):
    from mc_wall.tower import zcode_db
    db = mcwallt_make_db(tmp_path)
    real_connect = sqlite3.connect
    calls = []

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(zcode_db.sqlite3, "connect", spy)
    state = collect_state(TowerConfig(db_path=db, programs=(),
                                      now_s=mcwallt_clock(NOW)))
    assert calls, "collect_state never opened the db"
    args, kwargs = calls[0]
    assert args and args[0] == f"file:{db}?mode=ro"
    assert kwargs.get("uri") is True
    assert state["server"]["degraded"] == []


def test_mcwallt_db_ms_normalization(tmp_path, monkeypatch):
    from mc_wall.tower import zcode_db
    master_id = "sess_11111111-1111-4111-8111-111111111111"
    unmapped_id = "sess_22222222-2222-4222-8222-222222222222"

    def build(scale):
        def t(age):
            return int((NOW - age) * scale)
        sessions = [
            {"id": master_id, "title": "mcwallt master", "directory": "/mcwallt/m",
             "time_updated": t(100), "time_created": t(100)},
            {"id": unmapped_id, "title": "mcwallt unmapped", "directory": "/mcwallt/u",
             "time_updated": t(50), "time_created": t(50)},
            # Below the 86400s session window in the SAME stored magnitude: a
            # seconds-literal cutoff (or any unit confusion) would surface this
            # row in one db and not the other — pinned HERE, locally.
            {"id": "sess_07070707-0707-4070-8070-070707070707", "title": "mcwallt old",
             "directory": "/mcwallt/old", "time_updated": t(200000), "time_created": t(200000)},
        ]
        inputs = [mcwallt_tag_input(master_id, ["secfix-master"], t(100))]
        return sessions, inputs

    note = mcwallt_empty_note(tmp_path)
    prog = (ProgramConfig(program="secfix", tag="secfix", note_glob=note,
                          master_tag="secfix-master"),)
    # db 1: ms magnitude (factor 1000); db 2: seconds magnitude (factor 1).
    s_ms, i_ms = build(1000)
    s_s, i_s = build(1)
    db_ms = mcwallt_make_db(tmp_path, name="mcwallt_ms.db", sessions=s_ms, inputs=i_ms)
    db_s = mcwallt_make_db(tmp_path, name="mcwallt_seconds.db", sessions=s_s, inputs=i_s)

    counts = {"n": 0}
    real_probe = zcode_db.probe_factor

    def counting_probe(cur):
        counts["n"] += 1
        return real_probe(cur)

    monkeypatch.setattr(zcode_db, "probe_factor", counting_probe)
    s1 = collect_state(TowerConfig(db_path=db_ms, programs=prog,
                                   now_s=mcwallt_clock(NOW)))
    assert counts["n"] == 1  # exactly ONE unit probe per collect
    counts["n"] = 0
    s2 = collect_state(TowerConfig(db_path=db_s, programs=prog,
                                   now_s=mcwallt_clock(NOW)))
    assert counts["n"] == 1

    expected_unmapped = [{"id": unmapped_id, "title": "mcwallt unmapped",
                          "dir": "/mcwallt/u", "last_active_ago_s": 50}]
    assert s1["sessions_unmapped"] == expected_unmapped
    assert s2["sessions_unmapped"] == expected_unmapped
    assert s1["programs"][0]["master"]["last_active_ago_s"] == 100
    assert s2["programs"][0]["master"]["last_active_ago_s"] == 100
    assert s1["server"]["degraded"] == []
    assert s2["server"]["degraded"] == []


def test_mcwallt_failopen_db_corrupt(tmp_path):
    bad = tmp_path / "mcwallt_corrupt.db"
    bad.write_bytes(b"garbage bytes not a db")
    note = mcwallt_make_note(tmp_path, "mcwallt_note.md", [
        "| id | wave | lane | repo/branch | slug | base | session/MR artifacts | status |",
        "|---|---|---|---|---|---|---|---|",
        "| W1-L1 | W1 | L1 | n/a | n/a | n/a | sess_00000000 | forged |",
    ])
    cfg = TowerConfig(db_path=str(bad),
                      programs=(ProgramConfig(program="secfix", tag="secfix",
                                              note_glob=note),),
                      now_s=mcwallt_clock(NOW))
    state = collect_state(cfg)
    assert state["server"]["degraded"] == ["tracking degraded: session store unreadable"]
    assert state["programs"][0]["lanes"][0]["session"] is None
    assert state["programs"][0]["master"] == contract.null_master()
    assert state["sessions_unmapped"] == []
    contract.assert_shape(state)


def test_mcwallt_failopen_db_midjoin_nulls_all(tmp_path, monkeypatch):
    # S1 regression: a failure MID-join (after masters were already written)
    # must null EVERY piece of session data — partial data never leaks into
    # the document (spec §4.1 / assumption 19: strict fail-open).
    from mc_wall.tower import zcode_db
    master_id = "sess_71717171-7171-4717-8717-717171717171"
    db = mcwallt_make_db(tmp_path, sessions=[
        {"id": master_id, "title": "mcwallt master", "directory": "/mcwallt/m",
         "time_updated": ms_ago(100), "time_created": ms_ago(100)},
        {"id": "sess_9a690ab2-cde8-4e9a-bc4e-177fcc68545f", "title": "mcwallt lane",
         "directory": "/mcwallt/l", "time_updated": ms_ago(200), "time_created": ms_ago(200)},
    ], inputs=[mcwallt_tag_input(master_id, ["secfix-master"], ms_ago(100))])
    note = mcwallt_make_note(tmp_path, "mcwallt_note.md", [
        "| id | wave | lane | repo/branch | slug | base | session/MR artifacts | status |",
        "|---|---|---|---|---|---|---|---|",
        "| W1-L1 | W1 | L1 | n/a | n/a | n/a | sess_9a690ab2 | forged |",
    ])

    def exploding_lane_join(cur, token):
        raise RuntimeError("mcwallt mid-join explosion")

    monkeypatch.setattr(zcode_db, "lane_join", exploding_lane_join)
    cfg = TowerConfig(db_path=db,
                      programs=(ProgramConfig(program="secfix", tag="secfix",
                                              note_glob=note,
                                              master_tag="secfix-master"),),
                      now_s=mcwallt_clock(NOW))
    state = collect_state(cfg)
    # Masters are computed BEFORE lane joins, so the master WAS set when the
    # explosion hit — it must come back all-null anyway.
    assert state["programs"][0]["master"] == contract.null_master()
    assert all(lane["session"] is None
               for prog in state["programs"] for lane in prog["lanes"])
    assert state["sessions_unmapped"] == []
    assert state["server"]["degraded"] == ["tracking degraded: session store unreadable"]
    assert "internal error: collect_state failed" not in state["server"]["degraded"]
    contract.assert_shape(state)


def test_mcwallt_failopen_db_schema_drift(tmp_path):
    db = mcwallt_make_db(tmp_path, drop_input=True,
                         sessions=[{"id": "sess_0f0f0f0f-0f0f-40f0-80f0-0f0f0f0f0f0f",
                                    "title": "t", "directory": "/mcwallt",
                                    "time_updated": ms_ago(100),
                                    "time_created": ms_ago(100)}])
    note = mcwallt_make_note(tmp_path, "mcwallt_note.md", [
        "| id | wave | lane | repo/branch | slug | base | session/MR artifacts | status |",
        "|---|---|---|---|---|---|---|---|",
        "| W1-L1 | W1 | L1 | n/a | n/a | n/a | sess_0f0f0f0f | forged |",
    ])
    cfg = TowerConfig(db_path=db,
                      programs=(ProgramConfig(program="secfix", tag="secfix",
                                              note_glob=note),),
                      now_s=mcwallt_clock(NOW))
    state = collect_state(cfg)
    # Distinct from entry 1: a present, readable db missing a required table.
    assert state["server"]["degraded"] == ["tracking degraded: session store schema drift"]
    assert state["programs"][0]["lanes"][0]["session"] is None
    assert state["programs"][0]["master"] == contract.null_master()
    assert state["sessions_unmapped"] == []
    contract.assert_shape(state)


def test_mcwallt_join_tag_grammar():
    from mc_wall.tower import zcode_db
    assert zcode_db.parse_tags("Session title: [secfix]\n") == {"secfix"}
    # Leading/trailing line spaces tolerated.
    assert zcode_db.parse_tags("  Session title: [secfix]  ") == {"secfix"}
    # Anchored grammar: trailing content after ] kills the match.
    assert zcode_db.parse_tags("Session title: [a] x]") == set()
    # The tag needs at least one character.
    assert zcode_db.parse_tags("Session title: []") == set()


def test_mcwallt_join_directory_never_used(tmp_path):
    repo_path = str(tmp_path / "mcwallt_repo")
    sid = "sess_33333333-3333-4333-8333-333333333333"
    db = mcwallt_make_db(tmp_path, sessions=[
        {"id": sid, "title": "mcwallt dirful", "directory": repo_path + "/lane-dir",
         "time_updated": ms_ago(90), "time_created": ms_ago(90)}])
    cfg = TowerConfig(
        db_path=db,
        programs=(ProgramConfig(program="secfix", tag="secfix",
                                note_glob=mcwallt_empty_note(tmp_path)),),
        repos=(RepoConfig(name="mcwallt-repo", path=repo_path, host="gitlab"),),
        now_s=mcwallt_clock(NOW))
    state = collect_state(cfg)
    # No tag line anywhere: directory membership never joins -> unmapped.
    assert state["sessions_unmapped"] == [
        {"id": sid, "title": "mcwallt dirful", "dir": repo_path + "/lane-dir",
         "last_active_ago_s": 90}]
    assert state["server"]["degraded"] == []


def test_mcwallt_join_master_newest_wins(tmp_path):
    ids = {
        "a1": "sess_11111111-1111-4111-8111-111111111111",
        "a2": "sess_12121212-1212-4121-8121-121212121212",
        "b1": "sess_23232323-2323-4323-8323-232323232323",
        "b2": "sess_34343434-3434-4343-8343-343434343434",
        "c1": "sess_55555555-5555-4555-8555-555555555555",
        "c2": "sess_66666666-6666-4666-8666-666666666666",
    }

    def sess(key, tu_age, tc_age):
        return {"id": ids[key], "title": key, "directory": f"/mcwallt/{key}",
                "time_updated": ms_ago(tu_age), "time_created": ms_ago(tc_age)}

    sessions = [
        sess("a1", 5000, 5000),  # program A: distinct time_updated -> a2 wins
        sess("a2", 100, 100),
        sess("b1", 200, 200),    # program B: time_updated tie -> newer created b2 wins
        sess("b2", 200, 150),
        sess("c1", 300, 300),    # program C: full tie -> larger id c2 wins
        sess("c2", 300, 300),
    ]
    inputs = [mcwallt_tag_input(ids["a1"], ["master-a"], ms_ago(5000)),
              mcwallt_tag_input(ids["a2"], ["master-a"], ms_ago(100)),
              mcwallt_tag_input(ids["b1"], ["master-b"], ms_ago(200)),
              mcwallt_tag_input(ids["b2"], ["master-b"], ms_ago(200)),
              mcwallt_tag_input(ids["c1"], ["master-c"], ms_ago(300)),
              mcwallt_tag_input(ids["c2"], ["master-c"], ms_ago(300))]
    db = mcwallt_make_db(tmp_path, sessions=sessions, inputs=inputs)
    note = mcwallt_empty_note(tmp_path)
    cfg = TowerConfig(db_path=db, now_s=mcwallt_clock(NOW), programs=(
        ProgramConfig(program="pa", tag="pa", note_glob=note, master_tag="master-a"),
        ProgramConfig(program="pb", tag="pb", note_glob=note, master_tag="master-b"),
        ProgramConfig(program="pc", tag="pc", note_glob=note, master_tag="master-c")))
    state = collect_state(cfg)
    masters = {p["program"]: p["master"] for p in state["programs"]}
    assert masters["pa"]["session_id"] == ids["a2"]
    assert masters["pa"]["last_active_ago_s"] == 100
    assert masters["pb"]["session_id"] == ids["b2"]
    assert masters["pc"]["session_id"] == ids["c2"]
    assert state["server"]["degraded"] == []


def test_mcwallt_master_null_time_updated_loses(tmp_path):
    # S3 regression: a SQL-NULL time_updated must lose newest-wins to ANY real
    # timestamp — no TypeError escapes, no entry-1 over-reaction.
    null_id = "sess_91919191-9191-4919-8919-919191919191"
    real_id = "sess_92929292-9292-4929-8929-929292929292"
    db = mcwallt_make_db(tmp_path, sessions=[
        {"id": null_id, "title": "null-tu", "directory": "/mcwallt/nulltu",
         "time_updated": None, "time_created": ms_ago(10)},   # newer created, NULL updated
        {"id": real_id, "title": "real-tu", "directory": "/mcwallt/realtu",
         "time_updated": ms_ago(5000), "time_created": ms_ago(5000)},  # old but REAL
    ], inputs=[mcwallt_tag_input(null_id, ["secfix-master"], ms_ago(10)),
               mcwallt_tag_input(real_id, ["secfix-master"], ms_ago(5000))])
    cfg = TowerConfig(db_path=db,
                      programs=(ProgramConfig(program="secfix", tag="secfix",
                                              note_glob=mcwallt_empty_note(tmp_path),
                                              master_tag="secfix-master"),),
                      now_s=mcwallt_clock(NOW))
    state = collect_state(cfg)
    assert state["programs"][0]["master"]["session_id"] == real_id
    assert state["programs"][0]["master"]["last_active_ago_s"] == 5000
    assert state["server"]["degraded"] == []  # NULL is data, not an unreadable store
    contract.assert_shape(state)


def test_mcwallt_join_ambiguous_flagged(tmp_path):
    sid = "sess_77777777-7777-4777-8777-777777777777"
    db = mcwallt_make_db(tmp_path, sessions=[
        {"id": sid, "title": "mcwallt amb", "directory": "/mcwallt/amb",
         "time_updated": ms_ago(120), "time_created": ms_ago(120)}],
        inputs=[mcwallt_tag_input(sid, ["secfix"], ms_ago(120)),
                # A second DISTINCT tag — even with one tag configured, size >= 2
                # is ambiguous: maps to nothing, always flagged, never guessed.
                mcwallt_tag_input(sid, ["mcwallt-other"], ms_ago(110))])
    cfg = TowerConfig(db_path=db,
                      programs=(ProgramConfig(program="secfix", tag="secfix",
                                              note_glob=mcwallt_empty_note(tmp_path)),),
                      now_s=mcwallt_clock(NOW))
    state = collect_state(cfg)
    assert state["server"]["degraded"] == [f"join degraded: ambiguous tags {sid}"]
    assert state["programs"][0]["master"] == contract.null_master()
    assert [r["id"] for r in state["sessions_unmapped"]] == [sid]


def test_mcwallt_join_unconfigured_tag_unmapped(tmp_path):
    sid = "sess_8a8a8a8a-8a8a-48a8-88a8-8a8a8a8a8a8a"
    db = mcwallt_make_db(tmp_path, sessions=[
        {"id": sid, "title": "mcwallt uncfg", "directory": "/mcwallt/uncfg",
         "time_updated": ms_ago(60), "time_created": ms_ago(60)}],
        inputs=[mcwallt_tag_input(sid, ["not-configured"], ms_ago(60))])
    cfg = TowerConfig(db_path=db,
                      programs=(ProgramConfig(program="secfix", tag="secfix",
                                              note_glob=mcwallt_empty_note(tmp_path)),),
                      now_s=mcwallt_clock(NOW))
    state = collect_state(cfg)
    assert state["sessions_unmapped"] == [
        {"id": sid, "title": "mcwallt uncfg", "dir": "/mcwallt/uncfg",
         "last_active_ago_s": 60}]
    assert state["server"]["degraded"] == []  # unmatched tag is absence, not failure


def test_mcwallt_unmapped_rows_and_order(tmp_path):
    ids = {
        "old": "sess_0a0a0a0a-0a0a-40a0-80a0-0a0a0a0a0a0a",         # below window cutoff
        "archived": "sess_1b1b1b1b-1b1b-41b1-81b1-1b1b1b1b1b1b",    # time_archived set
        "subagent": "sess_subagent_agent_077dbbd1-f1a2-4611-9232-609679beed37",
        "mapped": "sess_3c3c3c3c-3c3c-43c3-83c3-3c3c3c3c3c3c",      # tagged with the program tag
        "u1": "sess_dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        "tie1": "sess_e4e4e4e4-e4e4-4e4e-8e4e-e4e4e4e4e4e4",
        "tie2": "sess_f5f5f5f5-f5f5-45f5-85f5-f5f5f5f5f5f5",
        "u2": "sess_9a9a9a9a-9a9a-49a9-89a9-9a9a9a9a9a9a",
    }

    def sess(key, age, *, archived=None):
        return {"id": ids[key], "title": key, "directory": f"/mcwallt/{key}",
                "time_updated": ms_ago(age), "time_created": ms_ago(age),
                "time_archived": archived}

    sessions = [
        sess("old", 200000),                                # below the 86400s window
        sess("archived", 100, archived=ms_ago(50)),
        sess("subagent", 80),                               # child session: excluded
        sess("mapped", 70),
        sess("u1", 1000),
        sess("tie1", 700),
        sess("tie2", 700),
        sess("u2", 500),
    ]
    inputs = [mcwallt_tag_input(ids["mapped"], ["secfix"], ms_ago(70))]
    db = mcwallt_make_db(tmp_path, sessions=sessions, inputs=inputs)
    cfg = TowerConfig(db_path=db,
                      programs=(ProgramConfig(program="secfix", tag="secfix",
                                              note_glob=mcwallt_empty_note(tmp_path)),),
                      now_s=mcwallt_clock(NOW))
    state = collect_state(cfg)
    # last_active desc (newest = smallest ago first), tie id asc.
    assert state["sessions_unmapped"] == [
        {"id": ids["u2"], "title": "u2", "dir": "/mcwallt/u2", "last_active_ago_s": 500},
        {"id": ids["tie1"], "title": "tie1", "dir": "/mcwallt/tie1", "last_active_ago_s": 700},
        {"id": ids["tie2"], "title": "tie2", "dir": "/mcwallt/tie2", "last_active_ago_s": 700},
        {"id": ids["u1"], "title": "u1", "dir": "/mcwallt/u1", "last_active_ago_s": 1000},
    ]
    assert state["server"]["degraded"] == []


def test_mcwallt_master_unset_nulls(tmp_path):
    sid = "sess_2b2b2b2b-2b2b-42b2-82b2-2b2b2b2b2b2b"
    db = mcwallt_make_db(tmp_path, sessions=[
        {"id": sid, "title": "mcwallt tagged", "directory": "/mcwallt/tagged",
         "time_updated": ms_ago(100), "time_created": ms_ago(100)}],
        inputs=[mcwallt_tag_input(sid, ["secfix"], ms_ago(100))])
    cfg = TowerConfig(db_path=db,
                      programs=(ProgramConfig(program="secfix", tag="secfix",
                                              note_glob=mcwallt_empty_note(tmp_path),
                                              master_tag=None),),
                      now_s=mcwallt_clock(NOW))
    state = collect_state(cfg)
    assert state["programs"][0]["master"] == {"session_id": None, "title": None,
                                              "last_active_ago_s": None}


def test_mcwallt_master_join_populated(tmp_path):
    sid = "sess_88888888-8888-4888-8888-888888888888"
    db = mcwallt_make_db(tmp_path, sessions=[
        {"id": sid, "title": "MC wall master session", "directory": "/mcwallt/master",
         "time_updated": ms_ago(3600), "time_created": ms_ago(3600)}],
        inputs=[mcwallt_tag_input(sid, ["secfix-master"], ms_ago(3600))])
    cfg = TowerConfig(db_path=db,
                      programs=(ProgramConfig(program="secfix", tag="secfix",
                                              note_glob=mcwallt_empty_note(tmp_path),
                                              master_tag="secfix-master"),),
                      now_s=mcwallt_clock(NOW))
    state = collect_state(cfg)
    assert state["programs"][0]["master"] == {"session_id": sid,
                                              "title": "MC wall master session",
                                              "last_active_ago_s": 3600}


def test_mcwallt_drift_unreadable(tmp_path):
    from mc_wall.tower import check_drift
    cfg = TowerConfig(db_path=str(tmp_path / "mcwallt_missing.db"), programs=(),
                      now_s=mcwallt_clock(NOW))
    assert check_drift(cfg) == ["tracking degraded: session store unreadable"]


def test_mcwallt_drift_schema_distinct(tmp_path):
    from mc_wall.tower import check_drift
    db = mcwallt_make_db(tmp_path, drop_input=True)
    cfg = TowerConfig(db_path=db, programs=(), now_s=mcwallt_clock(NOW))
    assert check_drift(cfg) == ["tracking degraded: session store schema drift"]


def test_mcwallt_drift_warning_not_degradation(tmp_path):
    from mc_wall.tower import check_drift
    prog = (ProgramConfig(program="secfix", tag="secfix",
                          note_glob=mcwallt_empty_note(tmp_path)),)

    # (a) healthy schema, newest sendText BELOW the unit-aware 24h cutoff:
    # guard-only warning; collect must NOT emit a degraded entry for it.
    quiet_sid = "sess_4d4d4d4d-4d4d-44d4-84d4-4d4d4d4d4d4d"
    quiet = mcwallt_make_db(tmp_path, name="mcwallt_quiet.db", sessions=[
        {"id": quiet_sid, "title": "q", "directory": "/mcwallt/q",
         "time_updated": ms_ago(90000), "time_created": ms_ago(90000)}],
        inputs=[mcwallt_tag_input(quiet_sid, ["secfix"], ms_ago(90000))])
    cfg = TowerConfig(db_path=quiet, programs=prog, now_s=mcwallt_clock(NOW))
    assert check_drift(cfg) == ["drift warning: no sendText rows in 24h"]
    state = collect_state(cfg)
    assert "drift warning: no sendText rows in 24h" not in state["server"]["degraded"]
    assert state["server"]["degraded"] == []

    # (b) newest sendText above the cutoff but its payload is not JSON:
    # warning 4, again absent from collect-time degraded.
    broken_sid = "sess_5d5d5d5d-5d5d-45d5-85d5-5d5d5d5d5d5d"
    broken = mcwallt_make_db(tmp_path, name="mcwallt_broken_payload.db", sessions=[
        {"id": broken_sid, "title": "b", "directory": "/mcwallt/b",
         "time_updated": ms_ago(100), "time_created": ms_ago(100)}],
        inputs=[{"session_id": broken_sid, "payload": "not-json",
                 "time_created": ms_ago(100)}])
    cfg2 = TowerConfig(db_path=broken, programs=prog, now_s=mcwallt_clock(NOW))
    assert check_drift(cfg2) == ["drift warning: session_input.payload not extractable"]
    state2 = collect_state(cfg2)
    assert "drift warning: session_input.payload not extractable" not in state2["server"]["degraded"]
    assert state2["server"]["degraded"] == []


def test_mcwallt_drift_null_payload(tmp_path):
    # S2 regression: a SQL-NULL payload above the cutoff is NOT an unreadable
    # store — check_drift (no LIKE prefilter on its checks) must answer
    # warning 4, never entry 1; scan_tags never even sees the row (NULL fails
    # the LIKE prefilter in SQL) and collect stays clean.
    from mc_wall.tower import check_drift
    sid = "sess_8e8e8e8e-8e8e-48e8-88e8-8e8e8e8e8e8e"
    db = mcwallt_make_db(tmp_path, sessions=[
        {"id": sid, "title": "null-payload", "directory": "/mcwallt/nullpayload",
         "time_updated": ms_ago(100), "time_created": ms_ago(100)}],
        inputs=[{"session_id": sid, "payload": None, "time_created": ms_ago(100)}])
    cfg = TowerConfig(db_path=db, programs=(), now_s=mcwallt_clock(NOW))
    assert check_drift(cfg) == ["drift warning: session_input.payload not extractable"]
    state = collect_state(cfg)
    assert state["server"]["degraded"] == []  # no entry 1: the row is skipped, tolerated
    assert state["sessions_unmapped"] == [
        {"id": sid, "title": "null-payload", "dir": "/mcwallt/nullpayload",
         "last_active_ago_s": 100}]
    contract.assert_shape(state)


def test_mcwallt_drift_pure(tmp_path):
    from mc_wall.tower import check_drift

    def snap(root):
        out = []
        for p in sorted(Path(root).rglob("*")):
            st = p.stat()
            out.append((str(p.relative_to(root)), p.is_dir(), st.st_size, st.st_mtime_ns))
        return out

    sid = "sess_6f6f6f6f-6f6f-46f6-86f6-6f6f6f6f6f6f"
    db = mcwallt_make_db(tmp_path, sessions=[
        {"id": sid, "title": "healthy", "directory": "/mcwallt/healthy",
         "time_updated": ms_ago(100), "time_created": ms_ago(100)}],
        inputs=[mcwallt_tag_input(sid, ["secfix"], ms_ago(100))])
    cfg = TowerConfig(db_path=db, programs=(), now_s=mcwallt_clock(NOW))
    before = snap(tmp_path)
    r1, r2 = check_drift(cfg), check_drift(cfg)
    assert r1 == r2 == []
    assert snap(tmp_path) == before  # no side effects anywhere in the fixture tree
