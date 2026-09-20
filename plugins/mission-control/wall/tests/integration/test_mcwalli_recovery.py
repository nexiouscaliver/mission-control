"""AC-5/AC-6 — crash-restart recovery through the real state dir.

The serve() context exit is the "crash": whatever reached pending.json is the
only survivor, and a fresh create_server on the SAME state_dir/log_dir/db_path
must resume the handshake from there.

Every mc_wall / harness import is INSIDE the test functions, mirroring
tests/server/: at failure time each test must FAIL individually (exit 1)
instead of erroring at collection (exit 2).
"""


def _state_pending(h):
    r = h.http("GET", f"/{h.token}/state")
    assert r.status == 200
    return r.json()["wall"]["pending"]


def _goal_armed(h, session_id):
    pending = _state_pending(h)
    return (pending is not None
            and pending.get("status") == "goal-armed"
            and pending.get("matched_session_id") == session_id)


def test_restart_resumes_await_birth(tmp_path, monkeypatch):
    import json

    from mc_wall.tower import collect_state
    from tests.integration.mcwalli_fixtures import (
        BIRTH_ID,
        CLICK_MS,
        mcwalli_insert_birth,
        mcwalli_row,
        mcwalli_world,
    )
    from tests.server.mcwalls_harness import FakeClock, FakeRunner, serve, wait_for

    world = mcwalli_world(tmp_path, monkeypatch)
    tower_doc = collect_state(world.cfg)
    state_dir, log_dir = tmp_path / "state", tmp_path / "logs"
    clock = FakeClock(start_ms=CLICK_MS)
    collect = lambda: {**tower_doc, "rows": [mcwalli_row(world.repo)]}  # noqa: E731

    # Server A: the launch persists await-birth, then A dies.
    with serve("mcwalli-token", collect_state=collect, runner=FakeRunner(),
               state_dir=state_dir, log_dir=log_dir, db_path=world.db_path,
               clock=clock) as a:
        first = a.http("GET", f"/{a.token}/state").json()
        assert first["server"]["degraded"] == []  # non-degraded precondition
        r = a.http("POST", f"/{a.token}/launch",
                   {"row_id": "W1-L1", "repo_root": world.repo})
        assert r.status == 200
        assert r.json()["pending"]["status"] == "await-birth"
    # A is down: pin the persisted pre-B state ON DISK, not over HTTP — B's
    # monitor starts inside create_server and, with the birth row in place,
    # can complete the whole match before a GET lands.
    persisted = json.loads((state_dir / "pending.json").read_text(encoding="utf-8"))
    assert persisted["status"] == "await-birth"
    # Only now is the birth session born.
    mcwalli_insert_birth(world.db_path, world.repo)
    # Server B: same state_dir/log_dir/db_path — recovery from disk alone.
    with serve("mcwalli-token", collect_state=collect, runner=FakeRunner(),
               state_dir=state_dir, log_dir=log_dir, db_path=world.db_path,
               clock=clock) as b:
        assert wait_for(lambda: _goal_armed(b, BIRTH_ID), timeout=5.0)


def test_boot_promotes_prompt_armed(tmp_path, monkeypatch):
    import hashlib

    from mc_wall.server.pending import STATUS_PROMPT_ARMED, PendingRecord
    from mc_wall.tower import collect_state
    from tests.integration.mcwalli_fixtures import (
        BIRTH_ID,
        CLICK_MS,
        mcwalli_insert_birth,
        mcwalli_row,
        mcwalli_world,
    )
    from tests.server.mcwalls_harness import FakeClock, FakeRunner, serve, wait_for, write_pending

    world = mcwalli_world(tmp_path, monkeypatch)
    tower_doc = collect_state(world.cfg)
    state_dir, log_dir = tmp_path / "state", tmp_path / "logs"
    # A crash between the durable create and the promote left prompt-armed on
    # disk; every other PendingRecord field takes its dataclass default.
    write_pending(state_dir, PendingRecord(
        status=STATUS_PROMPT_ARMED,
        row_id="W1-L1",
        lane_tag="mcwalli-lane",
        repo_root=world.repo,
        prompt_sha256=hashlib.sha256(b"MCWALLI PROMPT").hexdigest(),
        launch_click_ms=CLICK_MS,
    ).to_dict())
    mcwalli_insert_birth(world.db_path, world.repo)
    with serve("mcwalli-token",
               collect_state=lambda: {**tower_doc, "rows": [mcwalli_row(world.repo)]},
               runner=FakeRunner(), state_dir=state_dir, log_dir=log_dir,
               db_path=world.db_path,
               clock=FakeClock(start_ms=CLICK_MS)) as h:
        first = h.http("GET", f"/{h.token}/state").json()
        assert first["server"]["degraded"] == []  # non-degraded precondition
        # Boot recovery ran at create_server, before any request above.
        log_text = (h.log_dir / "wall.log").read_text(encoding="utf-8")
        assert "recovered status=await-birth row_id=W1-L1" in log_text
        assert wait_for(lambda: _goal_armed(h, BIRTH_ID), timeout=5.0)
