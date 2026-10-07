"""rg1 — ``bin/mc-wall register|deregister|list|bind`` (v1.12.0): the operator's
registration surface. SC-1/2/3 contracts pinned here: lint-first register,
backup-first writes under <wall_home>/backups/, token/port/existing entries
preserved, idempotent no-op re-runs, REAL deregistration (declaration removed
+ ignore[] so discovery cannot resurrect the note), list with
declared/discovered/ignored + lane counts + lint status, bind writing the
session token into the row's artifacts cell with post-edit lint + the /state
assert line. Same discipline as the other CLI files: load_cli() per test, BOTH
roots injected tmp dirs, MC_WALL_DISCOVERY=0 (except the discovery-visible
list case), MC_WALL_FORGE_ROOT redirected, fake state_fetch/poll_sleep."""

import io
import json
import os

from tests.server import mcwalls_harness as H
from tests.tower.conftest import mcwallt_make_db

HEADER = "| id | wave | lane | repo/branch | slug | base | session/MR artifacts | status |"
SEP = "|---|---|---|---|---|---|---|---|"
SESS = "sess_aaaa1111bbbb2222"
SESS2 = "sess_cccc3333dddd4444"

NOTE_TMPL = "objective: %s\n%s\n%s\n%s\n"


def _make_program_dir(root, slug, rows, objective="obj"):
    d = os.path.join(str(root), "programs")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "mission-control-%s-program.md" % slug)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(NOTE_TMPL % (objective, HEADER, SEP, "\n".join(rows)))
    return path


def _wall_json(root, data):
    path = os.path.join(str(root), "wall.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path


def _read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _cli(cli_mod, wall_home, out):
    return cli_mod.Cli(str(wall_home), str(H.make_tmp_root("rg1-home-")),
                       executor=H.FakeExecutor(), stdout=out,
                       state_fetch=_fake_state, poll_sleep=lambda s: None)


def _fake_state(url):
    return (True, {"programs": [
        {"program": "prog", "lanes": [
            {"row_id": "W1-L1", "session": {"id": SESS + "-deadbeef"}}]}]})


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("MC_WALL_DISCOVERY", "0")
    monkeypatch.setenv("MC_WALL_FORGE_ROOT", str(tmp_path / "no-forge"))


# -- register (SC-1) --------------------------------------------------------


def test_rg1_register_by_path_declares_and_derives_repos(tmp_path, monkeypatch):
    from mc_wall.tower import discovery

    _env(monkeypatch, tmp_path)
    cli = H.load_cli()
    wh = H.make_tmp_root("rg1-reg-")
    repo = tmp_path / "myrepo"
    repo.mkdir()
    note = _make_program_dir(wh, "fresh", [
        "| W1-L1 | W1 | L1 | %s loop/x | s1 | n/a | n/a | forged |" % repo])
    _wall_json(wh, {"token": "rgtok", "port": 8799,
                    "programs": [{"program": "other", "tag": "other",
                                  "note_glob": "/none-*.md"}],
                    "repos": [{"name": "other", "path": "/other", "host": "gitlab"}]})
    monkeypatch.setattr(discovery, "_run_git",
                        lambda p: (0, "origin git@github.com:x/y.git (fetch)\n"))
    out = io.StringIO()
    assert _cli(cli, wh, out).register(note) == 0
    text = out.getvalue()
    assert "programs[]: registered fresh -> %s" % note in text
    assert "repos[]: added myrepo (github" in text
    assert "wall.json updated — live within one ~5s poll (no restart)" in text
    data = _read_json(os.path.join(str(wh), "wall.json"))
    assert data["token"] == "rgtok" and data["port"] == 8799      # preserved
    assert data["programs"][0] == {"program": "other", "tag": "other",
                                   "note_glob": "/none-*.md"}      # untouched
    assert {"program": "fresh", "tag": "fresh", "note_glob": note} in data["programs"]
    assert {"name": "myrepo", "path": str(repo), "host": "github"} in data["repos"]
    backups = os.listdir(os.path.join(str(wh), "backups"))
    assert len([b for b in backups if b.startswith("wall.json.backup-")]) == 1


def test_rg1_register_idempotent_no_rewrite(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    cli = H.load_cli()
    wh = H.make_tmp_root("rg1-reg2-")
    note = _make_program_dir(wh, "fresh", [
        "| W1-L1 | W1 | L1 | n/a | s1 | n/a | n/a | done |"])
    _wall_json(wh, {"token": "rgtok", "port": 8799, "programs": []})
    out = io.StringIO()
    assert _cli(cli, wh, out).register(note) == 0
    wj = os.path.join(str(wh), "wall.json")
    before = open(wj, "rb").read()
    n_backups = len(os.listdir(os.path.join(str(wh), "backups")))
    out2 = io.StringIO()
    assert _cli(cli, wh, out2).register(note) == 0
    assert "no changes — wall.json untouched" in out2.getvalue()
    assert open(wj, "rb").read() == before            # no rewrite
    assert len(os.listdir(os.path.join(str(wh), "backups"))) == n_backups


def test_rg1_register_by_slug_resolves_search_dirs(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    cli = H.load_cli()
    wh = H.make_tmp_root("rg1-reg3-")
    _make_program_dir(wh, "fresh", [
        "| W1-L1 | W1 | L1 | n/a | s1 | n/a | n/a | done |"])
    # A DECLARED program whose note_glob dir is the programs/ dir — the same
    # place discovery would scan — gives by-slug lookup its search path.
    anchor = _make_program_dir(wh, "anchor", [
        "| W1-L0 | W1 | L0 | n/a | s0 | n/a | n/a | done |"])
    _wall_json(wh, {"token": "rgtok", "port": 8799,
                    "programs": [{"program": "anchor", "tag": "anchor",
                                  "note_glob": anchor}]})
    out = io.StringIO()
    assert _cli(cli, wh, out).register("fresh") == 0
    data = _read_json(os.path.join(str(wh), "wall.json"))
    assert any(p["program"] == "fresh" for p in data["programs"])


def test_rg1_register_refuses_defective_note(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    cli = H.load_cli()
    wh = H.make_tmp_root("rg1-reg4-")
    bad = _make_program_dir(wh, "bad", [
        "| W1-L1 | W1 | L1 | n/a | s1 | n/a | n/a | totally-done |"])
    _wall_json(wh, {"token": "rgtok", "port": 8799, "programs": []})
    wj = os.path.join(str(wh), "wall.json")
    before = open(wj, "rb").read()
    out = io.StringIO()
    assert _cli(cli, wh, out).register(bad) == 1
    text = out.getvalue()
    assert "status not in vocabulary: 'totally-done'" in text
    assert "register refused" in text
    assert open(wj, "rb").read() == before  # untouched


def test_rg1_register_needs_wall_json_and_valid_name(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    cli = H.load_cli()
    out = io.StringIO()
    wh = H.make_tmp_root("rg1-reg5-")  # NO wall.json
    assert _cli(cli, wh, out).register("whatever") == 1
    assert cli.NO_WALL_JSON in out.getvalue()

    wh2 = H.make_tmp_root("rg1-reg6-")
    _wall_json(wh2, {"token": "t", "port": 8799, "programs": []})
    stray = os.path.join(str(wh2), "not-a-program-note.md")
    with open(stray, "w", encoding="utf-8") as fh:
        fh.write("objective: x\n")
    out2 = io.StringIO()
    assert _cli(cli, wh2, out2).register(stray) == 1
    assert "is not named mission-control-<slug>-program.md" in out2.getvalue()


def test_rg1_register_clears_stale_ignore(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    cli = H.load_cli()
    wh = H.make_tmp_root("rg1-reg7-")
    note = _make_program_dir(wh, "fresh", [
        "| W1-L1 | W1 | L1 | n/a | s1 | n/a | n/a | done |"])
    _wall_json(wh, {"token": "rgtok", "port": 8799, "programs": [],
                    "ignore": ["fresh"]})
    out = io.StringIO()
    assert _cli(cli, wh, out).register(note) == 0
    assert "ignore[]: removed fresh (declared again)" in out.getvalue()
    data = _read_json(os.path.join(str(wh), "wall.json"))
    assert data["ignore"] == []


# -- deregister (SC-2) ------------------------------------------------------


def test_rg1_deregister_removes_and_ignores(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    cli = H.load_cli()
    wh = H.make_tmp_root("rg1-dereg-")
    note = _make_program_dir(wh, "prog", [
        "| W1-L1 | W1 | L1 | n/a | s1 | n/a | n/a | done |"])
    _wall_json(wh, {"token": "rgtok", "port": 8799,
                    "programs": [{"program": "prog", "tag": "prog",
                                  "note_glob": note}], "repos": []})
    out = io.StringIO()
    assert _cli(cli, wh, out).deregister("prog") == 0
    text = out.getvalue()
    assert "programs[]: removed declaration for prog" in text
    assert "ignore[]: added prog — discovery will not re-register it" in text
    data = _read_json(os.path.join(str(wh), "wall.json"))
    assert data["programs"] == []
    assert data["ignore"] == ["prog"]
    assert data["token"] == "rgtok"  # preserved
    assert len([b for b in os.listdir(os.path.join(str(wh), "backups"))
                if b.startswith("wall.json.backup-")]) == 1
    # Idempotent: the second run changes nothing (no new backup, rc 0).
    n_backups = len(os.listdir(os.path.join(str(wh), "backups")))
    out2 = io.StringIO()
    assert _cli(cli, wh, out2).deregister("prog") == 0
    assert "no changes — wall.json untouched" in out2.getvalue()
    assert len(os.listdir(os.path.join(str(wh), "backups"))) == n_backups


def test_rg1_deregister_unknown_refused(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    cli = H.load_cli()
    wh = H.make_tmp_root("rg1-dereg2-")
    _wall_json(wh, {"token": "rgtok", "port": 8799, "programs": []})
    wj = os.path.join(str(wh), "wall.json")
    before = open(wj, "rb").read()
    out = io.StringIO()
    assert _cli(cli, wh, out).deregister("ghost") == 1
    assert "nothing to remove" in out.getvalue()
    assert open(wj, "rb").read() == before


def test_rg1_deregister_discovered_slug_ignores(tmp_path, monkeypatch):
    # A note on disk but NOT declared (exactly what discovery registers):
    # deregistration's real job is the ignore[] entry. DEFAULT_SEARCH_DIR is
    # repointed so the empty-programs fallback never scans the real vault.
    from mc_wall.tower import discovery

    _env(monkeypatch, tmp_path)
    cli = H.load_cli()
    wh = H.make_tmp_root("rg1-dereg3-")
    vault = H.make_tmp_root("rg1-vault3-")
    monkeypatch.setattr(discovery, "DEFAULT_SEARCH_DIR",
                        os.path.join(str(vault), "programs"))
    os.makedirs(os.path.join(str(vault), "programs"))
    with open(os.path.join(str(vault), "programs",
                           "mission-control-ghostprog-program.md"), "w",
              encoding="utf-8") as fh:
        fh.write(NOTE_TMPL % ("g", HEADER, SEP,
                              "| W1-L1 | W1 | L1 | n/a | s1 | n/a | n/a | done |"))
    _wall_json(wh, {"token": "rgtok", "port": 8799, "programs": []})
    out = io.StringIO()
    assert _cli(cli, wh, out).deregister("ghostprog") == 0
    assert "ignore[]: added ghostprog" in out.getvalue()
    data = _read_json(os.path.join(str(wh), "wall.json"))
    assert data["ignore"] == ["ghostprog"]


def test_rg1_deregister_respected_by_boot(tmp_path, monkeypatch):
    # End-to-end: after deregister, tower_config_from_wall (the per-poll
    # rebuild) does NOT carry the program even with discovery enabled.
    # DEFAULT_SEARCH_DIR repointed: the emptied programs[] fallback must not
    # scan the real vault.
    from mc_wall.tower import discovery

    _env(monkeypatch, tmp_path)
    monkeypatch.setattr(discovery, "DEFAULT_SEARCH_DIR",
                        os.path.join(str(tmp_path), "programs"))
    cli = H.load_cli()
    wh = H.make_tmp_root("rg1-dereg4-")
    note = _make_program_dir(wh, "prog", [
        "| W1-L1 | W1 | L1 | n/a | s1 | n/a | n/a | done |"])
    _wall_json(wh, {"token": "rgtok", "port": 8799, "programs": [
        {"program": "prog", "tag": "prog", "note_glob": note}]})
    monkeypatch.delenv("MC_WALL_DISCOVERY")
    out = io.StringIO()
    assert _cli(cli, wh, out).deregister("prog") == 0
    from mc_wall.server import tower_boot

    cfg = tower_boot.tower_config_from_wall(
        _read_json(os.path.join(str(wh), "wall.json")), str(wh))
    assert [p.program for p in cfg.programs] == []


# -- list (SC-2) ------------------------------------------------------------


def test_rg1_list_shows_kinds_lanes_lint(tmp_path, monkeypatch):
    cli = H.load_cli()
    wh = H.make_tmp_root("rg1-list-")
    good = _make_program_dir(wh, "goodprog", [
        "| W1-L1 | W1 | L1 | n/a | s1 | n/a | n/a | done |",
        "| W1-L2 | W1 | L2 | n/a | s2 | n/a | n/a | done |"])
    bad = _make_program_dir(wh, "badprog", [
        "| W1-L1 | W1 | L1 | n/a | s1 | n/a | n/a | nope |"])
    _wall_json(wh, {"token": "rgtok", "port": 8799,
                    "programs": [{"program": "goodprog", "tag": "goodprog",
                                  "note_glob": good}],
                    "ignore": ["retired"]})
    monkeypatch.delenv("MC_WALL_DISCOVERY", raising=False)
    monkeypatch.setenv("MC_WALL_FORGE_ROOT", str(tmp_path / "no-forge"))
    out = io.StringIO()
    assert _cli(cli, wh, out).list_programs() == 1  # badprog's defect fails rc
    text = out.getvalue()
    assert "# mc-wall list — 1 declared, 1 discovered, 1 ignored" in text
    good_line = [ln for ln in text.split("\n") if ln.startswith("goodprog")][0]
    assert "declared" in good_line and "2 lanes" in good_line \
        and "lint:clean" in good_line and good_line.rstrip().endswith(good)
    bad_line = [ln for ln in text.split("\n") if ln.startswith("badprog")][0]
    assert "discovered" in bad_line and "1 lanes" in bad_line \
        and "lint:1 defects" in bad_line and bad_line.rstrip().endswith(bad)
    ignored_line = [ln for ln in text.split("\n") if ln.startswith("retired")][0]
    assert "ignored" in ignored_line

    out2 = io.StringIO()
    wh2 = H.make_tmp_root("rg1-list2-")
    assert _cli(cli, wh2, out2).list_programs() == 1
    assert cli.NO_WALL_JSON in out2.getvalue()


# -- bind (SC-3) -------------------------------------------------------------


def _bind_fixture(wh, rows=None, sess_rows=None, db_name="rg1.db"):
    note = _make_program_dir(wh, "prog", rows if rows is not None else [
        "| W1-L1 | W1 | L1 | n/a | s1 | n/a | n/a | done |"])
    db = mcwallt_make_db(H.make_tmp_root("rg1-db-"), name=db_name,
                         sessions=sess_rows if sess_rows is not None else [
                             {"id": SESS + "-1111", "title": "t", "directory": "/x",
                              "time_updated": 1000, "time_created": 900}])
    _wall_json(wh, {"token": "rgtok", "port": 8799,
                    "programs": [{"program": "prog", "tag": "prog",
                                  "note_glob": note}], "db_path": db})
    return note, db


def test_rg1_bind_writes_token_backs_up_and_asserts(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    cli = H.load_cli()
    wh = H.make_tmp_root("rg1-bind-")
    note, _db = _bind_fixture(wh)
    out = io.StringIO()
    assert _cli(cli, wh, out).bind("prog", "W1-L1", SESS) == 0
    text = out.getvalue()
    assert "bound: prog row W1-L1 <- %s" % SESS in text
    assert ("state: program=prog row=W1-L1 session=%s — bound (poll 1)"
            % (SESS + "-deadbeef")) in text
    assert ": 1 rows parsed, 0 defects" in text  # post-edit lint ran
    with open(note, encoding="utf-8") as fh:
        row = [ln for ln in fh.read().split("\n") if ln.startswith("| W1-L1")][0]
    assert row.endswith("| %s | done |" % SESS)
    assert len([b for b in os.listdir(os.path.join(str(wh), "backups"))
                if b.startswith("note-prog.backup-")]) == 1


def test_rg1_bind_idempotent_and_rebind_replaces(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    cli = H.load_cli()
    wh = H.make_tmp_root("rg1-bind2-")
    note, _db = _bind_fixture(wh, rows=[
        "| W1-L1 | W1 | L1 | n/a | s1 | n/a | n/a | done |"], sess_rows=[
        {"id": SESS + "-1111", "time_updated": 1000, "time_created": 900},
        {"id": SESS2 + "-2222", "time_updated": 1000, "time_created": 900}])
    assert _cli(cli, wh, io.StringIO()).bind("prog", "W1-L1", SESS) == 0
    before = open(note, "rb").read()
    out = io.StringIO()
    assert _cli(cli, wh, out).bind("prog", "W1-L1", SESS) == 0
    assert "already bound" in out.getvalue()
    assert open(note, "rb").read() == before  # idempotent: no rewrite

    # Rebind REPLACES the binding token; verify:ok + MR refs are preserved.
    wh3 = H.make_tmp_root("rg1-bind3-")
    note3, _db3 = _bind_fixture(wh3, db_name="rg1b.db", rows=[
        "| W2-L1 | W2 | L1 | n/a | s1 | n/a | %s verify:ok #21 | done |" % SESS],
        sess_rows=[{"id": SESS2 + "-2222", "time_updated": 1000,
                    "time_created": 900}])

    def fake_state2(url):
        return (True, {"programs": [
            {"program": "prog", "lanes": [
                {"row_id": "W2-L1", "session": {"id": SESS2 + "-2222"}}]}]})

    c3 = cli.Cli(str(wh3), str(H.make_tmp_root("rg1-home-")),
                 executor=H.FakeExecutor(), stdout=io.StringIO(),
                 state_fetch=fake_state2, poll_sleep=lambda s: None)
    out3 = io.StringIO()
    c3.stdout = out3
    assert c3.bind("prog", "W2-L1", SESS2) == 0
    with open(note3, encoding="utf-8") as fh:
        row = [ln for ln in fh.read().split("\n") if ln.startswith("| W2-L1")][0]
    assert row.endswith("| %s verify:ok #21 | done |" % SESS2)


def test_rg1_bind_refusals(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    cli = H.load_cli()
    wh = H.make_tmp_root("rg1-bind4-")
    note, _db = _bind_fixture(wh)
    out = io.StringIO()
    assert _cli(cli, wh, out).bind("nosuch", "W1-L1", SESS) == 1
    assert "unknown program" in out.getvalue()
    out = io.StringIO()
    assert _cli(cli, wh, out).bind("prog", "W9-L9", SESS) == 1
    assert "unknown row" in out.getvalue()
    out = io.StringIO()
    assert _cli(cli, wh, out).bind("prog", "W1-L1", "not-a-token") == 1
    assert "is not a session token" in out.getvalue()
    # A token matching ZERO sessions in the db is refused (typo guard).
    out = io.StringIO()
    assert _cli(cli, wh, out).bind("prog", "W1-L1", "sess_00000000dead") == 1
    assert "matches 0 sessions" in out.getvalue()
    assert "W1-L1" in open(note, encoding="utf-8").read()


def test_rg1_bind_tolerates_preexisting_defects(tmp_path, monkeypatch):
    # The hsp migration case: a note may carry KNOWN recorded defects (e.g.
    # the _BRANCH_RE residual) — bind is judged on defects IT introduces.
    # Pre-existing defects print as "unchanged"; rc stays 0.
    _env(monkeypatch, tmp_path)
    cli = H.load_cli()
    wh = H.make_tmp_root("rg1-bind6-")
    note, _db = _bind_fixture(wh, rows=[
        # repo token present but branch token off-vocabulary -> pre-existing defect
        "| W1-L1 | W1 | L1 | /tmp/x backport/thing | s1 | n/a | n/a | done |"])
    out = io.StringIO()
    assert _cli(cli, wh, out).bind("prog", "W1-L1", SESS) == 0
    text = out.getvalue()
    assert "bound: prog row W1-L1 <- %s" % SESS in text
    assert "1 defect (1 pre-existing, unchanged)" in text
    assert "revert via" not in text


def test_rg1_bind_main_dispatch(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    cli = H.load_cli()
    wh = H.make_tmp_root("rg1-bind5-")
    _bind_fixture(wh)
    rc = cli.main(["bind", "prog", "W1-L1", SESS], version_info=(3, 11),
                  wall_home=str(wh), home=str(H.make_tmp_root("rg1-home-")),
                  state_fetch=_fake_state, poll_sleep=lambda s: None)
    assert rc == 0
