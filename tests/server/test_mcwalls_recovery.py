"""T11 — Crash recovery, races, corrupt-boot quarantine.

Every mc_wall / harness import is INSIDE the test functions, mirroring
test_mcwalls_monitor_match.py: at RED time each test must FAIL individually
(exit 1) instead of erroring at collection (exit 2).
"""

import hashlib
import json
import re
import threading
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


def _launch(h, repo):
    return h.http(
        "POST", f"/{h.token}/launch", {"row_id": ROW["row_id"], "repo_root": str(repo)}
    )


def test_recovery_await_birth():
    from tests.server.mcwalls_harness import (
        FakeClock,
        FakeRunner,
        StubTower,
        make_fixture_db,
        make_tmp_root,
        serve,
        wait_for,
    )

    state_dir = make_tmp_root("mcwalls-state-")
    db = make_tmp_root("mcwalls-db-") / "db.sqlite"
    make_fixture_db(path=db)
    repo = make_tmp_root("mcwalls-repo-")
    clock = FakeClock(1_000_000)

    # Server A: the launch persists await-birth, then A dies.
    with serve(
        collect_state=StubTower(STATE),
        runner=FakeRunner(),
        state_dir=state_dir,
        db_path=db,
        monitor_interval_s=0.02,
        clock=clock,
    ) as a:
        r = _launch(a, repo)
        assert r.status == 200
    # A is shut down; the birth happens only after that.
    runner_b = FakeRunner()
    with serve(
        collect_state=StubTower(STATE),
        runner=runner_b,
        state_dir=state_dir,
        db_path=db,
        monitor_interval_s=0.02,
        clock=clock,
    ) as b:
        # The recovered record surfaces as await-birth BEFORE any birth
        # exists in the session db (deterministic: nothing to match yet).
        wall = b.http("GET", f"/{b.token}/state").json()
        assert wall["wall"]["pending"]["status"] == "await-birth"
        base = clock()
        _add_rows(
            db,
            sessions=[("s1", str(repo), base + 50)],
            inputs=[(1, "s1", {"text": "born after the crash [secfix W2-L7]"}, base + 60)],
        )
        assert wait_for(lambda: _read_pending(state_dir)["status"] == "goal-armed")
        assert _read_pending(state_dir)["matched_session_id"] == "s1"


def test_recovery_goal_armed_no_refire_duplicate_still_works():
    from mc_wall.server import templates
    from tests.server.mcwalls_harness import (
        FakeClock,
        FakeRunner,
        StubTower,
        make_fixture_db,
        make_tmp_root,
        serve,
        wait_for,
        write_pending,
    )

    t0, t1 = 2_000_000, 2_000_500
    prompt = ROW["prompt_text"]
    repo = make_tmp_root("mcwalls-repo-")
    db = make_tmp_root("mcwalls-db-") / "db.sqlite"
    # The pre-crash birth paste IS the prompt, dated BEFORE the persisted
    # last_eval_ms cursor: without that cursor a reboot would re-fire it.
    make_fixture_db(
        path=db,
        sessions=[("s1", str(repo), t0 - 10_000)],
        inputs=[(1, "s1", {"text": prompt}, t0 - 1_000)],
    )
    state_dir = make_tmp_root("mcwalls-state-")
    write_pending(
        state_dir,
        {
            "status": "goal-armed",
            "row_id": ROW["row_id"],
            "lane_tag": ROW["lane_tag"],
            "repo_root": str(repo),
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "launch_click_ms": t0 - 60_000,
            "matched_session_id": "s1",
            "matched_at_ms": t0,
            "last_eval_ms": t1,
            "canary_fired": True,
            "advisory_120s_fired": True,
        },
    )
    clock = FakeClock(t0 + 600_000)  # ten minutes after the match
    runner = FakeRunner()
    # Phase A: first recovery boot — nothing pre-crash may re-fire.
    with serve(
        collect_state=StubTower(STATE),
        runner=runner,
        state_dir=state_dir,
        db_path=db,
        monitor_interval_s=0.02,
        clock=clock,
    ) as h:
        time.sleep(0.3)  # many goal-armed ticks against the recovered record
        # No canary refire (canary_fired persisted), no match-found (the
        # monitor never re-runs await-birth logic), no pre-crash duplicate
        # refire (last_eval_ms persisted past the birth paste).
        assert runner.of("notify") == []
        assert runner.of("copy") == []
        assert _read_pending(state_dir)["status"] == "goal-armed"
    # "Crash" again. With no monitor running, the duplicate paste cannot
    # race a tick: date it past the persisted cursor, then move now past it
    # (exactly the real-clock relation: paste time < next tick's now).
    cursor = _read_pending(state_dir)["last_eval_ms"]
    _add_rows(db, inputs=[(2, "s1", {"text": prompt}, cursor + 50)])
    clock.advance(1_000)
    # Phase B: second recovery boot — the duplicate fires exactly once and
    # the firing tick's cursor advance prevents any re-fire.
    with serve(
        collect_state=StubTower(STATE),
        runner=runner,
        state_dir=state_dir,
        db_path=db,
        monitor_interval_s=0.02,
        clock=clock,
    ) as h2:
        title, body = templates.notification("goal-re-copied", {})
        assert wait_for(
            lambda: len(
                [n for n in runner.of("notify") if n[1] == title and n[2] == body]
            )
            == 1
        )
        goal_block = templates.render_file(
            "goal_block.txt",
            {
                "goal_text": ROW["goal_text"],
                "lane_tag": ROW["lane_tag"],
                "repo_root": str(repo),
            },
        )
        assert [c[1] for c in runner.of("copy")] == [goal_block]
        time.sleep(0.2)
        assert (
            len([n for n in runner.of("notify") if n[1] == title and n[2] == body])
            == 1
        )


def test_recovery_prompt_armed_promoted():
    from tests.server.mcwalls_harness import (
        FakeRunner,
        StubTower,
        make_tmp_root,
        serve,
        write_pending,
    )

    state_dir = make_tmp_root("mcwalls-state-")
    write_pending(
        state_dir,
        {
            "status": "prompt-armed",
            "row_id": ROW["row_id"],
            "lane_tag": ROW["lane_tag"],
            "repo_root": "/abs/repo",
            "prompt_sha256": "0" * 64,
            "launch_click_ms": 1_000,
        },
    )
    runner = FakeRunner()
    # No db wired at all: the promotion must happen at BOOT, with no monitor
    # tick involved.
    with serve(
        collect_state=StubTower(STATE),
        runner=runner,
        state_dir=state_dir,
    ) as h:
        wall = h.http("GET", f"/{h.token}/state").json()
        assert wall["wall"]["pending"]["status"] == "await-birth"
        assert _read_pending(state_dir)["status"] == "await-birth"
        log_text = (h.log_dir / "wall.log").read_text(encoding="utf-8")
        assert log_text.count("recovered status=await-birth") == 1


def test_concurrent_launch_and_monitor_never_corrupt():
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
    state_dir = make_tmp_root("mcwalls-state-")
    clock = FakeClock(1_000_000)
    base = clock()
    # A pre-created matchable session, dated after every launch_click_ms (the
    # frozen clock never moves the cursor past it): each launch can match.
    _add_rows(
        db,
        sessions=[("s1", str(repo), base + 500)],
        inputs=[(1, "s1", {"text": "churn [secfix W2-L7]"}, base + 510)],
    )
    with serve(
        collect_state=StubTower(STATE),
        runner=FakeRunner(),
        state_dir=state_dir,
        db_path=db,
        monitor_interval_s=0.02,
        clock=clock,
    ) as h:
        stop = threading.Event()
        statuses = []
        parse_failures = []

        def writer():
            while not stop.is_set():
                r = h.http(
                    "POST",
                    f"/{h.token}/launch",
                    {"row_id": ROW["row_id"], "repo_root": str(repo)},
                )
                statuses.append(r.status)
                time.sleep(0.005)

        def canceller():
            while not stop.is_set():
                h.http("POST", f"/{h.token}/launch/cancel", {})
                time.sleep(0.005)

        def reader():
            while not stop.is_set():
                try:
                    raw = (state_dir / "pending.json").read_text(encoding="utf-8")
                except OSError:
                    continue  # not written yet (or vanished mid-rename)
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parse_failures.append(raw)
                    continue
                if isinstance(parsed, dict) and not {
                    "version",
                    "status",
                    "row_id",
                    "updated_at_ms",
                } <= set(parsed):
                    parse_failures.append(raw)
                time.sleep(0.01)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=canceller),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        time.sleep(1.5)  # let them interleave
        stop.set()
        for t in threads:
            t.join(timeout=5)
        assert not any(t.is_alive() for t in threads)
        assert parse_failures == []
        assert set(statuses) <= {200, 409}
        assert 200 in statuses and 409 in statuses
        # The file still parses with the full record shape...
        final = json.loads(
            (state_dir / "pending.json").read_text(encoding="utf-8")
        )
        assert {"version", "status", "row_id", "updated_at_ms"} <= set(final)
        # ...and the server still answers.
        assert h.http("GET", f"/{h.token}/state").status == 200


def test_corrupt_pending_quarantined_boot_continues():
    from tests.server.mcwalls_harness import (
        FakeRunner,
        StubTower,
        make_tmp_root,
        serve,
    )

    state_dir = make_tmp_root("mcwalls-state-")
    (state_dir / "pending.json").write_text("{garbage", encoding="utf-8")
    runner = FakeRunner()
    with serve(
        collect_state=StubTower(STATE),
        runner=runner,
        state_dir=state_dir,
    ) as h:
        quarantined = [p.name for p in state_dir.glob("pending.corrupt-*")]
        assert len(quarantined) == 1
        assert re.fullmatch(r"pending\.corrupt-\d+", quarantined[0])
        assert not (state_dir / "pending.json").exists()
        log_text = (h.log_dir / "wall.log").read_text(encoding="utf-8")
        assert len([ln for ln in log_text.splitlines() if "corrupt" in ln]) == 1
        assert h.server.ready is True  # boot continued past the quarantine
        wall = h.http("GET", f"/{h.token}/state").json()
        assert wall["wall"]["pending"] is None
        r = _launch(h, make_tmp_root("mcwalls-repo-"))
        assert r.status == 200  # fresh pending created
        assert _read_pending(state_dir)["status"] == "await-birth"


def test_cancel_beats_monitor_no_resurrection():
    from mc_wall.server import templates
    from tests.server.mcwalls_harness import (
        FakeClock,
        FakeRunner,
        StubTower,
        make_fixture_db,
        make_tmp_root,
        serve,
        wait_for,
    )

    repo = make_tmp_root("mcwalls-repo-")
    db = make_tmp_root("mcwalls-db-") / "db.sqlite"
    make_fixture_db(path=db)
    state_dir = make_tmp_root("mcwalls-state-")
    clock = FakeClock(1_000_000)
    runner = FakeRunner()
    with serve(
        collect_state=StubTower(STATE),
        runner=runner,
        state_dir=state_dir,
        db_path=db,
        monitor_interval_s=0.02,  # tiny interval: ticks interleave the cancel
        clock=clock,
    ) as h:
        base = clock()
        # The birth session ALREADY matches: the monitor's very next tick can
        # goal-arm while the cancel is in flight.
        _add_rows(
            db,
            sessions=[("s1", str(repo), base + 50)],
            inputs=[(1, "s1", {"text": "race [secfix W2-L7]"}, base + 60)],
        )
        r = _launch(h, repo)
        assert r.status == 200
        rc = h.http("POST", f"/{h.token}/launch/cancel", {})
        assert rc.status == 200

        def _cleared():
            pending = _read_pending(state_dir)
            return pending["status"] == "cleared" and pending["reason"] == "cancel"

        assert wait_for(_cleared)
        # Let the dust settle, then snapshot every race-sensitive artifact.
        time.sleep(0.3)
        goal_block = templates.render_file(
            "goal_block.txt",
            {
                "goal_text": ROW["goal_text"],
                "lane_tag": ROW["lane_tag"],
                "repo_root": str(repo),
            },
        )
        match_title, match_body = templates.notification(
            "match-found", {"lane_tag": ROW["lane_tag"]}
        )
        goal_copies = len([c for c in runner.of("copy") if c[1] == goal_block])
        match_notifies = len(
            [n for n in runner.of("notify") if n[1] == match_title and n[2] == match_body]
        )
        # At most the pre-cancel race window produced one of each.
        assert goal_copies <= 1
        assert match_notifies <= 1
        # Many more ticks against the cleared record: nothing new fires —
        # the status-conditional _patch makes the goal-arm a no-op on cleared.
        time.sleep(0.3)
        assert len([c for c in runner.of("copy") if c[1] == goal_block]) == goal_copies
        assert (
            len(
                [
                    n
                    for n in runner.of("notify")
                    if n[1] == match_title and n[2] == match_body
                ]
            )
            == match_notifies
        )
        pending = _read_pending(state_dir)
        assert pending["status"] == "cleared"  # terminal — never resurrected
        assert pending["reason"] == "cancel"
