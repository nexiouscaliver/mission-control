"""T-1 collect tests: AC-API-1, AC-LAUNCH-1/2, AC-FAIL-8, DegradedLog ordering."""

import sqlite3

from mc_wall.tower import NetCache, TowerConfig, collect_state
from mc_wall.tower import contract
from mc_wall.tower.collect import DegradedLog


def mcwallt_make_session_db(tmp_path, name="mcwallt_sessions.db"):
    """Minimal db satisfying the T-1 session-store probe (session table present)."""
    p = tmp_path / name
    con = sqlite3.connect(p)
    con.execute(
        "CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT, directory TEXT,"
        " time_updated INTEGER, time_created INTEGER, time_archived INTEGER)"
    )
    con.commit()
    con.close()
    return str(p)


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
