"""td1 discovery tests: AC-1,2,3,4,7,9,10,10b,11,12 (spec
mcwall-tower-discovery; R1 core + plumbing). The module-level
``from mc_wall.tower import discovery`` fails collection until the GREEN
phase lands ``mc_wall/tower/discovery.py`` — the accepted RED state."""

import json

import pytest

from mc_wall.server.tower_boot import build_tower_config, tower_config_from_wall
from mc_wall.tower import (ProgramConfig, RepoConfig, TowerConfig, collect_state,
                           discovery)
from mc_wall.tower import netcache as netcache_module
from tests.tower.conftest import (MCWALLT_HEADER_A, MCWALLT_SEP, mcwallt_fake_cmd,
                                  mcwallt_make_db, mcwallt_world,
                                  mcwallt_world_default_handler)


def td_note(directory, filename, objective="td-1 objective", repo="n/a"):
    """Valid candidate/declared note: objective line + variant-A table + 1 row."""
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / filename
    p.write_text("\n".join(["objective: " + objective, MCWALLT_HEADER_A, MCWALLT_SEP,
        f"| W1-L1 | W1 | L1 | {repo} | n/a | n/a | n/a | done |"]) + "\n", encoding="utf-8")
    return str(p)


def td_data(*note_globs):  # wall.json dict: one declared program per glob
    return {"programs": [{"program": f"p{i}", "tag": f"p{i}", "note_glob": g}
                         for i, g in enumerate(note_globs)]}


def td_wall(program, note_glob):
    """wall.json dict with ONE declared program named for its slug — the
    declared-wins check matches the candidate slug against the declared
    program NAME, so AC-1/2/7/10(iii) need alpha/gamma/anchor, not p0."""
    return {"programs": [{"program": program, "tag": program, "note_glob": note_glob}]}


def test_td1_discovery_adds_program(tmp_path):
    # AC-1: an undeclared valid candidate in the declared glob's dir joins the
    # boot set AFTER the declared programs (same ProgramConfig shape); clean
    # discovery adds no degraded line and no repo.
    d = tmp_path / "td-1-declared"
    alpha_glob = td_note(d, "mission-control-alpha-program.md", objective="td-1 alpha")
    beta = td_note(d, "mission-control-beta-program.md")
    cfg = tower_config_from_wall(td_wall("alpha", alpha_glob), tmp_path)
    assert cfg.programs == (ProgramConfig("alpha", "alpha", alpha_glob, None),
                            ProgramConfig("beta", "beta", beta, None))
    assert cfg.discovery_degraded == ()
    assert cfg.repos == ()


def test_td1_declared_wins(tmp_path):
    # AC-2: a candidate whose slug equals a DECLARED program name is shadowed
    # silently — the declared entry comes back verbatim, no duplicate, no line.
    d = tmp_path / "td-1-wins"
    alpha_glob = td_note(d, "alpha-declared.md", objective="td-1 declared alpha")
    td_note(d, "mission-control-alpha-program.md", objective="td-1 different objective")
    cfg = tower_config_from_wall(td_wall("alpha", alpha_glob), tmp_path)
    assert cfg.programs == (ProgramConfig("alpha", "alpha", alpha_glob, None),)
    assert cfg.discovery_degraded == ()
    assert cfg.repos == ()


def test_td1_nonmatching_filenames(tmp_path):
    # AC-3: only FILENAME_RE-matching REGULAR files are candidates — wrong
    # prefix/suffix, case mismatch, empty slug, and a directory named like a
    # note are all silently ignored (decision 1's "not a candidate" class).
    d = tmp_path / "td-1-names"
    declared = td_note(d, "td-1-declared.md")
    for name in ("mc-wall-mission-control-facts.md", "random.txt",
                 "Mission-control-X-Program.md", "mission-control--program.md"):
        (d / name).write_text("objective: x\n" + MCWALLT_HEADER_A + "\n", encoding="utf-8")
    (d / "mission-control-dir-program.md").mkdir()
    cfg = tower_config_from_wall(td_data(declared), tmp_path)
    assert cfg.programs == (ProgramConfig("p0", "p0", declared, None),)
    assert cfg.discovery_degraded == ()
    assert cfg.repos == ()


def test_td1_malformed_candidate(tmp_path):
    # AC-4: three isolated rejection classes — undecodable bytes, no prompt-log
    # table, no objective line — each skips the candidate with exactly its
    # decision-2 line (first failing check: read -> header -> objective).
    cases = (
        ("td-1-bytes", "mission-control-bad-bytes-program.md",
         lambda p: p.write_bytes(b"\xff\x00\xfe\x80"),
         "discovery degraded: unreadable note {p}"),
        ("td-1-notable", "mission-control-no-table-program.md",
         lambda p: p.write_text("objective: x\nno table\n", encoding="utf-8"),
         "discovery degraded: no prompt-log table in {p}"),
        ("td-1-noobj", "mission-control-no-objective-program.md",
         lambda p: p.write_text("\n".join([MCWALLT_HEADER_A, MCWALLT_SEP,
             "| W1-L1 | W1 | L1 | n/a | n/a | n/a | n/a | done |"]) + "\n",
             encoding="utf-8"),
         "discovery degraded: no objective in {p}"),
    )
    for sub, filename, write, line in cases:
        d = tmp_path / sub
        declared = td_note(d, "td-1-declared.md")
        candidate = d / filename
        write(candidate)
        cfg = tower_config_from_wall(td_data(declared), tmp_path)
        assert cfg.programs == (ProgramConfig("p0", "p0", declared, None),), sub
        assert cfg.discovery_degraded == (line.format(p=candidate),), sub
        assert cfg.repos == (), sub


def test_td1_boot_set_union(tmp_path, monkeypatch):
    # AC-7: build_tower_config boot union — declared gamma, then discovered
    # beta/carol sorted by slug; the gamma-name collision is shadowed silently;
    # the no-objective candidate degrades with its one line. Rows are
    # repo-token-free (plan deviation 3) so repos stay () through td2.
    monkeypatch.setenv("MC_WALL_DB", str(tmp_path / "td-1.db"))
    d = tmp_path / "td-1-boot"
    gamma_glob = td_note(d, "gamma-declared.md", objective="td-1 gamma")
    beta = td_note(d, "mission-control-beta-program.md")
    carol = td_note(d, "mission-control-carol-program.md")
    no_objective = d / "mission-control-no-objective-program.md"
    no_objective.write_text("\n".join([MCWALLT_HEADER_A, MCWALLT_SEP,
        "| W1-L1 | W1 | L1 | n/a | n/a | n/a | n/a | done |"]) + "\n", encoding="utf-8")
    td_note(d, "mission-control-gamma-program.md", objective="td-1 gamma collision")
    (tmp_path / "wall.json").write_text(json.dumps(
        {"token": "t", "programs": [{"program": "gamma", "tag": "gamma",
                                     "note_glob": gamma_glob}]}), encoding="utf-8")
    cfg = build_tower_config(tmp_path)
    assert cfg.programs == (ProgramConfig("gamma", "gamma", gamma_glob, None),
                            ProgramConfig("beta", "beta", beta, None),
                            ProgramConfig("carol", "carol", carol, None))
    assert cfg.discovery_degraded == (f"discovery degraded: no objective in {no_objective}",)
    assert cfg.repos == ()


def test_td1_duplicate_slug(tmp_path):
    # AC-9: the same discovered slug in two search dirs — the first in scan
    # order (sorted dirs, td-1-a < td-1-b) is added once; each later one is
    # skipped WITH the duplicate line naming its own absolute path.
    a, b = tmp_path / "td-1-a", tmp_path / "td-1-b"
    glob_a = td_note(a, "a-declared.md")
    glob_b = td_note(b, "b-declared.md")
    dup_a = td_note(a, "mission-control-dup-program.md")
    dup_b = td_note(b, "mission-control-dup-program.md")
    cfg = tower_config_from_wall(td_data(glob_a, glob_b), tmp_path)
    assert cfg.programs == (ProgramConfig("p0", "p0", glob_a, None),
                            ProgramConfig("p1", "p1", glob_b, None),
                            ProgramConfig("dup", "dup", dup_a, None))
    assert cfg.discovery_degraded == (f"discovery degraded: duplicate program dup at {dup_b}",)
    assert cfg.repos == ()


def test_td1_search_fallback(tmp_path, monkeypatch):
    # AC-10: zero declared programs -> the fallback constant is scanned; a
    # nonexistent fallback dir is clean-empty; a wildcard declared glob
    # contributes its dirname and bypasses the fallback (plan deviation 2:
    # anchor is the declared slug, shadowed; only wild is discovered).
    vault = tmp_path / "td-1-vault" / "programs"
    fb = td_note(vault, "mission-control-fb-program.md")
    monkeypatch.setattr(discovery, "DEFAULT_SEARCH_DIR", str(vault))
    cfg = tower_config_from_wall(td_data(), tmp_path)
    assert cfg.programs == (ProgramConfig("fb", "fb", fb, None),)
    assert cfg.discovery_degraded == ()
    assert cfg.repos == ()

    monkeypatch.setattr(discovery, "DEFAULT_SEARCH_DIR", str(tmp_path / "td-1-none"))
    cfg = tower_config_from_wall(td_data(), tmp_path)
    assert cfg.programs == ()
    assert cfg.discovery_degraded == ()
    assert cfg.repos == ()

    w = tmp_path / "td-1-wild"
    wild = td_note(w, "mission-control-wild-program.md")
    td_note(w, "mission-control-anchor-program.md")
    wild_glob = str(w / "*.md")
    cfg = tower_config_from_wall(td_wall("anchor", wild_glob), tmp_path)
    assert cfg.programs == (ProgramConfig("anchor", "anchor", wild_glob, None),
                            ProgramConfig("wild", "wild", wild, None))
    assert cfg.discovery_degraded == ()
    assert cfg.repos == ()


@pytest.mark.parametrize("val", ["0", "OFF", "no", "false"])
def test_td1_env_opt_out(tmp_path, monkeypatch, val):
    # AC-10b: every off-value skips discovery entirely (declared-only boot, no
    # degraded lines); "1" and the unset default both run it (decision 3b).
    d = tmp_path / "td-1-opt"
    declared = td_note(d, "td-1-declared.md")
    cand = td_note(d, "mission-control-cand-program.md")
    data = td_data(declared)
    monkeypatch.setenv("MC_WALL_DISCOVERY", val)
    cfg = tower_config_from_wall(data, tmp_path)
    assert cfg.programs == (ProgramConfig("p0", "p0", declared, None),)
    assert cfg.discovery_degraded == ()
    assert cfg.repos == ()
    monkeypatch.setenv("MC_WALL_DISCOVERY", "1")
    cfg_on = tower_config_from_wall(data, tmp_path)
    assert cfg_on.programs == (ProgramConfig("p0", "p0", declared, None),
                               ProgramConfig("cand", "cand", cand, None))
    monkeypatch.delenv("MC_WALL_DISCOVERY")
    assert tower_config_from_wall(data, tmp_path).programs == cfg_on.programs


def test_td1_boot_no_crash(tmp_path, monkeypatch):
    # AC-11: an explosion inside discovery internals never escapes boot — the
    # config still builds declared-only with the one internal-error line.
    monkeypatch.setenv("MC_WALL_DB", str(tmp_path / "td-1.db"))
    d = tmp_path / "td-1-crash"
    declared = td_note(d, "td-1-declared.md")
    (tmp_path / "wall.json").write_text(json.dumps(
        {"token": "t", "programs": [{"program": "p0", "tag": "p0",
                                     "note_glob": declared}]}), encoding="utf-8")

    def _boom(declared_programs):
        raise RuntimeError("td-1 boom")

    monkeypatch.setattr(discovery, "search_dirs", _boom)
    cfg = build_tower_config(tmp_path)
    assert cfg.programs == (ProgramConfig("p0", "p0", declared, None),)
    assert cfg.discovery_degraded == ("discovery degraded: internal error (RuntimeError)",)


def test_td1_degraded_seeding(tmp_path, monkeypatch):
    # AC-12: TowerConfig.discovery_degraded seeds the collect document's
    # degraded list as group 7 — after the existing note groups, in emission
    # order; a clean world carries no discovery line; the field defaults to ().
    cfg, _ = mcwallt_world(tmp_path, monkeypatch, rows=["| W1-L1 | W1 |"],
                           discovery_degraded=("discovery degraded: unit probe",))
    assert collect_state(cfg)["server"]["degraded"] == ["note rows skipped: 1",
                                                        "discovery degraded: unit probe"]
    cfg_clean, _ = mcwallt_world(tmp_path, monkeypatch, rows=["| W1-L1 | W1 |"],
                                 name="mcwallt_world_clean", discovery_degraded=())
    assert collect_state(cfg_clean)["server"]["degraded"] == ["note rows skipped: 1"]
    assert TowerConfig(db_path="x", programs=()).discovery_degraded == ()
