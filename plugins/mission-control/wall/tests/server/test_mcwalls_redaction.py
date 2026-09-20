"""T15 — redaction end-to-end sweep (AC-11).

One serve() with a distinctive token, the full traffic surface exercised
(reads, auth failures, every POST route including the conflict paths), then
EVERY file under the log dir (wall.log + any rotated siblings) is scanned:
the token string never appears, no line carries the token-bearing path, and
the repo_root string never appears.
"""


def test_no_token_or_repo_root_in_logs_after_full_exercise():
    from tests.server.mcwalls_harness import FakeRunner, StubTower, make_tmp_root, serve

    token = "redtok-abc123"
    # One tmp root owns state/, logs/, AND is the launch repo_root: a single
    # string whose absence from the logs we assert.
    tmp = make_tmp_root("mcwalls-redact-")
    row = {
        "row_id": "sweep-r1",
        "lane_tag": "[sweep R1]",
        "prompt_text": "SWEEP PROMPT 77",
        "goal_text": "SWEEP GOAL 78",
    }
    with serve(
        token,
        collect_state=StubTower({"schema_version": 1, "rows": [row]}),
        runner=FakeRunner(),
        state_dir=tmp / "state",
        log_dir=tmp / "logs",
    ) as h:
        # Read surface.
        assert h.http("GET", f"/{h.token}/state").status == 200
        assert h.http("GET", f"/{h.token}/").status == 200
        assert h.http("GET", f"/{h.token}/assets/app.js").status == 200
        # Auth-failure surface (wrong token; evil Host on the right token).
        assert h.http("GET", "/wrongtoken/").status == 404
        evil = h.http("GET", f"/{h.token}/", headers={"Host": "evil.selftest.invalid"})
        assert evil.status == 403

        # POST surface, including both 409 paths.
        launch_body = {"row_id": "sweep-r1", "repo_root": str(tmp)}
        assert h.http("POST", f"/{h.token}/launch", launch_body).status == 200
        assert h.http("POST", f"/{h.token}/launch", launch_body).status == 409
        assert h.http("POST", f"/{h.token}/copy-goal", {"row_id": "sweep-r1"}).status == 200
        assert h.http("POST", f"/{h.token}/activate-app", {}).status == 200
        assert h.http("POST", f"/{h.token}/needs-me-now", {}).status == 200
        assert h.http("POST", f"/{h.token}/launch/cancel", {}).status == 200
        # Cancelled is terminal -> re-copy only-while-awaiting-birth is a 409.
        assert h.http("POST", f"/{h.token}/launch/re-copy", {}).status == 409

        # EVERY file under the log dir, rotated siblings included.
        log_files = sorted(p for p in h.log_dir.rglob("*") if p.is_file())
        assert log_files, "expected at least wall.log under the log dir"
        scanned = {p.name: p.read_text(encoding="utf-8", errors="replace") for p in log_files}
        # The exercise produced audit output, so the scan below is not vacuous.
        assert "audit action=launch" in scanned.get("wall.log", "")

        for name, text in scanned.items():
            assert token not in text, f"token leaked into {name}"
            for line in text.splitlines():
                assert "/" + token not in line, f"token-bearing path in {name}: {line!r}"
            assert str(tmp) not in text, f"repo_root leaked into {name}"
