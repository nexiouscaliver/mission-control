"""T3 — the mc-status skill deploy hook on bin/mc-wall install().

The skills SOURCE is a fixture repo (``skillrepo/skills/mc-status/``) injected
via ``cli.skill_repo_root``; the deploy code under test is the real
``scripts.deploy_mc_status.deploy`` imported IN-PROCESS by the CLI. The
executor stays a FakeExecutor and its argvs must remain EXACTLY the one
launchctl bootstrap call — the deploy never spawns anything. All repo imports
are INSIDE the test functions so the RED phase fails per-item, not at
collection.
"""

import io
import os


def _make_skill_repo(tmp_path, body="fixture body v1"):
    from pathlib import Path

    root = Path(tmp_path) / "skillrepo"
    skill = root / "skills" / "mc-status"
    (skill / "reference").mkdir(parents=True)
    (skill / "SKILL.md").write_text(body, encoding="utf-8")
    (skill / "reference" / "notes.md").write_text("fixture reference\n", encoding="utf-8")
    return root


def test_deploy_skill_on_install(tmp_path):
    from tests.server import mcwalls_harness as H

    skillrepo = _make_skill_repo(tmp_path)
    wh = H.make_tmp_root("mcwalli-wh-")
    home = H.make_tmp_root("mcwalli-home-")
    cli = H.load_cli()
    out = io.StringIO()
    c = cli.Cli(str(wh), str(home), H.FakeExecutor(), stdout=out)
    c.skill_repo_root = str(skillrepo)

    assert c.install() == 0

    target = home / ".zcode" / "skills" / "mc-status"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "fixture body v1"
    assert (target / "reference" / "notes.md").read_text(encoding="utf-8") == "fixture reference\n"
    assert "skill deployed" in out.getvalue()


def test_redeploy_on_reinstall(tmp_path):
    from tests.server import mcwalls_harness as H

    skillrepo = _make_skill_repo(tmp_path)
    wh = H.make_tmp_root("mcwalli-wh-")
    home = H.make_tmp_root("mcwalli-home-")
    cli = H.load_cli()

    def fresh_cli():
        c = cli.Cli(str(wh), str(home), H.FakeExecutor(), stdout=io.StringIO())
        c.skill_repo_root = str(skillrepo)
        return c

    assert fresh_cli().install() == 0
    (skillrepo / "skills" / "mc-status" / "SKILL.md").write_text(
        "fixture body v2", encoding="utf-8")

    # Idempotent overwrite: a reinstall refreshes the deployed copy.
    assert fresh_cli().install() == 0
    target = home / ".zcode" / "skills" / "mc-status" / "SKILL.md"
    assert target.read_text(encoding="utf-8") == "fixture body v2"


def test_dry_run_does_not_deploy(tmp_path):
    from tests.server import mcwalls_harness as H

    skillrepo = _make_skill_repo(tmp_path)
    wh = H.make_tmp_root("mcwalli-wh-")
    home = H.make_tmp_root("mcwalli-home-")
    cli = H.load_cli()
    c = cli.Cli(str(wh), str(home), H.FakeExecutor(), stdout=io.StringIO())
    c.skill_repo_root = str(skillrepo)

    assert c.install(dry_run=True) == 0

    assert not (home / ".zcode" / "skills" / "mc-status").exists()


def test_install_executor_argv_unchanged(tmp_path):
    from tests.server import mcwalls_harness as H

    skillrepo = _make_skill_repo(tmp_path)
    wh = H.make_tmp_root("mcwalli-wh-")
    home = H.make_tmp_root("mcwalli-home-")
    cli = H.load_cli()
    executor = H.FakeExecutor()
    c = cli.Cli(str(wh), str(home), executor, stdout=io.StringIO())
    c.skill_repo_root = str(skillrepo)

    assert c.install() == 0

    # The deploy is in-process only: the executor saw EXACTLY the one
    # launchctl bootstrap argv — nothing else, in no other order.
    plist = home / "Library" / "LaunchAgents" / "ai.zcode.mc-wall.plist"
    assert executor.argvs == [
        ["launchctl", "bootstrap", "gui/%d" % os.getuid(), str(plist)]
    ]
