"""T12 — CLI install: version guard, two injected roots, dry-run, token preservation.

bin/mc-wall (no .py extension) is loaded via the harness load_cli()
SourceFileLoader INSIDE each test: with bin/mc-wall absent, each test must
FAIL individually (pytest exit 1) instead of erroring at collection time
(exit 2), so the TDD red receipt carries item-level evidence. The two roots
(wall_home / home) are ALWAYS injected tmp dirs and the executor is ALWAYS a
FakeExecutor — no test ever touches ~/.zcode, ~/Library/LaunchAgents, or a
real launchctl.
"""

import io
import json
import os
import re
import stat
import sys

DRY_RUN_HEADER = (
    "# mc-wall install --dry-run (token: DRYRUN-TOKEN "
    "— nothing is written, nothing is started)"
)
PLIST_REL = os.path.join("Library", "LaunchAgents", "ai.zcode.mc-wall.plist")


def test_version_guard(capsys):
    from tests.server.mcwalls_harness import load_cli

    cli = load_cli()
    rc = cli.main(["status"], version_info=(3, 8, 14))
    assert rc == 2
    err = capsys.readouterr().err
    assert "3.9" in err


def test_dry_run_writes_nothing():
    from tests.server import mcwalls_harness as H

    wh = H.make_tmp_root("mcwalls-wh-")
    home = H.make_tmp_root("mcwalls-home-")
    before_wh = H.snapshot_tree(wh)
    before_home = H.snapshot_tree(home)

    cli = H.load_cli()
    executor = H.FakeExecutor()
    out = io.StringIO()
    c = cli.Cli(str(wh), str(home), executor, stdout=out)
    assert c.install(dry_run=True) == 0

    text = out.getvalue()
    assert text.splitlines()[0] == DRY_RUN_HEADER
    assert "DRYRUN-TOKEN" in text
    assert "ai.zcode.mc-wall" in text
    # ProgramArguments order: /bin/bash, -lc, <wall_home>/run.sh
    i_bash = text.index("/bin/bash")
    i_lc = text.index("-lc")
    i_run = text.index(os.path.join(str(wh), "run.sh"))
    assert i_bash < i_lc < i_run
    assert "CrashedOnly" in text
    assert "RunAtLoad" in text
    assert os.path.join(str(wh), "logs", "launchd.out.log") in text
    assert "export PATH=/opt/homebrew/bin:/usr/bin:/bin" in text
    assert 'PYTHONPATH="%s"' % str(H.REPO_ROOT) in text
    assert "exec /opt/homebrew/bin/python3.14 -m mc_wall.server" in text

    assert H.snapshot_tree(wh) == before_wh
    assert H.snapshot_tree(home) == before_home
    assert executor.argvs == []


def test_install_full():
    from tests.server import mcwalls_harness as H

    wh = H.make_tmp_root("mcwalls-wh-")
    home = H.make_tmp_root("mcwalls-home-")
    cli = H.load_cli()
    executor = H.FakeExecutor()
    c = cli.Cli(str(wh), str(home), executor, stdout=io.StringIO())
    assert c.install() == 0

    wall_json = wh / "wall.json"
    assert wall_json.is_file()
    assert stat.S_IMODE(wall_json.stat().st_mode) == 0o600
    data = json.loads(wall_json.read_text())
    assert re.fullmatch(r"[A-Za-z0-9_-]{20,}", data["token"])
    assert data["port"] == 8765
    assert (wh / "state").is_dir()
    assert (wh / "logs").is_dir()

    run_sh = wh / "run.sh"
    assert run_sh.is_file()
    assert stat.S_IMODE(run_sh.stat().st_mode) == 0o700
    body = run_sh.read_text()
    assert "export PATH=/opt/homebrew/bin:/usr/bin:/bin" in body
    assert 'PYTHONPATH="%s"' % str(H.REPO_ROOT) in body
    assert "exec /opt/homebrew/bin/python3.14 -m mc_wall.server" in body

    # plist lives under the INJECTED home — never under wall_home, never ~.
    plist = home / "Library" / "LaunchAgents" / "ai.zcode.mc-wall.plist"
    assert plist.is_file()
    assert not str(plist).startswith(str(wh))
    ptext = plist.read_text()
    assert "<string>ai.zcode.mc-wall</string>" in ptext
    assert "<string>%s</string>" % os.path.join(str(wh), "run.sh") in ptext
    assert os.path.join(str(wh), "logs", "launchd.out.log") in ptext
    assert "~" not in ptext
    assert "~" not in str(plist)

    assert executor.argvs == [
        ["launchctl", "bootstrap", "gui/%d" % os.getuid(), str(plist)]
    ]


def test_token_preserved_and_regenerate():
    from tests.server import mcwalls_harness as H

    wh = H.make_tmp_root("mcwalls-wh-")
    home = H.make_tmp_root("mcwalls-home-")
    cli = H.load_cli()

    def fresh_cli():
        return cli.Cli(str(wh), str(home), H.FakeExecutor(), stdout=io.StringIO())

    assert fresh_cli().install() == 0
    wall_json = wh / "wall.json"
    first_bytes = wall_json.read_bytes()
    first_token = json.loads(first_bytes)["token"]
    first_mtime = wall_json.stat().st_mtime_ns

    # Re-install without --regenerate-token: token byte-identical, untouched.
    assert fresh_cli().install() == 0
    assert wall_json.read_bytes() == first_bytes
    assert wall_json.stat().st_mtime_ns == first_mtime
    assert stat.S_IMODE(wall_json.stat().st_mode) == 0o600

    # --regenerate-token: new token, file rewritten, still mode 600.
    assert fresh_cli().install(regenerate_token=True) == 0
    second = json.loads(wall_json.read_text())
    assert second["token"] != first_token
    assert re.fullmatch(r"[A-Za-z0-9_-]{20,}", second["token"])
    assert wall_json.read_bytes() != first_bytes
    assert stat.S_IMODE(wall_json.stat().st_mode) == 0o600


def test_main_wiring():
    from tests.server import mcwalls_harness as H

    wh = H.make_tmp_root("mcwalls-wh-")
    home = H.make_tmp_root("mcwalls-home-")
    cli = H.load_cli()
    executor = H.FakeExecutor()
    rc = cli.main(
        ["install"],
        version_info=sys.version_info,
        wall_home=str(wh),
        home=str(home),
        executor=executor,
    )
    assert rc == 0
    # Effects landed under the injected tmp roots, never the real ones.
    assert (wh / "wall.json").is_file()
    assert (wh / "state").is_dir()
    assert (wh / "logs").is_dir()
    assert (wh / "run.sh").is_file()
    plist = home / "Library" / "LaunchAgents" / "ai.zcode.mc-wall.plist"
    assert plist.is_file()
    assert executor.argvs
    assert executor.argvs[0] == [
        "launchctl",
        "bootstrap",
        "gui/%d" % os.getuid(),
        str(plist),
    ]
