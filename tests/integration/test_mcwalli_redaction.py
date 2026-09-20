"""AC-7 — token redaction over the full traffic surface.

One serve() with a distinctive token, the whole surface exercised (reads, both
auth-failure paths, the FULL handshake to goal-armed, then cancel), and every
file under the log dir (wall.log + any rotated siblings) scanned for the token
bytes and any token-bearing path. The audit-line assertion proves the exercise
produced log output, so the sweep cannot pass vacuously.

Every mc_wall / harness import is INSIDE the test functions, mirroring
tests/server/: at failure time each test must FAIL individually (exit 1)
instead of erroring at collection (exit 2).
"""


def _state_pending(h):
    r = h.http("GET", f"/{h.token}/state")
    assert r.status == 200
    return r.json()["wall"]["pending"]


def test_token_absent_from_logs_full_exercise(tmp_path, monkeypatch):
    from mc_wall.tower import collect_state
    from tests.integration.mcwalli_fixtures import (
        BIRTH_ID,
        CLICK_MS,
        mcwalli_insert_birth,
        mcwalli_row,
        mcwalli_world,
    )
    from tests.server.mcwalls_harness import FakeClock, FakeRunner, serve, wait_for

    token = "mcwalli-zq7k4wtoken"
    world = mcwalli_world(tmp_path, monkeypatch)
    tower_doc = collect_state(world.cfg)
    with serve(
        token,
        collect_state=lambda: {**tower_doc, "rows": [mcwalli_row(world.repo)]},
        runner=FakeRunner(),
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
        db_path=world.db_path,
        monitor_interval_s=0.05,
        clock=FakeClock(start_ms=CLICK_MS),
    ) as h:
        # Read surface (first GET doubles as the non-degraded precondition).
        first = h.http("GET", f"/{h.token}/state")
        assert first.status == 200
        assert first.json()["server"]["degraded"] == []
        assert h.http("GET", f"/{h.token}/").status == 200
        # Auth-failure surface: wrong token (404), evil Host (403).
        assert h.http("GET", "/mcwalli-wrong-token/").status == 404
        evil = h.http("GET", f"/{h.token}/",
                      headers={"Host": "evil.mcwalli-invalid.test"})
        assert evil.status == 403
        # Full handshake to goal-armed, then cancel.
        r = h.http("POST", f"/{h.token}/launch",
                   {"row_id": "W1-L1", "repo_root": world.repo})
        assert r.status == 200
        mcwalli_insert_birth(world.db_path, world.repo)
        assert wait_for(lambda: (p := _state_pending(h)) is not None
                        and p["status"] == "goal-armed"
                        and p["matched_session_id"] == BIRTH_ID, timeout=5.0)
        cancel = h.http("POST", f"/{h.token}/launch/cancel", {})
        assert cancel.status == 200

        # The sweep is not vacuous: the exercise produced audit output (the
        # app.py phrasing is "audit action=launch row_id=... pending=...").
        log_text = (h.log_dir / "wall.log").read_text(encoding="utf-8")
        assert "audit action=launch" in log_text

        # EVERY file under the log dir, rotated siblings included.
        log_files = sorted(p for p in h.log_dir.rglob("*") if p.is_file())
        assert log_files, "expected at least wall.log under the log dir"
        token_bytes = token.encode("utf-8")
        for p in log_files:
            data = p.read_bytes()
            assert token_bytes not in data, f"token leaked into {p.name}"
            assert b"/" + token_bytes not in data, f"token-bearing path in {p.name}"
