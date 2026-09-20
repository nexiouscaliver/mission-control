"""AC-1/AC-2 — the frozen tower contract served by the real server.

Shape tests inject the raw ``collect_state(world.cfg)`` document (no "rows"
view): /state is a verbatim tower passthrough plus the server's own "wall" key.

Every mc_wall / harness import is INSIDE the test functions, mirroring
tests/server/: at failure time each test must FAIL individually (exit 1)
instead of erroring at collection (exit 2).
"""


def test_state_serves_frozen_contract(tmp_path, monkeypatch):
    from mc_wall.tower import collect_state
    from mc_wall.tower import contract
    from tests.integration.mcwalli_fixtures import MASTER_ID, UNMAPPED_ID, mcwalli_world
    from tests.server.mcwalls_harness import serve

    world = mcwalli_world(tmp_path, monkeypatch)
    tower_doc = collect_state(world.cfg)
    with serve(collect_state=lambda: tower_doc) as h:
        r = h.http("GET", f"/{h.token}/state")
        assert r.status == 200
        body = r.json()
        wall = body.pop("wall")
        # Non-degraded precondition FIRST: the content pins below are only
        # meaningful when nothing silently failed open.
        assert body["server"]["degraded"] == []
        contract.assert_shape(body)
        assert body["schema_version"] == 1
        assert wall == {"pending": None}
        # Content pins (silent-degradation guards).
        assert body["programs"][0]["master"]["session_id"] == MASTER_ID
        lane = body["programs"][0]["lanes"][0]
        assert lane["row_id"] == "W1-L1"
        assert lane["slug"] == "mcwalli-slug"
        unmapped = body["sessions_unmapped"]
        assert len(unmapped) == 1
        assert unmapped[0]["id"] == UNMAPPED_ID


def test_wrong_host_403(tmp_path, monkeypatch):
    from mc_wall.tower import collect_state
    from tests.integration.mcwalli_fixtures import mcwalli_world
    from tests.server.mcwalls_harness import serve

    world = mcwalli_world(tmp_path, monkeypatch)
    tower_doc = collect_state(world.cfg)
    with serve(collect_state=lambda: tower_doc) as h:
        evil = h.http("GET", f"/{h.token}/state",
                      headers={"Host": "evil.mcwalli-invalid.test"})
        assert evil.status == 403
        assert evil.headers["Content-Type"] == "text/plain"
        # Control: the harness's default Host header is localhost:<port>.
        control = h.http("GET", f"/{h.token}/state")
        assert control.status == 200
