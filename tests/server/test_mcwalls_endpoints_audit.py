"""T8 — copy-goal, activate-app, needs-me-now, POST audit lines.

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
            "finished_signal_ms": 5000,
            "verify_cmd": "/verify cmd",
            "parked": False,
        }
    ],
}


def test_copy_goal_renders_block():
    from mc_wall.server import templates
    from tests.server.mcwalls_harness import FakeRunner, StubTower, serve

    runner = FakeRunner()
    with serve(collect_state=StubTower(STATE), runner=runner) as h:
        r = h.http("POST", f"/{h.token}/copy-goal", {"row_id": "secfix-w2-l7"})
        assert r.status == 200
        body = r.json()
        assert body["ok"] is True
        assert body["copied"] == "goal"
        # The block's {repo_root} renders from the ROW's own data — the
        # request carries only row_id (T8 design note).
        expected = templates.render_file(
            "goal_block.txt",
            {
                "goal_text": "GOAL TEXT 77",
                "lane_tag": "[secfix W2-L7]",
                "repo_root": "/abs/repo",
            },
        )
        assert runner.of("copy") == [("copy", expected)]

        # Unknown row -> 404, clipboard untouched.
        r2 = h.http("POST", f"/{h.token}/copy-goal", {"row_id": "nope"})
        assert r2.status == 404
        assert r2.json()["error"] == "unknown-row"
        assert len(runner.of("copy")) == 1


def test_activate_app_argv():
    from tests.server.mcwalls_harness import FakeRunner, StubTower, serve

    runner = FakeRunner()
    with serve(collect_state=StubTower(STATE), runner=runner) as h:
        r = h.http("POST", f"/{h.token}/activate-app", {})
        assert r.status == 200
        body = r.json()
        assert body["ok"] is True
        # App activation only — never a URL (AC-22).
        assert runner.calls == [("open_app", "ZCode")]
        assert runner.of("open_url") == []


def test_needs_me_now_merge_oldest():
    from tests.server.mcwalls_harness import FakeRunner, StubTower, serve

    state = {
        "schema_version": 1,
        "rows": [],
        "owed_actions": [
            {
                "row_id": "m1",
                "kind": "merge",
                "finished_signal_ms": 1000,
                "mr_link": "https://mr/1",
                "parked": False,
            },
            {
                "row_id": "v1",
                "kind": "verify",
                "finished_signal_ms": 2000,
                "verify_cmd": "/verify s",
                "parked": False,
            },
        ],
    }
    runner = FakeRunner()
    with serve(collect_state=StubTower(state), runner=runner) as h:
        r = h.http("POST", f"/{h.token}/needs-me-now", {})
        assert r.status == 200
        body = r.json()
        assert body["ok"] is True
        action = body["action"]
        assert set(action) == {"row_id", "kind", "copied", "finished_signal_ms"}
        assert action["row_id"] == "m1"
        assert action["kind"] == "merge"
        assert action["copied"] == "https://mr/1"
        assert action["finished_signal_ms"] == 1000
        # merge copies the link and does NOT raise the app (AC-23a).
        assert runner.of("copy") == [("copy", "https://mr/1")]
        assert runner.of("open_app") == []


def test_needs_me_now_verify_oldest():
    from tests.server.mcwalls_harness import FakeRunner, StubTower, serve

    state = {
        "schema_version": 1,
        "rows": [],
        "owed_actions": [
            {
                "row_id": "v1",
                "kind": "verify",
                "finished_signal_ms": 1000,
                "verify_cmd": "/verify s",
                "parked": False,
            },
            {
                "row_id": "m1",
                "kind": "merge",
                "finished_signal_ms": 2000,
                "mr_link": "https://mr/2",
                "parked": False,
            },
        ],
    }
    runner = FakeRunner()
    with serve(collect_state=StubTower(state), runner=runner) as h:
        r = h.http("POST", f"/{h.token}/needs-me-now", {})
        assert r.status == 200
        action = r.json()["action"]
        assert action["row_id"] == "v1"
        assert action["kind"] == "verify"
        assert action["copied"] == "/verify s"
        # verify copies the command AND raises the app (AC-23b).
        assert runner.of("copy") == [("copy", "/verify s")]
        assert runner.of("open_app") == [("open_app", "ZCode")]


def test_needs_me_now_parked_excluded():
    from tests.server.mcwalls_harness import FakeRunner, StubTower, serve

    state = {
        "schema_version": 1,
        "rows": [],
        "owed_actions": [
            {
                "row_id": "p1",
                "kind": "merge",
                "finished_signal_ms": 500,
                "mr_link": "https://mr/parked",
                "parked": True,
            },
            {
                "row_id": "n1",
                "kind": "merge",
                "finished_signal_ms": 900,
                "mr_link": "https://mr/next",
                "parked": False,
            },
        ],
    }
    runner = FakeRunner()
    with serve(collect_state=StubTower(state), runner=runner) as h:
        r = h.http("POST", f"/{h.token}/needs-me-now", {})
        assert r.status == 200
        action = r.json()["action"]
        # The parked entry has the SMALLEST signal but is excluded (AC-23c).
        assert action["row_id"] == "n1"
        assert action["copied"] == "https://mr/next"
        assert runner.of("copy") == [("copy", "https://mr/next")]


def test_needs_me_now_unknown_kind_skipped():
    from tests.server.mcwalls_harness import FakeRunner, StubTower, serve

    state = {
        "schema_version": 1,
        "rows": [],
        "owed_actions": [
            {
                "row_id": "r1",
                "kind": "rebase",
                "finished_signal_ms": 100,
                "parked": False,
            },
            {
                "row_id": "v2",
                "kind": "verify",
                "finished_signal_ms": 200,
                "verify_cmd": "/verify v",
                "parked": False,
            },
        ],
    }
    runner = FakeRunner()
    with serve(collect_state=StubTower(state), runner=runner) as h:
        r = h.http("POST", f"/{h.token}/needs-me-now", {})
        assert r.status == 200
        action = r.json()["action"]
        assert action["row_id"] == "v2"
        assert runner.of("copy") == [("copy", "/verify v")]
        # Exactly ONE skip audit line, naming the reason and the row (AC-23d).
        log_text = (h.log_dir / "wall.log").read_text(encoding="utf-8")
        skip_lines = [
            ln for ln in log_text.splitlines() if "needs-me-now-skipped" in ln
        ]
        assert len(skip_lines) == 1
        assert "reason=unknown-kind" in skip_lines[0]
        assert "row_id=r1" in skip_lines[0]


def test_needs_me_now_tie_break():
    from tests.server.mcwalls_harness import FakeRunner, StubTower, serve

    state = {
        "schema_version": 1,
        "rows": [],
        "owed_actions": [
            {
                "row_id": "b",
                "kind": "verify",
                "finished_signal_ms": 700,
                "verify_cmd": "/verify b",
                "parked": False,
            },
            {
                "row_id": "a",
                "kind": "verify",
                "finished_signal_ms": 700,
                "verify_cmd": "/verify a",
                "parked": False,
            },
        ],
    }
    runner = FakeRunner()
    with serve(collect_state=StubTower(state), runner=runner) as h:
        r = h.http("POST", f"/{h.token}/needs-me-now", {})
        assert r.status == 200
        action = r.json()["action"]
        # Pinned tie-break: same finished_signal_ms -> lower row_id wins (AC-23e).
        assert action["row_id"] == "a"
        assert action["copied"] == "/verify a"


def test_needs_me_now_empty():
    from tests.server.mcwalls_harness import FakeRunner, StubTower, serve

    runner = FakeRunner()
    tower = StubTower({"schema_version": 1, "rows": [], "owed_actions": []})
    with serve(collect_state=tower, runner=runner) as h:
        r = h.http("POST", f"/{h.token}/needs-me-now", {})
        assert r.status == 200
        body = r.json()
        assert body == {"ok": True, "action": None}
        assert runner.calls == []

        # Missing owed_actions key entirely -> same empty answer (AC-23f).
        tower.state = {"schema_version": 1, "rows": []}
        r2 = h.http("POST", f"/{h.token}/needs-me-now", {})
        assert r2.status == 200
        assert r2.json() == {"ok": True, "action": None}
        assert runner.calls == []


def test_audit_lines_pinned_shape():
    import json

    from tests.server.mcwalls_harness import FakeRunner, StubTower, serve

    runner = FakeRunner()
    with serve(collect_state=StubTower(STATE), runner=runner) as h:
        # Full AC-17..AC-25 sequence against ONE server.
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
        r3 = h.http(
            "POST",
            f"/{h.token}/launch",
            {"row_id": "nope", "repo_root": "/abs/repo"},
        )
        assert r3.status == 404
        r4 = h.http("POST", f"/{h.token}/launch", {"repo_root": "rel/path"})
        assert r4.status == 400
        c1 = h.http("POST", f"/{h.token}/launch/cancel", {})
        assert c1.status == 200
        c2 = h.http("POST", f"/{h.token}/launch/cancel", {})
        assert c2.status == 200
        r5 = h.http(
            "POST",
            f"/{h.token}/launch",
            {"row_id": "secfix-w2-l7", "repo_root": "/abs/repo"},
        )
        assert r5.status == 200
        rc = h.http("POST", f"/{h.token}/launch/re-copy", {})
        assert rc.status == 200
        cg = h.http("POST", f"/{h.token}/copy-goal", {"row_id": "secfix-w2-l7"})
        assert cg.status == 200
        aa = h.http("POST", f"/{h.token}/activate-app", {})
        assert aa.status == 200
        nm = h.http("POST", f"/{h.token}/needs-me-now", {})
        assert nm.status == 200

        log_text = (h.log_dir / "wall.log").read_text(encoding="utf-8")
        lines = log_text.splitlines()
        audit_lines = [ln for ln in lines if "audit action=" in ln]
        # Exactly one audit line per successful POST (8 x 200 above).
        assert len(audit_lines) == 8
        actions = [
            ln.split("audit action=", 1)[1].split()[0] for ln in audit_lines
        ]
        assert actions == [
            "launch",
            "cancel",
            "cancel",
            "launch",
            "re-copy",
            "copy-goal",
            "activate-app",
            "needs-me-now",
        ]
        # Every pending= payload parses as JSON with the pinned audit triple.
        for ln in audit_lines:
            if "pending=" in ln:
                payload = json.loads(ln.split("pending=", 1)[1])
                assert set(payload) == {"status", "row_id", "flag"}
        # Nothing sensitive ever reaches the log (AC-28 redaction half).
        for forbidden in (
            h.token,
            "PROMPT TEXT 42",
            "GOAL TEXT 77",
            "/abs/repo",
            "[secfix W2-L7]",
        ):
            assert forbidden not in log_text
