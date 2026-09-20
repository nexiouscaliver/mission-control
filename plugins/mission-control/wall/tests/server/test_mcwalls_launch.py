"""T6 — POST pipeline guards + launch / cancel / re-copy.

Every mc_wall / harness import is INSIDE the test functions, mirroring
test_mcwalls_state.py: at RED time each test must FAIL individually (exit 1)
instead of erroring at collection (exit 2).
"""

ROW = {
    "row_id": "secfix-w2-l7",
    "lane_tag": "[secfix W2-L7]",
    "prompt_text": "PROMPT TEXT 42",
    "goal_text": "GOAL",
}
STATE = {"schema_version": 1, "rows": [ROW]}


def test_launch_happy_path():
    import hashlib
    import json
    import time

    from tests.server.mcwalls_harness import FakeRunner, StubTower, serve

    runner = FakeRunner()
    with serve(collect_state=StubTower(STATE), runner=runner) as h:
        r = h.http(
            "POST",
            f"/{h.token}/launch",
            {"row_id": "secfix-w2-l7", "repo_root": "/abs/repo"},
        )
        assert r.status == 200
        body = r.json()
        assert body["ok"] is True
        assert body["warnings"] == []
        assert body["pending"]["status"] == "await-birth"
        # prompt_block.txt is the identity wrapper -> copy is the prompt verbatim;
        # the deep link percent-encodes every path byte (AC-17).
        assert runner.calls == [
            ("copy", "PROMPT TEXT 42"),
            ("open_url", "zcode://workspace/open?path=%2Fabs%2Frepo"),
        ]
        pending = json.loads(
            (h.state_dir / "pending.json").read_text(encoding="utf-8")
        )
        assert pending["status"] == "await-birth"
        assert (
            pending["prompt_sha256"]
            == hashlib.sha256("PROMPT TEXT 42".encode("utf-8")).hexdigest()
        )
        assert abs(pending["launch_click_ms"] - int(time.time() * 1000)) < 5000
        assert pending["row_id"] == "secfix-w2-l7"
        assert pending["lane_tag"] == "[secfix W2-L7]"
        assert pending["repo_root"] == "/abs/repo"


def test_launch_conflict_409():
    from tests.server.mcwalls_harness import FakeRunner, StubTower, serve

    runner = FakeRunner()
    with serve(collect_state=StubTower(STATE), runner=runner) as h:
        r1 = h.http(
            "POST",
            f"/{h.token}/launch",
            {"row_id": "secfix-w2-l7", "repo_root": "/abs/repo"},
        )
        assert r1.status == 200
        r2 = h.http(
            "POST",
            f"/{h.token}/launch",
            {"row_id": "secfix-w2-l7", "repo_root": "/abs/repo"},
        )
        assert r2.status == 409
        body = r2.json()
        assert body["ok"] is False
        assert body["error"] == "launch-pending"
        assert body["pending"]["status"] == "await-birth"
        # AC-18: the runner is untouched — exactly the 2 calls of launch #1.
        assert len(runner.calls) == 2


def test_launch_unknown_row():
    from tests.server.mcwalls_harness import FakeRunner, StubTower, serve

    runner = FakeRunner()
    with serve(collect_state=StubTower(STATE), runner=runner) as h:
        r = h.http(
            "POST", f"/{h.token}/launch", {"row_id": "nope", "repo_root": "/abs/repo"}
        )
        assert r.status == 404
        body = r.json()
        assert body["ok"] is False
        assert body["error"] == "unknown-row"
        # AC-19: nothing copied, nothing persisted.
        assert runner.calls == []
        assert not (h.state_dir / "pending.json").exists()


def test_launch_validation_400():
    from tests.server.mcwalls_harness import FakeRunner, StubTower, serve

    runner = FakeRunner()
    with serve(collect_state=StubTower(STATE), runner=runner) as h:
        bad_bodies = (
            {"repo_root": "/abs/repo"},  # row_id missing
            {"row_id": 7, "repo_root": "/abs/repo"},  # row_id non-string
            {"row_id": "secfix-w2-l7", "repo_root": "rel/path"},  # not absolute
        )
        for bad in bad_bodies:
            r = h.http("POST", f"/{h.token}/launch", bad)
            assert r.status == 400
            assert r.json()["error"] == "bad-request"
        assert runner.calls == []
        assert not (h.state_dir / "pending.json").exists()


def test_launch_content_type_415():
    from tests.server.mcwalls_harness import FakeRunner, StubTower, serve

    runner = FakeRunner()
    with serve(collect_state=StubTower(STATE), runner=runner) as h:
        r = h.http(
            "POST",
            f"/{h.token}/launch",
            {"row_id": "secfix-w2-l7", "repo_root": "/abs/repo"},
            headers={"Content-Type": "text/plain"},
        )
        assert r.status == 415
        body = r.json()
        assert body["ok"] is False
        assert body["error"] == "unsupported-media-type"
        assert runner.calls == []


def test_launch_body_guards_413_411_400():
    from tests.server.mcwalls_harness import StubTower, raw_request, serve

    with serve(collect_state=StubTower(STATE)) as h:
        port = h.server.server_address[1]
        path = f"/{h.token}/launch"

        # No Content-Length (body sent anyway) -> 413, decided before reading.
        raw = (
            f"POST {path} HTTP/1.1\r\nHost: localhost\r\n"
            "Content-Type: application/json\r\n\r\n" + "x" * 16
        ).encode("ascii")
        assert b" 413 " in raw_request(port, raw).split(b"\r\n", 1)[0]

        # Content-Length: 70000 with no body sent -> 413 returned without waiting.
        raw = (
            f"POST {path} HTTP/1.1\r\nHost: localhost\r\n"
            "Content-Type: application/json\r\nContent-Length: 70000\r\n\r\n"
        ).encode("ascii")
        assert b" 413 " in raw_request(port, raw).split(b"\r\n", 1)[0]

        # Transfer-Encoding present (any value) -> 411, before length checks.
        raw = (
            f"POST {path} HTTP/1.1\r\nHost: localhost\r\n"
            "Content-Type: application/json\r\n"
            "Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n"
        ).encode("ascii")
        assert b" 411 " in raw_request(port, raw).split(b"\r\n", 1)[0]

        # Well-formed length + non-JSON body -> 400.
        payload = b"{not json"
        raw = (
            f"POST {path} HTTP/1.1\r\nHost: localhost\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n"
        ).encode("ascii") + payload
        assert b" 400 " in raw_request(port, raw).split(b"\r\n", 1)[0]


def test_cancel_idempotent_then_new_launch():
    from tests.server.mcwalls_harness import FakeRunner, StubTower, serve

    runner = FakeRunner()
    with serve(collect_state=StubTower(STATE), runner=runner) as h:
        r1 = h.http(
            "POST",
            f"/{h.token}/launch",
            {"row_id": "secfix-w2-l7", "repo_root": "/abs/repo"},
        )
        assert r1.status == 200

        c1 = h.http("POST", f"/{h.token}/launch/cancel", {})
        assert c1.status == 200
        assert c1.json()["ok"] is True
        assert c1.json()["pending"]["status"] == "cleared"
        assert c1.json()["pending"]["reason"] == "cancel"

        c2 = h.http("POST", f"/{h.token}/launch/cancel", {})
        assert c2.status == 200
        assert c2.json()["ok"] is True
        assert c2.json()["pending"] is None

        r2 = h.http(
            "POST",
            f"/{h.token}/launch",
            {"row_id": "secfix-w2-l7", "repo_root": "/abs/repo"},
        )
        assert r2.status == 200


def test_recopy_await_vs_goal_armed():
    import json

    from tests.server.mcwalls_harness import (
        FakeRunner,
        StubTower,
        make_tmp_root,
        serve,
        write_pending,
    )

    runner = FakeRunner()
    with serve(collect_state=StubTower(STATE), runner=runner) as h:
        r1 = h.http(
            "POST",
            f"/{h.token}/launch",
            {"row_id": "secfix-w2-l7", "repo_root": "/abs/repo"},
        )
        assert r1.status == 200
        click_ms = json.loads(
            (h.state_dir / "pending.json").read_text(encoding="utf-8")
        )["launch_click_ms"]

        r2 = h.http("POST", f"/{h.token}/launch/re-copy", {})
        assert r2.status == 200
        assert r2.json()["ok"] is True
        assert runner.of("copy") == [
            ("copy", "PROMPT TEXT 42"),
            ("copy", "PROMPT TEXT 42"),
        ]
        assert len(runner.of("open_url")) == 2
        after = json.loads(
            (h.state_dir / "pending.json").read_text(encoding="utf-8")
        )
        assert after["status"] == "await-birth"
        assert after["launch_click_ms"] == click_ms
        assert after["flag"] is None

    # goal-armed -> 409, clipboard untouched, on a fresh server instance.
    runner2 = FakeRunner()
    state_dir = make_tmp_root()
    write_pending(
        state_dir,
        {"status": "goal-armed", "row_id": "secfix-w2-l7", "repo_root": "/abs/repo"},
    )
    with serve(
        collect_state=StubTower(STATE), runner=runner2, state_dir=state_dir
    ) as h:
        r = h.http("POST", f"/{h.token}/launch/re-copy", {})
        assert r.status == 409
        body = r.json()
        assert body["ok"] is False
        assert body["error"] == "not-await-birth"
        assert runner2.calls == []


def test_recopy_clears_ambiguous_flag():
    from tests.server.mcwalls_harness import (
        FakeRunner,
        StubTower,
        make_tmp_root,
        serve,
        write_pending,
    )

    runner = FakeRunner()
    state_dir = make_tmp_root()
    write_pending(
        state_dir,
        {
            "status": "await-birth",
            "row_id": "secfix-w2-l7",
            "repo_root": "/abs/repo",
            "flag": "ambiguous",
        },
    )
    with serve(
        collect_state=StubTower(STATE), runner=runner, state_dir=state_dir
    ) as h:
        r = h.http("POST", f"/{h.token}/launch/re-copy", {})
        assert r.status == 200
        assert r.json()["ok"] is True
        assert r.json()["pending"]["flag"] is None
