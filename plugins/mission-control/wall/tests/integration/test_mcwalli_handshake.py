"""AC-3/AC-4 — the launch handshake over the real tower + real monitor.

Handshake tests serve ``{**tower_doc, "rows": [mcwalli_row(...)]}``: the rows
view is the documented B3 bridge (production has no rows producer yet — AC-4
pins that gap with the raw tower doc). The frozen FakeClock keeps every
advisory/canary delta at 0 and the matcher cutoffs strictly below T_BIRTH.

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


def test_launch_handshake_to_goal_armed(tmp_path, monkeypatch):
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
    runner = FakeRunner()
    with serve(
        "mcwalli-token",
        collect_state=lambda: {**tower_doc, "rows": [mcwalli_row(world.repo)]},
        runner=runner,
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
        db_path=world.db_path,
        monitor_interval_s=0.05,
        clock=FakeClock(start_ms=CLICK_MS),
    ) as h:
        # Non-degraded precondition FIRST (content-guard).
        first = h.http("GET", f"/{h.token}/state").json()
        assert first["server"]["degraded"] == []
        r = h.http("POST", f"/{h.token}/launch",
                   {"row_id": "W1-L1", "repo_root": world.repo})
        assert r.status == 200
        assert r.json()["pending"]["status"] == "await-birth"
        # The birth session appears only now: one paste serves the tower's tag
        # scan AND the matcher's payload-text confirmation.
        mcwalli_insert_birth(world.db_path, world.repo)
        assert wait_for(lambda: _goal_armed(h, BIRTH_ID), timeout=5.0)
        goal_copies = [c for c in runner.of("copy") if "MCWALLI GOAL" in c[1]]
        assert goal_copies, "the goal block was never copied to the runner"


def test_launch_raw_tower_doc_unknown_row(tmp_path, monkeypatch):
    from mc_wall.tower import collect_state
    from tests.integration.mcwalli_fixtures import mcwalli_world
    from tests.server.mcwalls_harness import FakeRunner, serve

    world = mcwalli_world(tmp_path, monkeypatch)
    tower_doc = collect_state(world.cfg)
    # db_path omitted (None) -> no monitor; state_dir + runner kept so the
    # route is fully configured and cannot 500 before row resolution.
    with serve(
        "mcwalli-token",
        collect_state=lambda: tower_doc,  # raw tower doc: no "rows" producer
        runner=FakeRunner(),
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
    ) as h:
        r = h.http("POST", f"/{h.token}/launch",
                   {"row_id": "W1-L1", "repo_root": world.repo})
        assert r.status == 404
        assert r.json()["error"] == "unknown-row"
        assert not (h.state_dir / "pending.json").exists()
