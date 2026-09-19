"""T9 — HandshakeMonitor: birth matching, ambiguity, conflict.

Every mc_wall / harness import is INSIDE the test functions, mirroring
test_mcwalls_matcher.py: at RED time each test must FAIL individually (exit 1)
instead of erroring at collection (exit 2).
"""

import json
import time

ROW = {
    "row_id": "secfix-w2-l7",
    "lane_tag": "[secfix W2-L7]",
    "prompt_text": "PROMPT TEXT 42",
    "goal_text": "GOAL TEXT 7",
}
STATE = {"schema_version": 1, "rows": [ROW]}


def _read_pending(state_dir):
    return json.loads((state_dir / "pending.json").read_text(encoding="utf-8"))


def _add_birth_rows(db, sessions, inputs):
    # Fixture db written mid-test through a fresh connection (never the
    # matcher's read-only one). Same row shapes as make_fixture_db.
    import sqlite3

    conn = sqlite3.connect(db)
    try:
        for sid, directory, created in sessions:
            conn.execute(
                "INSERT INTO session VALUES (?,?,?,?)", (sid, "t", created, directory)
            )
        for iid, sid, payload, created in inputs:
            conn.execute(
                "INSERT INTO session_input VALUES (?,?,?,?,?)",
                (iid, sid, "sendText", json.dumps(payload), created),
            )
        conn.commit()
    finally:
        conn.close()


def _start(tagged_state=STATE):
    # (db, clock, runner, serve-context) for one monitor-wired server.
    from tests.server.mcwalls_harness import (
        FakeClock,
        FakeRunner,
        StubTower,
        make_fixture_db,
        make_tmp_root,
        serve,
    )

    repo = make_tmp_root("mcwalls-repo-")
    db = make_tmp_root("mcwalls-db-") / "db.sqlite"
    make_fixture_db(path=db)
    clock = FakeClock(1_000_000)
    runner = FakeRunner()
    ctx = serve(
        collect_state=StubTower(tagged_state),
        runner=runner,
        db_path=db,
        monitor_interval_s=0.02,
        clock=clock,
    )
    return repo, db, clock, runner, ctx


def _launch(h, repo):
    return h.http(
        "POST", f"/{h.token}/launch", {"row_id": ROW["row_id"], "repo_root": str(repo)}
    )


def test_birth_match_full_handshake():
    from mc_wall.server import templates
    from tests.server.mcwalls_harness import wait_for

    repo, db, clock, runner, ctx = _start()
    with ctx as h:
        r = _launch(h, repo)
        assert r.status == 200
        base = clock()  # == launch_click_ms
        _add_birth_rows(
            db,
            sessions=[("s1", str(repo), base + 50)],
            inputs=[(1, "s1", {"text": "working [secfix W2-L7] now"}, base + 60)],
        )
        assert wait_for(lambda: _read_pending(h.state_dir)["status"] == "goal-armed")
        time.sleep(0.2)  # later ticks must not re-copy or re-notify
        pending = _read_pending(h.state_dir)
        assert pending["matched_session_id"] == "s1"
        assert pending["flag"] is None
        goal_block = templates.render_file(
            "goal_block.txt",
            {
                "goal_text": "GOAL TEXT 7",
                "lane_tag": "[secfix W2-L7]",
                "repo_root": str(repo),
            },
        )
        # Exactly ONE prompt copy (launch) + ONE goal-block copy.
        assert [c[1] for c in runner.of("copy")] == ["PROMPT TEXT 42", goal_block]
        title, body = templates.notification(
            "match-found", {"lane_tag": "[secfix W2-L7]"}
        )
        assert runner.of("notify") == [("notify", title, body)]
        wall_state = h.http("GET", f"/{h.token}/state").json()
        assert wall_state["wall"]["pending"]["status"] == "goal-armed"


def test_two_rows_same_session_no_ambiguity():
    from tests.server.mcwalls_harness import wait_for

    repo, db, clock, runner, ctx = _start()
    with ctx as h:
        r = _launch(h, repo)
        assert r.status == 200
        base = clock()
        _add_birth_rows(
            db,
            sessions=[("s1", str(repo), base + 50)],
            inputs=[
                (1, "s1", {"text": "first [secfix W2-L7] paste"}, base + 60),
                (2, "s1", {"text": "second [secfix W2-L7] paste"}, base + 70),
            ],
        )
        assert wait_for(lambda: _read_pending(h.state_dir)["status"] == "goal-armed")
        time.sleep(0.2)
        pending = _read_pending(h.state_dir)
        assert pending["matched_session_id"] == "s1"
        assert pending["flag"] is None  # two rows, ONE session -> no ambiguity
        assert len(runner.of("copy")) == 2  # prompt + exactly one goal copy
        assert len(runner.of("notify")) == 1


def test_session_before_cursor_never_matches():
    from mc_wall.server import templates
    from tests.server.mcwalls_harness import wait_for

    repo, db, clock, runner, ctx = _start()
    with ctx as h:
        r = _launch(h, repo)
        assert r.status == 200
        base = clock()
        # Matching tag, but session AND input predate the launch click.
        _add_birth_rows(
            db,
            sessions=[("s-old", str(repo), base - 10)],
            inputs=[(1, "s-old", {"text": "has the [secfix W2-L7] tag"}, base - 5)],
        )
        clock.advance(121_000)  # now - launch_click_ms >= 120_000 -> advisory
        title, body = templates.notification(
            "still-no-session", {"lane_tag": "[secfix W2-L7]"}
        )
        assert wait_for(lambda: runner.of("notify") == [("notify", title, body)])
        clock.advance(240_000)  # 361_000 total since the launch click
        time.sleep(0.2)  # several monitor ticks
        assert runner.of("notify") == [("notify", title, body)]  # one-shot
        pending = _read_pending(h.state_dir)
        assert pending["status"] == "await-birth"  # never auto-clears
        assert pending["advisory_120s_fired"] is True
        assert pending["matched_session_id"] is None
        assert [c for c in runner.of("copy") if "GOAL TEXT 7" in c[1]] == []


def test_like_and_json_confirmation_through_monitor():
    from tests.server.mcwalls_harness import wait_for

    tagged = {"schema_version": 1, "rows": [dict(ROW, lane_tag="50%_off")]}

    # (a) LIKE prefilter: "50 percent off" is not a containment of 50%_off.
    repo, db, clock, runner, ctx = _start(tagged)
    with ctx as h:
        r = _launch(h, repo)
        assert r.status == 200
        base = clock()
        _add_birth_rows(
            db,
            sessions=[("sa", str(repo), base + 10)],
            inputs=[(1, "sa", {"text": "50 percent off"}, base + 20)],
        )
        time.sleep(0.3)  # many ticks, never a match
        assert _read_pending(h.state_dir)["status"] == "await-birth"
        assert [c for c in runner.of("copy") if "GOAL TEXT 7" in c[1]] == []

    # (b) literal tag inside "text" -> the full handshake runs.
    repo, db, clock, runner, ctx = _start(tagged)
    with ctx as h:
        r = _launch(h, repo)
        assert r.status == 200
        base = clock()
        _add_birth_rows(
            db,
            sessions=[("sb", str(repo), base + 10)],
            inputs=[(1, "sb", {"text": "ship the 50%_off discount"}, base + 20)],
        )
        assert wait_for(lambda: _read_pending(h.state_dir)["status"] == "goal-armed")
        assert _read_pending(h.state_dir)["matched_session_id"] == "sb"

    # (c) tag under a DIFFERENT json key -> LIKE passes, JSON confirm discards.
    repo, db, clock, runner, ctx = _start(tagged)
    with ctx as h:
        r = _launch(h, repo)
        assert r.status == 200
        base = clock()
        _add_birth_rows(
            db,
            sessions=[("sc", str(repo), base + 10)],
            inputs=[(1, "sc", {"other": "50%_off", "text": "unrelated"}, base + 20)],
        )
        time.sleep(0.3)
        assert _read_pending(h.state_dir)["status"] == "await-birth"
        assert [c for c in runner.of("copy") if "GOAL TEXT 7" in c[1]] == []


def test_ambiguity_flag_no_action_slot_held():
    from tests.server.mcwalls_harness import wait_for

    repo, db, clock, runner, ctx = _start()
    with ctx as h:
        r = _launch(h, repo)
        assert r.status == 200
        base = clock()
        _add_birth_rows(
            db,
            sessions=[("s1", str(repo), base + 50), ("s2", str(repo), base + 55)],
            inputs=[
                (1, "s1", {"text": "one [secfix W2-L7]"}, base + 60),
                (2, "s2", {"text": "two [secfix W2-L7]"}, base + 65),
            ],
        )
        assert wait_for(lambda: _read_pending(h.state_dir)["flag"] == "ambiguous")
        time.sleep(0.2)  # ambiguity is a holding state — never an action
        pending = _read_pending(h.state_dir)
        assert pending["status"] == "await-birth"
        assert [c for c in runner.of("copy") if "GOAL TEXT 7" in c[1]] == []
        assert runner.of("notify") == []
        r2 = _launch(h, repo)
        assert r2.status == 409  # the slot is still held


def test_recopy_unflags_and_reevaluates():
    import sqlite3

    from tests.server.mcwalls_harness import wait_for

    repo, db, clock, runner, ctx = _start()
    with ctx as h:
        r = _launch(h, repo)
        assert r.status == 200
        base = clock()
        _add_birth_rows(
            db,
            sessions=[("s1", str(repo), base + 50), ("s2", str(repo), base + 55)],
            inputs=[
                (1, "s1", {"text": "one [secfix W2-L7]"}, base + 60),
                (2, "s2", {"text": "two [secfix W2-L7]"}, base + 65),
            ],
        )
        assert wait_for(lambda: _read_pending(h.state_dir)["flag"] == "ambiguous")
        r = h.http("POST", f"/{h.token}/launch/re-copy", {})
        assert r.status == 200
        assert r.json()["pending"]["flag"] is None
        # Still two matching sessions -> the monitor re-flags on the next tick.
        assert wait_for(lambda: _read_pending(h.state_dir)["flag"] == "ambiguous")
        # Resolve the ambiguity: point one session's directory elsewhere.
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                "UPDATE session SET directory=? WHERE id='s1'",
                (str(repo) + "-elsewhere",),
            )
            conn.commit()
        finally:
            conn.close()
        assert wait_for(lambda: _read_pending(h.state_dir)["status"] == "goal-armed")
        pending = _read_pending(h.state_dir)
        assert pending["matched_session_id"] == "s2"
        assert pending["flag"] is None


def test_conflict_flags_and_frees_slot():
    from mc_wall.server import templates
    from tests.server.mcwalls_harness import make_tmp_root, wait_for

    repo, db, clock, runner, ctx = _start()
    elsewhere = make_tmp_root("mcwalls-elsewhere-")
    with ctx as h:
        r = _launch(h, repo)
        assert r.status == 200
        base = clock()
        _add_birth_rows(
            db,
            sessions=[("s1", str(elsewhere), base + 50)],
            inputs=[(1, "s1", {"text": "landed [secfix W2-L7] elsewhere"}, base + 60)],
        )
        assert wait_for(lambda: _read_pending(h.state_dir)["status"] == "flagged")
        time.sleep(0.2)  # flagged is terminal — no later tick acts again
        pending = _read_pending(h.state_dir)
        assert pending["reason"] == "conflict"
        assert pending["flag"] is None
        title, body = templates.notification(
            "conflict", {"lane_tag": "[secfix W2-L7]"}
        )
        assert runner.of("notify") == [("notify", title, body)]
        # Tombstone surfaced by /state (checked BEFORE the slot is reused).
        wall_state = h.http("GET", f"/{h.token}/state").json()
        assert wall_state["wall"]["pending"]["status"] == "flagged"
        # Terminal -> the slot is freed: a new launch succeeds.
        r2 = _launch(h, repo)
        assert r2.status == 200
