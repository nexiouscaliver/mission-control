"""T10 — HandshakeMonitor goal-armed behaviors: duplicate paste re-copy,
one-shot canary nudge, goal confirmation, 120 s advisory one-shot, and
db-absent degradation (monitor only, server unaffected).

Every mc_wall / harness import is INSIDE the test functions, mirroring
test_mcwalls_monitor_match.py: at RED time each test must FAIL individually
(exit 1) instead of erroring at collection (exit 2).
"""

import json
import threading
import time

ROW = {
    "row_id": "secfix-w2-l7",
    "lane_tag": "[secfix W2-L7]",
    "prompt_text": "PROMPT TEXT 42",
    "goal_text": "GOAL TEXT 7",
}
STATE = {"schema_version": 1, "rows": [ROW]}


class TickingClock:
    """A clock whose every read advances virtual time (thread-safe).

    For tests that insert session-db rows while the monitor is live: a row
    dated at ``clock()`` is then strictly BEFORE every later tick's ``now``
    and strictly AFTER every earlier one, so a duplicate paste is detected
    exactly once whatever the tick interleaving (a frozen FakeClock makes
    advance-then-insert racy: a tick in the gap moves the cursor past the
    row, insert-then-advance double-fires)."""

    def __init__(self, start_ms, step_ms=100):
        self._lock = threading.Lock()
        self._now = start_ms
        self._step = step_ms

    def __call__(self):
        with self._lock:
            self._now += self._step
            return self._now


def _read_pending(state_dir):
    return json.loads((state_dir / "pending.json").read_text(encoding="utf-8"))


def _add_rows(db, sessions=(), inputs=(), targets=()):
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
        for sid, tid, created in targets:
            conn.execute(
                "INSERT INTO session_target VALUES (?,?,?,?,?)",
                (sid, tid, "obj", "open", created),
            )
        conn.commit()
    finally:
        conn.close()


def _start(state=STATE, clock=None):
    # (repo, db, clock, runner, serve-context) for one monitor-wired server.
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
    clock = clock if clock is not None else FakeClock(1_000_000)
    runner = FakeRunner()
    ctx = serve(
        collect_state=StubTower(state),
        runner=runner,
        db_path=db,
        monitor_interval_s=0.02,
        clock=clock,
    )
    return repo, db, clock, runner, ctx


def _launch(h, repo, row_id=ROW["row_id"]):
    return h.http(
        "POST", f"/{h.token}/launch", {"row_id": row_id, "repo_root": str(repo)}
    )


def _notify_count(runner, section, mapping):
    from mc_wall.server import templates

    title, body = templates.notification(section, mapping)
    return len([n for n in runner.of("notify") if n[1] == title and n[2] == body])


def _goal_copy_count(runner, repo, goal_text, lane_tag):
    from mc_wall.server import templates

    block = templates.render_file(
        "goal_block.txt",
        {"goal_text": goal_text, "lane_tag": lane_tag, "repo_root": str(repo)},
    )
    return len([c for c in runner.of("copy") if c[1] == block])


def test_duplicate_paste_refires_goal_once():
    from tests.server.mcwalls_harness import wait_for

    # The prompt itself carries the lane tag (as in the real handshake): the
    # BIRTH paste IS the prompt, so sha256(birth text) == prompt_sha256.
    # TickingClock determinism: reads strictly increase, and a match tick's
    # clock read always postdates its snapshot of the launch record — so the
    # birth paste (dated launch_click+50, under one clock step past the
    # launch) is ALWAYS behind the match tick's now, while a duplicate dated
    # at a fresh clock() is ALWAYS ahead of every prior snapshot cursor.
    row = {
        "row_id": "dup-w2-l9",
        "lane_tag": "[dup W2-L9]",
        "prompt_text": "PROMPT BODY [dup W2-L9] verbatim",
        "goal_text": "GOAL TEXT 9",
    }
    state = {"schema_version": 1, "rows": [row]}
    clock = TickingClock(1_000_000)
    repo, db, clock, runner, ctx = _start(state, clock=clock)
    with ctx as h:
        r = _launch(h, repo, row_id=row["row_id"])
        assert r.status == 200
        prompt = row["prompt_text"]
        launch_click = r.json()["pending"]["launch_click_ms"]
        _add_rows(
            db,
            sessions=[("s1", str(repo), launch_click + 10)],
            inputs=[(1, "s1", {"text": prompt}, launch_click + 50)],
        )
        assert wait_for(lambda: _read_pending(h.state_dir)["status"] == "goal-armed")
        # Corrected duplicate-paste pin: BEFORE any duplicate is inserted, the
        # birth paste itself must NEVER fire goal-re-copied — the regression
        # guard for T9's last_eval_ms=now-at-goal-arm pin (the match tick's
        # now postdates the paste, so the cursor excludes it).
        time.sleep(0.2)  # several goal-armed ticks
        assert _notify_count(runner, "goal-re-copied", {}) == 0
        assert _goal_copy_count(runner, repo, "GOAL TEXT 9", row["lane_tag"]) == 1
        # A true duplicate: the prompt pasted AGAIN into the matched session.
        _add_rows(db, inputs=[(2, "s1", {"text": prompt}, clock())])
        assert wait_for(lambda: _notify_count(runner, "goal-re-copied", {}) == 1)
        assert _goal_copy_count(runner, repo, "GOAL TEXT 9", row["lane_tag"]) == 2
        time.sleep(0.2)  # the firing tick moved last_eval_ms past the row
        assert _notify_count(runner, "goal-re-copied", {}) == 1  # no re-fire
        # A DIFFERENT text never fires (sha mismatch alone).
        _add_rows(db, inputs=[(3, "s1", {"text": "different text"}, clock())])
        time.sleep(0.2)
        assert _notify_count(runner, "goal-re-copied", {}) == 1
        assert _goal_copy_count(runner, repo, "GOAL TEXT 9", row["lane_tag"]) == 2
        assert _read_pending(h.state_dir)["status"] == "goal-armed"


def test_canary_one_shot():
    from tests.server.mcwalls_harness import wait_for

    repo, db, clock, runner, ctx = _start()
    with ctx as h:
        r = _launch(h, repo)
        assert r.status == 200
        base = clock()
        _add_rows(
            db,
            sessions=[("s1", str(repo), base + 50)],
            inputs=[(1, "s1", {"text": "has the [secfix W2-L7] tag"}, base + 60)],
        )
        assert wait_for(lambda: _read_pending(h.state_dir)["status"] == "goal-armed")
        # 60 s pass with no /goal target in the matched session.
        clock.advance(61_000)
        assert wait_for(
            lambda: _notify_count(
                runner, "canary-nudge", {"lane_tag": ROW["lane_tag"]}
            )
            == 1
        )
        assert _read_pending(h.state_dir)["canary_fired"] is True
        # Far past the threshold, still no target: the one-shot holds.
        clock.advance(120_000)
        time.sleep(0.2)
        assert (
            _notify_count(runner, "canary-nudge", {"lane_tag": ROW["lane_tag"]}) == 1
        )
        pending = _read_pending(h.state_dir)
        assert pending["status"] == "goal-armed"  # the canary never clears


def test_goal_confirmation_clears():
    from tests.server.mcwalls_harness import wait_for

    repo, db, clock, runner, ctx = _start()
    with ctx as h:
        r = _launch(h, repo)
        assert r.status == 200
        base = clock()
        _add_rows(
            db,
            sessions=[("s1", str(repo), base + 50)],
            inputs=[(1, "s1", {"text": "has the [secfix W2-L7] tag"}, base + 60)],
        )
        assert wait_for(lambda: _read_pending(h.state_dir)["status"] == "goal-armed")
        matched_at = _read_pending(h.state_dir)["matched_at_ms"]

        # The /goal lands in the matched session -> confirmed, slot freed.
        _add_rows(db, targets=[("s1", "g1", matched_at + 100)])

        def _confirmed():
            pending = _read_pending(h.state_dir)
            return (
                pending["status"] == "cleared"
                and pending["reason"] == "goal-confirmed"
            )

        assert wait_for(_confirmed)
        r2 = _launch(h, repo)
        assert r2.status == 200


def test_advisory_120s_never_clears():
    from tests.server.mcwalls_harness import wait_for

    repo, db, clock, runner, ctx = _start()
    with ctx as h:
        r = _launch(h, repo)
        assert r.status == 200
        # No birth ever happens.
        clock.advance(121_000)
        assert wait_for(
            lambda: _notify_count(
                runner, "still-no-session", {"lane_tag": ROW["lane_tag"]}
            )
            == 1
        )
        assert _read_pending(h.state_dir)["status"] == "await-birth"
        # 3x the threshold (361_000 total since the launch click): still
        # exactly one advisory, still holding the record.
        clock.advance(240_000)
        time.sleep(0.2)
        assert (
            _notify_count(runner, "still-no-session", {"lane_tag": ROW["lane_tag"]})
            == 1
        )
        pending = _read_pending(h.state_dir)
        assert pending["status"] == "await-birth"  # never auto-clears
        assert pending["advisory_120s_fired"] is True


def test_missing_db_degrades_monitor_only():
    from tests.server.mcwalls_harness import (
        FakeRunner,
        StubTower,
        make_tmp_root,
        serve,
        wait_for,
    )

    runner = FakeRunner()
    tmp = make_tmp_root()
    with serve(
        collect_state=StubTower(STATE),
        runner=runner,
        db_path=tmp / "missing.sqlite",
        monitor_interval_s=0.02,
    ) as h:
        r = h.http(
            "POST",
            f"/{h.token}/launch",
            {"row_id": ROW["row_id"], "repo_root": "/abs/repo"},
        )
        assert r.status == 200
        pending = _read_pending(h.state_dir)
        assert pending["status"] == "await-birth"
        click_ms = pending["launch_click_ms"]
        assert click_ms > 0
        # Many ticks against the missing db: one WARNING per failed query,
        # never an exception, never a state change.
        assert wait_for(
            lambda: "session-db-unavailable"
            in (h.log_dir / "wall.log").read_text(encoding="utf-8")
        )
        time.sleep(0.1)  # more failed ticks
        pending = _read_pending(h.state_dir)
        assert pending["status"] == "await-birth"  # degraded, not destroyed
        assert pending["launch_click_ms"] == click_ms  # cursor intact
        state = h.http("GET", f"/{h.token}/state")
        assert state.status == 200  # the HTTP server itself is unaffected
        assert state.json()["wall"]["pending"]["status"] == "await-birth"
