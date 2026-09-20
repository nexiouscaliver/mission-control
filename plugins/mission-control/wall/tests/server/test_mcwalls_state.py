"""T5 — GET/HEAD /state: verbatim tower passthrough, no caching, degradation.

Every mc_wall / harness import is INSIDE the test functions, mirroring
test_mcwalls_boot.py: at RED time each test must FAIL individually (exit 1)
instead of erroring at collection (exit 2).
"""


def test_state_verbatim_plus_wall_key():
    from tests.server.mcwalls_harness import StubTower, serve

    tower = {"schema_version": 7, "rows": [{"row_id": "a"}], "extra": {"n": [1, 2]}}
    with serve(collect_state=StubTower(tower)) as h:
        r = h.http("GET", f"/{h.token}/state")
        assert r.status == 200
        assert r.headers["Content-Type"] == "application/json; charset=utf-8"
        assert r.headers["Cache-Control"] == "no-store"
        body = r.json()
        assert set(body.keys()) == set(tower.keys()) | {"wall"}
        assert body["schema_version"] == 7  # type/value identical passthrough
        assert body["rows"] == tower["rows"]
        assert body["extra"] == tower["extra"]
        assert body["wall"] == {"pending": None}


def test_state_degraded_on_exception():
    from tests.server.mcwalls_harness import StubTower, serve

    with serve(collect_state=StubTower(exc=RuntimeError("boom"))) as h:
        r = h.http("GET", f"/{h.token}/state")
        assert r.status == 503
        body = r.json()
        assert set(body.keys()) == {
            "ok",
            "degraded",
            "error",
            "detail",
            "occurred_at_ms",
            "wall",
        }
        assert body["ok"] is False
        assert body["degraded"] is True
        assert body["error"] == "collect_state_failed"
        assert body["detail"] == "RuntimeError"
        assert isinstance(body["occurred_at_ms"], int)
        assert body["wall"] == {"pending": None}
        log = (h.log_dir / "wall.log").read_text(encoding="utf-8")
        assert "boom" not in log  # exception text never logged — type name only


def test_state_no_caching():
    from tests.server.mcwalls_harness import StubTower, serve

    stub = StubTower({"schema_version": 1, "rows": []})
    with serve(collect_state=stub) as h:
        responses = [h.http("GET", f"/{h.token}/state") for _ in range(5)]
        assert stub.calls == 5  # collector runs on EVERY request
        assert all(r.status == 200 for r in responses)
        bodies = [r.json() for r in responses]
        assert all(b == bodies[0] for b in bodies)


def test_state_degraded_on_tower_import_failure_server_stays_up():
    from tests.server.mcwalls_harness import StubTower, serve

    stub = StubTower(exc=ImportError("No module named 'mc_wall.tower'"))
    with serve(collect_state=stub) as h:
        r1 = h.http("GET", f"/{h.token}/state")
        assert r1.status == 503
        assert r1.json()["degraded"] is True
        assert r1.json()["detail"] == "ImportError"

        # Same server instance: tower import recovers -> /state serves again.
        stub.exc = None
        stub.state = {"schema_version": 1}
        r2 = h.http("GET", f"/{h.token}/state")
        assert r2.status == 200
        assert r2.json()["schema_version"] == 1


def test_head_state():
    from tests.server.mcwalls_harness import StubTower, serve

    with serve(collect_state=StubTower({"schema_version": 1})) as h:
        r = h.http("HEAD", f"/{h.token}/state")
        assert r.status == 200
        assert r.headers["Content-Type"] == "application/json; charset=utf-8"
        assert r.headers["Cache-Control"] == "no-store"
        assert r.body == b""


def test_state_includes_live_pending():
    from mc_wall.server.pending import STATUS_AWAIT_BIRTH, PendingRecord, PendingStore
    from tests.server.mcwalls_harness import StubTower, make_tmp_root, serve

    state_dir = make_tmp_root()
    PendingStore(state_dir).create(PendingRecord(status=STATUS_AWAIT_BIRTH, row_id="r1"))
    with serve(collect_state=StubTower({"schema_version": 1}), state_dir=state_dir) as h:
        r = h.http("GET", f"/{h.token}/state")
        assert r.status == 200
        assert r.json()["wall"]["pending"]["status"] == "await-birth"
