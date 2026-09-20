"""T13 — CLI lifecycle: start/stop/restart/status/log/open.

Same discipline as T12: bin/mc-wall is loaded via the harness load_cli()
SourceFileLoader INSIDE each test so a missing file fails the item, not the
collection. The two roots (wall_home / home) are ALWAYS injected tmp dirs and
the executor is ALWAYS a FakeExecutor — no test ever touches ~/.zcode,
~/Library/LaunchAgents, a real launchctl, or a real `open`. The status probe
does make a real HTTP GET, but only against the harness serve() server bound
to 127.0.0.1 on an ephemeral port.
"""

import io
import json
import os

PLIST_LABEL = "ai.zcode.mc-wall"
PRINT_ARGV = ["launchctl", "print", "gui/%d/" % os.getuid() + PLIST_LABEL]
BOOTOUT_ARGV = ["launchctl", "bootout", "gui/%d/" % os.getuid() + PLIST_LABEL]


def test_start_fallback_kickstart():
    from tests.server import mcwalls_harness as H

    cli = H.load_cli()
    wh = H.make_tmp_root("mcwalls-wh-")
    home = H.make_tmp_root("mcwalls-home-")
    plist = os.path.join(str(home), "Library", "LaunchAgents", PLIST_LABEL + ".plist")
    bootstrap = ["launchctl", "bootstrap", "gui/%d" % os.getuid(), plist]
    kickstart = ["launchctl", "kickstart", "-k", "gui/%d/%s" % (os.getuid(), PLIST_LABEL)]

    # bootstrap fails -> kickstart -k fallback; kickstart healthy -> start ok.
    executor = H.FakeExecutor(results={tuple(bootstrap): (1, "bootstrap failed")})
    c = cli.Cli(str(wh), str(home), executor, stdout=io.StringIO())
    assert c.start() == 0
    assert executor.argvs[0] == bootstrap
    assert executor.argvs[1] == kickstart

    # bootstrap healthy -> no kickstart at all.
    executor2 = H.FakeExecutor()
    c2 = cli.Cli(str(wh), str(home), executor2, stdout=io.StringIO())
    assert c2.start() == 0
    assert executor2.argvs == [bootstrap]


def test_stop_and_restart_order():
    from tests.server import mcwalls_harness as H

    cli = H.load_cli()
    wh = H.make_tmp_root("mcwalls-wh-")
    home = H.make_tmp_root("mcwalls-home-")
    plist = os.path.join(str(home), "Library", "LaunchAgents", PLIST_LABEL + ".plist")
    bootstrap = ["launchctl", "bootstrap", "gui/%d" % os.getuid(), plist]

    # stop: exactly one bootout argv.
    executor = H.FakeExecutor()
    out = io.StringIO()
    c = cli.Cli(str(wh), str(home), executor, stdout=out)
    assert c.stop() == 0
    assert executor.argvs == [BOOTOUT_ARGV]
    assert "not loaded" not in out.getvalue()

    # stop with a non-zero bootout: tolerated, one "(ok)" line, rc still 0.
    executor2 = H.FakeExecutor(results={tuple(BOOTOUT_ARGV): (5, "not loaded")})
    out2 = io.StringIO()
    c2 = cli.Cli(str(wh), str(home), executor2, stdout=out2)
    assert c2.stop() == 0
    assert "launchd: not loaded (ok)" in out2.getvalue()

    # restart: bootout THEN bootstrap, in that order, nothing else.
    executor3 = H.FakeExecutor()
    assert cli.Cli(str(wh), str(home), executor3, stdout=io.StringIO()).restart() == 0
    assert executor3.argvs == [BOOTOUT_ARGV, bootstrap]

    # restart still starts even when stop's bootout reports non-zero.
    executor4 = H.FakeExecutor(results={tuple(BOOTOUT_ARGV): (5, "not loaded")})
    assert cli.Cli(str(wh), str(home), executor4, stdout=io.StringIO()).restart() == 0
    assert executor4.argvs == [BOOTOUT_ARGV, bootstrap]


def test_status_both_healthy_and_http_failure():
    from tests.server import mcwalls_harness as H

    cli = H.load_cli()
    wh = H.make_tmp_root("mcwalls-wh-")
    home = H.make_tmp_root("mcwalls-home-")

    # collect_state injected so /state answers a real 2xx (the lazy tower
    # wrapper would degrade to 503 and fail the probe for the wrong reason).
    with H.serve(collect_state=H.StubTower(state={"rows": []})) as h:
        port = h.server.server_address[1]
        (wh / "wall.json").write_text(
            json.dumps({"token": h.token, "port": port}), encoding="utf-8"
        )
        executor = H.FakeExecutor()  # launchctl print -> (0, "") by default
        out = io.StringIO()
        c = cli.Cli(str(wh), str(home), executor, stdout=out)
        assert c.status() == 0
        text = out.getvalue()
        assert "launchctl: ok" in text
        assert "http: ok" in text
        assert "http: failed" not in text
        assert executor.argvs == [PRINT_ARGV]

    # Server shut down: the same probe now fails. launchctl still ok, so the
    # dual verdict must be rc 1 with exactly the http half failed.
    executor2 = H.FakeExecutor()
    out2 = io.StringIO()
    c2 = cli.Cli(str(wh), str(home), executor2, stdout=out2)
    assert c2.status() == 1
    text2 = out2.getvalue()
    assert "launchctl: ok" in text2
    assert "http: failed" in text2


def test_open_chrome_and_fallback():
    from tests.server import mcwalls_harness as H

    cli = H.load_cli()
    wh = H.make_tmp_root("mcwalls-wh-")
    home = H.make_tmp_root("mcwalls-home-")
    (wh / "wall.json").write_text(
        json.dumps({"token": "opentok", "port": 8765}), encoding="utf-8"
    )
    url = "http://127.0.0.1:8765/opentok/"
    profile = os.path.join(str(wh), "chrome-profile")
    chrome_argv = [
        "open",
        "-na",
        "Google Chrome",
        "--args",
        "--app=" + url,
        "--user-data-dir=" + profile,
    ]

    # Chrome command fails -> fallback `open <url>` issued second; the
    # chrome-profile dir exists either way.
    executor = H.FakeExecutor(results={tuple(chrome_argv): (1, "")})
    c = cli.Cli(str(wh), str(home), executor, stdout=io.StringIO())
    assert c.open_wall() == 0
    assert os.path.isdir(profile)
    assert executor.argvs[0] == chrome_argv
    assert "--app=http://127.0.0.1:8765/opentok/" in executor.argvs[0]
    assert "--user-data-dir=" + os.path.join(str(wh), "chrome-profile") in executor.argvs[0]
    assert executor.argvs[1] == ["open", url]

    # Chrome healthy -> no fallback.
    executor2 = H.FakeExecutor()
    c2 = cli.Cli(str(wh), str(home), executor2, stdout=io.StringIO())
    assert c2.open_wall() == 0
    assert executor2.argvs == [chrome_argv]
