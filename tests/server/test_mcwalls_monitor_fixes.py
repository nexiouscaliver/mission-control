"""T9-T11 review fixes — monitor concurrency regressions (S1/S2/S3).

S1: a status-only _patch guard lets a monitor patch built for launch A adopt
    a NEW await-birth record B after cancel+relaunch (goal-armed with
    matched_session_id=None -> wedged forever).
S2: last_eval_ms read from the clock BEFORE the queries: a birth paste
    committed during the advisory-notify window lands AFTER the cursor and
    reads as a duplicate on the next goal-armed tick (false "prompt again").
S3: unwrapped runner calls: a failing notify re-matches every tick (clipboard
    overwritten forever); a failing copy must be contained and retried.

Every mc_wall / harness import is INSIDE the test functions, mirroring
test_mcwalls_monitor_match.py: at RED time each test must FAIL individually
(exit 1) instead of erroring at collection (exit 2).

All ticks are MANUAL (`monitor._tick()` on a monitor built against the live
server's store) — deterministic, no thread interleaving needed.
"""

import json

ROW = {
    "row_id": "secfix-w2-l7",
    "lane_tag": "[secfix W2-L7]",
    "prompt_text": "PROMPT TEXT 42",
    "goal_text": "GOAL TEXT 7",
}
STATE = {"schema_version": 1, "rows": [ROW]}


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


def _manual_monitor(h, db, clock):
    # A monitor wired to the live server's store/runner/tower/logger but with
    # NO thread: the test drives _tick() by hand.
    from mc_wall.server.matcher import Matcher
    from mc_wall.server.monitor import HandshakeMonitor

    ctx = h.server.ctx
    return HandshakeMonitor(
        ctx.pending,
        Matcher(db),
        ctx.runner,
        ctx.collect_state,
        ctx.logger,
        interval_s=0.02,
        clock=clock,
    )


def _run_tick(monitor):
    # Exactly run()'s containment: one tick, one ERROR line on any escape.
    try:
        monitor._tick()
    except Exception as exc:  # never die — same guard as the thread loop
        monitor.logger.error("monitor-tick-failed error=%s", type(exc).__name__)


def test_cancel_relaunch_no_hijack():
    from tests.server.mcwalls_harness import (
        FakeClock,
        StubTower,
        make_fixture_db,
        make_tmp_root,
        serve,
    )

    row = {
        "row_id": "hijack-w3-l2",
        "lane_tag": "[hijack W3-L2]",
        "prompt_text": "PROMPT BODY [hijack W3-L2] verbatim",
        "goal_text": "GOAL TEXT 12",
    }
    state = {"schema_version": 1, "rows": [row]}
    repo = make_tmp_root("mcwalls-repo-")
    db = make_tmp_root("mcwalls-db-") / "db.sqlite"
    make_fixture_db(path=db)
    clock = FakeClock(1_000_000)
    box = {}

    class CancelRelaunchRunner:
        """copy() re-entrantly fires ONE queued POST from inside the side
        effect (a second connection + handler thread; the caller blocks)."""

        def __init__(self):
            self.calls = []

        def copy(self, text):
            self.calls.append(("copy", text))
            fire = box.pop("fire", None)
            if fire is not None:
                fire()

        def open_url(self, url):
            self.calls.append(("open_url", url))

        def open_app(self, name):
            self.calls.append(("open_app", name))

        def notify(self, title, body):
            self.calls.append(("notify", title, body))

        def of(self, kind):
            return [c for c in self.calls if c[0] == kind]

    runner = CancelRelaunchRunner()
    with serve(
        collect_state=StubTower(state),
        runner=runner,
        db_path=db,
        monitor_interval_s=0.02,
        clock=clock,
        start_monitor=False,
    ) as h:
        monitor = _manual_monitor(h, db, clock)
        r = h.http(
            "POST",
            f"/{h.token}/launch",
            {"row_id": row["row_id"], "repo_root": str(repo)},
        )
        assert r.status == 200
        # Birth rows dated after BOTH launch clicks (A's and the incoming B's).
        _add_rows(
            db,
            sessions=[("s1", str(repo), 1_006_000)],
            inputs=[(1, "s1", {"text": "born [hijack W3-L2]"}, 1_006_050)],
        )

        def cancel_and_relaunch():
            # Runs INSIDE the monitor's goal copy — between the linkage patch
            # and the goal-arm patch: the exact S1 hijack window.
            clock.advance(5_000)  # B's launch click is a NEW unique click
            rc = h.http("POST", f"/{h.token}/launch/cancel", {})
            assert rc.status == 200
            rl = h.http(
                "POST",
                f"/{h.token}/launch",
                {"row_id": row["row_id"], "repo_root": str(repo)},
            )
            assert rl.status == 200
            box["relaunched"] = rl.json()["pending"]

        box["fire"] = cancel_and_relaunch
        monitor._tick()  # match A -> linkage -> copy (cancel + relaunch B) -> patch
        box.pop("fire", None)
        assert box["relaunched"]["status"] == "await-birth"  # B really launched
        pending = _read_pending(h.state_dir)
        # S1: the goal-arm patch built for A must NOT adopt record B — B is
        # never promoted to goal-armed with a null linkage.
        assert pending["status"] == "await-birth"
        assert pending["matched_session_id"] is None
        assert pending["launch_click_ms"] == 1_005_000  # this is B, fresh
        # And B matches normally on its own later ticks.
        monitor._tick()
        monitor._tick()
        pending = _read_pending(h.state_dir)
        assert pending["status"] == "goal-armed"
        assert pending["matched_session_id"] == "s1"


def test_no_false_duplicate_in_advisory_window():
    from mc_wall.server import templates
    from tests.server.mcwalls_harness import (
        FakeClock,
        StubTower,
        make_fixture_db,
        make_tmp_root,
        serve,
    )

    row = {
        "row_id": "advisory-w3-l4",
        "lane_tag": "[advisory W3-L4]",
        "prompt_text": "PROMPT BODY [advisory W3-L4] verbatim",
        "goal_text": "GOAL TEXT 13",
    }
    state = {"schema_version": 1, "rows": [row]}
    repo = make_tmp_root("mcwalls-repo-")
    db = make_tmp_root("mcwalls-db-") / "db.sqlite"
    make_fixture_db(path=db)
    clock = FakeClock(1_000_000)
    box = {}

    class AdvisoryBirthRunner:
        """notify() commits the birth paste MID-TICK — exactly the S2 window:
        the paste's time_created postdates the tick's already-read clock."""

        def __init__(self):
            self.calls = []

        def copy(self, text):
            self.calls.append(("copy", text))

        def open_url(self, url):
            self.calls.append(("open_url", url))

        def open_app(self, name):
            self.calls.append(("open_app", name))

        def notify(self, title, body):
            self.calls.append(("notify", title, body))
            fire = box.pop("fire", None)
            if fire is not None:
                fire()

        def of(self, kind):
            return [c for c in self.calls if c[0] == kind]

    runner = AdvisoryBirthRunner()
    with serve(
        collect_state=StubTower(state),
        runner=runner,
        db_path=db,
        monitor_interval_s=0.02,
        clock=clock,
        start_monitor=False,
    ) as h:
        monitor = _manual_monitor(h, db, clock)
        r = h.http(
            "POST",
            f"/{h.token}/launch",
            {"row_id": row["row_id"], "repo_root": str(repo)},
        )
        assert r.status == 200  # launch_click_ms == 1_000_000
        clock.advance(200_000)  # the advisory is due on the next tick

        def commit_birth():
            # The birth paste IS the prompt, committed DURING the advisory
            # notification — after the tick read now=1_200_000, before
            # find_candidates runs.
            _add_rows(
                db,
                sessions=[("s1", str(repo), clock() + 40)],
                inputs=[(1, "s1", {"text": row["prompt_text"]}, clock() + 50)],
            )
            box["birth_committed"] = True

        box["fire"] = commit_birth
        monitor._tick()  # advisory notify -> birth commits -> match
        assert box.get("birth_committed") is True
        pending = _read_pending(h.state_dir)
        assert pending["status"] == "goal-armed"
        assert pending["matched_session_id"] == "s1"
        # The advisory DID fire in the matched tick — the window was exercised.
        adv_title, adv_body = templates.notification(
            "still-no-session", {"lane_tag": row["lane_tag"]}
        )
        assert (
            len([n for n in runner.of("notify") if n[1] == adv_title and n[2] == adv_body])
            == 1
        )
        # S2: the ORIGINAL birth paste must never read as a duplicate, no
        # matter how many goal-armed ticks re-evaluate it.
        for _ in range(3):
            monitor._tick()
        rc_title, rc_body = templates.notification("goal-re-copied", {})
        assert [
            n for n in runner.of("notify") if n[1] == rc_title and n[2] == rc_body
        ] == []
        goal_block = templates.render_file(
            "goal_block.txt",
            {
                "goal_text": row["goal_text"],
                "lane_tag": row["lane_tag"],
                "repo_root": str(repo),
            },
        )
        # Exactly ONE goal-block copy (the match itself), never a re-copy.
        assert len([c for c in runner.of("copy") if c[1] == goal_block]) == 1


def test_notify_failure_does_not_loop_copy():
    from tests.server.mcwalls_harness import (
        FakeClock,
        StubTower,
        make_fixture_db,
        make_tmp_root,
        serve,
        write_pending,
    )

    repo = make_tmp_root("mcwalls-repo-")
    db = make_tmp_root("mcwalls-db-") / "db.sqlite"
    make_fixture_db(path=db)

    class NotifyBoomRunner:
        def __init__(self):
            self.calls = []

        def copy(self, text):
            self.calls.append(("copy", text))

        def open_url(self, url):
            self.calls.append(("open_url", url))

        def open_app(self, name):
            self.calls.append(("open_app", name))

        def notify(self, title, body):
            self.calls.append(("notify", title, body))
            raise RuntimeError("boom-notify")

        def of(self, kind):
            return [c for c in self.calls if c[0] == kind]

    # (a) notify always fails: the goal copy happens ONCE and the handshake
    # still advances to goal-armed.
    clock_a = FakeClock(1_000_000)
    runner_a = NotifyBoomRunner()
    with serve(
        collect_state=StubTower(STATE),
        runner=runner_a,
        db_path=db,
        monitor_interval_s=0.02,
        clock=clock_a,
        start_monitor=False,
    ) as h:
        monitor = _manual_monitor(h, db, clock_a)
        r = h.http(
            "POST", f"/{h.token}/launch", {"row_id": ROW["row_id"], "repo_root": str(repo)}
        )
        assert r.status == 200
        _add_rows(
            db,
            sessions=[("s1", str(repo), 1_000_050)],
            inputs=[(1, "s1", {"text": "has [secfix W2-L7] tag"}, 1_000_060)],
        )
        for _ in range(5):
            _run_tick(monitor)
        assert _read_pending(h.state_dir)["status"] == "goal-armed"
        # prompt copy + exactly ONE goal copy — no per-tick re-copy loop.
        assert [c[0] for c in runner_a.of("copy")] == ["copy", "copy"]
        log_text = (h.log_dir / "wall.log").read_text(encoding="utf-8")
        assert "monitor-notify-failed error=RuntimeError" in log_text
        assert "monitor-tick-failed" not in log_text  # contained, never escaped

    # (b) copy fails, then recovers: each failing tick is contained (no
    # exception escapes _tick), the record stays await-birth, and the next
    # tick retries the copy.
    import hashlib

    db_b = make_tmp_root("mcwalls-db-") / "db.sqlite"
    make_fixture_db(
        path=db_b,
        sessions=[("s1", str(repo), 1_000_050)],
        inputs=[(1, "s1", {"text": "has [secfix W2-L7] tag"}, 1_000_060)],
    )

    class FlakyCopyRunner:
        def __init__(self, fail_first):
            self.calls = []
            self.fail_first = fail_first

        def copy(self, text):
            self.calls.append(("copy", text))
            if self.fail_first > 0:
                self.fail_first -= 1
                raise RuntimeError("boom-copy")

        def open_url(self, url):
            self.calls.append(("open_url", url))

        def open_app(self, name):
            self.calls.append(("open_app", name))

        def notify(self, title, body):
            self.calls.append(("notify", title, body))

        def of(self, kind):
            return [c for c in self.calls if c[0] == kind]

    state_dir = make_tmp_root("mcwalls-state-")
    write_pending(
        state_dir,
        {
            "status": "await-birth",
            "row_id": ROW["row_id"],
            "lane_tag": ROW["lane_tag"],
            "repo_root": str(repo),
            "prompt_sha256": hashlib.sha256(
                ROW["prompt_text"].encode("utf-8")
            ).hexdigest(),
            "launch_click_ms": 1_000_000,
        },
    )
    clock_b = FakeClock(1_000_000)
    runner_b = FlakyCopyRunner(fail_first=2)
    with serve(
        collect_state=StubTower(STATE),
        runner=runner_b,
        state_dir=state_dir,
        db_path=db_b,
        monitor_interval_s=0.02,
        clock=clock_b,
        start_monitor=False,
    ) as h:
        monitor = _manual_monitor(h, db_b, clock_b)
        monitor._tick()  # copy attempt 1 fails — MUST NOT raise
        assert _read_pending(state_dir)["status"] == "await-birth"
        monitor._tick()  # copy attempt 2 fails — still contained, still waiting
        assert _read_pending(state_dir)["status"] == "await-birth"
        monitor._tick()  # attempt 3 succeeds -> the handshake advances
        assert _read_pending(state_dir)["status"] == "goal-armed"
        assert len(runner_b.of("copy")) == 3  # two failed + one good
        log_text = (h.log_dir / "wall.log").read_text(encoding="utf-8")
        assert log_text.count("monitor-copy-failed error=RuntimeError") == 2
