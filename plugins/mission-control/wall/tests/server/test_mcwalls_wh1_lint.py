"""W5-L5 wall-honesty CLI tests (A7): ``bin/mc-wall lint-note <path>`` and
``--all`` run the SAME parser the server collects with (one truth), print
defects with their lines, a per-note "N rows parsed" summary line, and exit 1
on ANY defect / 0 clean. ``--all`` walks every declared (+ discovered, unless
MC_WALL_DISCOVERY=0) program's note. Same discipline as the other CLI cases:
bin/mc-wall loads via the harness load_cli() inside each test; the two roots
are ALWAYS injected tmp dirs; MC_WALL_FORGE_ROOT redirects the manifest
cross-check so no test reads the real ~/.mc-wall/forge.
"""

import io
import json
import os

HEADER = "| id | wave | lane | repo/branch | slug | base | session/MR artifacts | status |"
SEP = "|---|---|---|---|---|---|---|---|"

GOOD_ROWS = [
    "| W1-L1 | W1 | L1 | ~/repos/mc loop/x | s1 | d55 | sess_00000001 | done |",
    "| W1-L2 | W1 | L2 | ~/repos/mc main | s2 | d55 | n/a | forged |",
]


def _make_note(root, name, lines):
    path = os.path.join(str(root), name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def _install_wall_json(root, note_path, program="wh1prog"):
    # A usable wall.json (token + one declared program) under wall_home.
    data = {"token": "linttok", "port": 8765,
            "programs": [{"program": program, "tag": program,
                          "note_glob": note_path}]}
    with open(os.path.join(str(root), "wall.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return data


def test_wh1_cli_lint_clean_note_exits_zero():
    from tests.server import mcwalls_harness as H

    cli = H.load_cli()
    wh = H.make_tmp_root("mcwalls-wh1-")
    note = _make_note(wh, "clean.md", ["objective: clean", HEADER, SEP] + GOOD_ROWS)
    out = io.StringIO()
    c = cli.Cli(str(wh), str(H.make_tmp_root("mcwalls-wh1-home-")),
                executor=H.FakeExecutor(), stdout=out)
    assert c.lint_note(note) == 0
    assert out.getvalue() == "%s: 2 rows parsed, 0 defects\n" % note


def test_wh1_cli_lint_incident_fixture_exits_one_with_lines():
    # EXPECT-5: the exact incident shape under lint — the blank line's defect
    # is printed WITH ITS LINE, rows still count, exit 1.
    from tests.server import mcwalls_harness as H

    cli = H.load_cli()
    wh = H.make_tmp_root("mcwalls-wh1-")
    note = _make_note(wh, "incident.md", [
        "objective: incident", HEADER, SEP, ""] + GOOD_ROWS)  # blank after separator
    out = io.StringIO()
    c = cli.Cli(str(wh), str(H.make_tmp_root("mcwalls-wh1-home-")),
                executor=H.FakeExecutor(), stdout=out)
    assert c.lint_note(note) == 1
    text = out.getvalue()
    assert "%s: 2 rows parsed, 1 defect\n" % note in text
    assert "  line 4: blank line inside prompt-log table\n" in text


def test_wh1_cli_lint_stray_and_unreadable():
    from tests.server import mcwalls_harness as H

    cli = H.load_cli()
    wh = H.make_tmp_root("mcwalls-wh1-")
    stray = _make_note(wh, "stray.md", [
        "objective: stray", HEADER, SEP, GOOD_ROWS[0],
        "prose closes the table",
        "| W9-L9 | W9 | L9 | ~/repos/mc main | s | n/a | n/a | forged |"])
    out = io.StringIO()
    c = cli.Cli(str(wh), str(H.make_tmp_root("mcwalls-wh1-home-")),
                executor=H.FakeExecutor(), stdout=out)
    assert c.lint_note(stray) == 1
    assert "lane row outside prompt-log table [row W9-L9]" in out.getvalue()

    missing = os.path.join(str(wh), "nope.md")
    out2 = io.StringIO()
    c2 = cli.Cli(str(wh), str(H.make_tmp_root("mcwalls-wh1-home-")),
                 executor=H.FakeExecutor(), stdout=out2)
    assert c2.lint_note(missing) == 1
    assert "UNREADABLE" in out2.getvalue()


def test_wh1_cli_lint_manifest_alarm_via_program_match(tmp_path, monkeypatch):
    # A direct-path lint whose note IS a declared program's note runs the
    # forge-manifest cross-check (MC_WALL_FORGE_ROOT redirects it): a forged
    # row absent from the parse alarms at "manifest:".
    from tests.server import mcwalls_harness as H

    cli = H.load_cli()
    wh = H.make_tmp_root("mcwalls-wh1-")
    home = H.make_tmp_root("mcwalls-wh1-home-")
    forge = tmp_path / "forge"
    (forge / "wh1prog" / "W1-L2").mkdir(parents=True)
    (forge / "wh1prog" / "W1-L2" / "manifest.json").write_text(
        json.dumps({"row_id": "W1-L2", "program": "wh1prog"}), encoding="utf-8")
    note = _make_note(wh, "manifest.md", [
        "objective: manifest", HEADER, SEP, GOOD_ROWS[0]])  # W1-L2's row absent
    _install_wall_json(wh, note)
    monkeypatch.setenv("MC_WALL_FORGE_ROOT", str(forge))
    monkeypatch.setenv("MC_WALL_DISCOVERY", "0")
    out = io.StringIO()
    c = cli.Cli(str(wh), str(home), executor=H.FakeExecutor(), stdout=out)
    assert c.lint_note(note) == 1
    text = out.getvalue()
    assert "1 rows parsed" in text
    assert ("  manifest: forged row W1-L2 absent from note parse"
            " (manifest exists) [row W1-L2]\n") in text


def test_wh1_cli_lint_all_walks_programs(tmp_path, monkeypatch):
    # EXPECT-5 (--all): every declared program's note lints; a glob miss is a
    # failure; no wall.json -> one clear line, exit 1.
    from tests.server import mcwalls_harness as H

    cli = H.load_cli()
    wh = H.make_tmp_root("mcwalls-wh1-")
    home = H.make_tmp_root("mcwalls-wh1-home-")
    good = _make_note(wh, "good.md", ["objective: good", HEADER, SEP] + GOOD_ROWS)
    broken = _make_note(wh, "broken.md", [
        "objective: broken", HEADER, SEP, "",
        "| W2-L1 | W2 | L1 | ~/r loop/x | s | n/a | n/a | done |",
        "| W2-B | W2 | B | ~/r main | s | n/a | n/a | totally-done | EXTRA |"])
    data = {"token": "linttok", "port": 8765, "programs": [
        {"program": "wh1prog", "tag": "wh1prog", "note_glob": good},
        {"program": "wh1prog2", "tag": "wh1prog2", "note_glob": broken},
        {"program": "wh1miss", "tag": "wh1miss",
         "note_glob": os.path.join(str(wh), "none-*.md")}]}
    with open(os.path.join(str(wh), "wall.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    monkeypatch.setenv("MC_WALL_DISCOVERY", "0")
    monkeypatch.setenv("MC_WALL_FORGE_ROOT", str(tmp_path / "no-forge"))
    out = io.StringIO()
    c = cli.Cli(str(wh), str(home), executor=H.FakeExecutor(), stdout=out)
    assert c.lint_all() == 1
    text = out.getvalue()
    assert "# mc-wall lint-note --all (3 programs)" in text
    assert "%s: 2 rows parsed, 0 defects" % good in text
    assert "%s: 2 rows parsed, 3 defects" % broken in text
    assert "line 4: blank line inside prompt-log table" in text
    assert "1 extra cells under variant-A header [row W2-B]" in text
    assert "status not in vocabulary: 'totally-done'" in text
    assert "no note matches the glob (notes degraded: wh1miss)" in text

    # No wall.json at all: the same one-line refusal as status/open.
    wh2 = H.make_tmp_root("mcwalls-wh1b-")
    out2 = io.StringIO()
    c2 = cli.Cli(str(wh2), str(home), executor=H.FakeExecutor(), stdout=out2)
    assert c2.lint_all() == 1
    assert cli.NO_WALL_JSON in out2.getvalue()


def test_wh1_cli_lint_note_dispatch_via_main():
    from tests.server import mcwalls_harness as H

    cli = H.load_cli()
    wh = H.make_tmp_root("mcwalls-wh1-")
    note = _make_note(wh, "clean.md", ["objective: clean", HEADER, SEP] + GOOD_ROWS)
    rc = cli.main(["lint-note", note], version_info=(3, 11),
                  wall_home=str(wh), home=str(H.make_tmp_root("mcwalls-wh1-home-")))
    assert rc == 0
    rc2 = cli.main(["lint-note", "--all"], version_info=(3, 11),
                   wall_home=str(H.make_tmp_root("mcwalls-wh1c-")),
                   home=str(H.make_tmp_root("mcwalls-wh1-home-")))
    assert rc2 == 1  # no wall.json installed
