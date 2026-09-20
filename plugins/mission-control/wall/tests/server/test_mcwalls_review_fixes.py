"""T6-T8 review fixes — regression tests (charset, orphan race, NULL payload,
exact content-type match, endpoint runner failures).

Every mc_wall / harness import is INSIDE the test functions, mirroring
test_mcwalls_launch.py: at RED time each test must FAIL individually (exit 1)
instead of erroring at collection (exit 2).
"""

ROW = {
    "row_id": "secfix-w2-l7",
    "lane_tag": "[secfix W2-L7]",
    "prompt_text": "PROMPT TEXT 42",
    "goal_text": "GOAL TEXT 77",
    "repo_root": "/abs/repo",
}
STATE = {
    "schema_version": 1,
    "rows": [ROW],
    "owed_actions": [
        {
            "row_id": "v1",
            "kind": "verify",
            "finished_signal_ms": 100,
            "verify_cmd": "/verify x",
            "parked": False,
        }
    ],
}


def test_launch_200_charset():
    from tests.server.mcwalls_harness import FakeRunner, StubTower, serve

    runner = FakeRunner()
    with serve(collect_state=StubTower(STATE), runner=runner) as h:
        r = h.http(
            "POST",
            f"/{h.token}/launch",
            {"row_id": "secfix-w2-l7", "repo_root": "/abs/repo"},
        )
        assert r.status == 200
        assert r.headers["Content-Type"] == "application/json; charset=utf-8"


def test_recopy_during_launch_window_no_orphan():
    import json

    from tests.server.mcwalls_harness import (
        FakeClock,
        StubTower,
        make_tmp_root,
        serve,
        write_pending,
    )

    box = {}

    class ReentrantRunner:
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

    # Phase A: re-copy arrives DURING launch's first side effect. The record
    # must still reach await-birth — never orphaned in prompt-armed.
    runner = ReentrantRunner()
    with serve(collect_state=StubTower(STATE), runner=runner) as h:

        def fire_recopy():
            rr = h.http("POST", f"/{h.token}/launch/re-copy", {})
            assert rr.status == 200

        box["fire"] = fire_recopy
        r = h.http(
            "POST",
            f"/{h.token}/launch",
            {"row_id": "secfix-w2-l7", "repo_root": "/abs/repo"},
        )
        assert r.status == 200
        assert r.json()["pending"]["status"] == "await-birth"
        pending = json.loads(
            (h.state_dir / "pending.json").read_text(encoding="utf-8")
        )
        assert pending["status"] == "await-birth"  # not stuck in prompt-armed

    # Phase B: cancel lands DURING re-copy's side effects. The cleared
    # tombstone is never rewritten (updated_at_ms stays at the cancel write).
    clock = FakeClock(1_000_000)
    runner2 = ReentrantRunner()
    state_dir = make_tmp_root()
    write_pending(
        state_dir,
        {
            "status": "await-birth",
            "row_id": "secfix-w2-l7",
            "repo_root": "/abs/repo",
        },
    )
    with serve(
        collect_state=StubTower(STATE), runner=runner2, state_dir=state_dir,
        clock=clock,
    ) as h:

        def fire_cancel():
            cc = h.http("POST", f"/{h.token}/launch/cancel", {})
            assert cc.status == 200
            clock.advance(50)  # any later rewrite would show in updated_at_ms

        box["fire"] = fire_cancel
        r = h.http("POST", f"/{h.token}/launch/re-copy", {})
        assert r.status == 200
        pending = json.loads(
            (h.state_dir / "pending.json").read_text(encoding="utf-8")
        )
        assert pending["status"] == "cleared"
        assert pending["reason"] == "cancel"
        assert pending["updated_at_ms"] == 1_000_000  # the cancel write only


def test_null_payload_row_does_not_break_matcher():
    import sqlite3

    from mc_wall.server.matcher import Matcher
    from tests.server.mcwalls_harness import make_fixture_db

    db, _ = make_fixture_db(
        sessions=[("s1", "/r", 2000)],
        inputs=[(1, "s1", {"text": "real [tag] row"}, 3000)],
    )
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO session_input VALUES (2, 's1', 'sendText', NULL, 3500)"
    )
    conn.commit()
    conn.close()

    m = Matcher(db)
    hits = m.find_candidates("[tag]", 1000)  # must not raise on the NULL row
    assert [h["input_id"] for h in hits] == [1]
    assert m.find_duplicate_paste("s1", "0" * 64, 1000) is False


def test_content_type_exact_match():
    from tests.server.mcwalls_harness import FakeRunner, StubTower, serve

    runner = FakeRunner()
    with serve(collect_state=StubTower(STATE), runner=runner) as h:
        r = h.http(
            "POST",
            f"/{h.token}/launch",
            {"row_id": "secfix-w2-l7", "repo_root": "/abs/repo"},
            headers={"Content-Type": "application/jsonx"},
        )
        assert r.status == 415
        assert r.json()["error"] == "unsupported-media-type"
        assert runner.calls == []
        # Parameters after the media type remain tolerated.
        r2 = h.http(
            "POST",
            f"/{h.token}/launch",
            {"row_id": "secfix-w2-l7", "repo_root": "/abs/repo"},
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        assert r2.status == 200


def test_endpoint_runner_failure_returns_json_500():
    from tests.server.mcwalls_harness import StubTower, serve

    class ExplodingRunner:
        def copy(self, text):
            raise RuntimeError("boom-copy")

        def open_url(self, url):
            raise RuntimeError("boom-url")

        def open_app(self, name):
            raise RuntimeError("boom-app")

        def notify(self, title, body):
            raise RuntimeError("boom-notify")

    with serve(collect_state=StubTower(STATE), runner=ExplodingRunner()) as h:
        for path, body in (
            ("copy-goal", {"row_id": "secfix-w2-l7"}),
            ("activate-app", {}),
            ("needs-me-now", {}),
        ):
            r = h.http("POST", f"/{h.token}/{path}", body)
            assert r.status == 500
            payload = r.json()  # a JSON reply arrived — connection NOT dropped
            assert payload["ok"] is False
            assert payload["error"] == "internal-error"
