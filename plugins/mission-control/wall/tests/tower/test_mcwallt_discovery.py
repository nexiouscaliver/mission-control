"""td1 discovery tests: AC-1,2,3,4,7,9,10,10b,11,12 (spec
mcwall-tower-discovery; R1 core + plumbing). The module-level
``from mc_wall.tower import discovery`` fails collection until the GREEN
phase lands ``mc_wall/tower/discovery.py`` — the accepted RED state."""

import json
import logging

import pytest

from mc_wall.server.tower_boot import build_tower_config, tower_config_from_wall
from mc_wall.tower import (ProgramConfig, RepoConfig, TowerConfig, collect_state,
                           discovery)
from mc_wall.tower import collect as collect_module
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


def test_td1_disabled_log_lands_via_collect(tmp_path, monkeypatch, caplog):
    # td1 review fix (decision 3b): on the __main__ boot path load_config runs
    # BEFORE setup_logging, so collect_state re-emits the disabled line itself —
    # ONCE per process even across configs; a discovery-on config stays silent.
    # The once-flag reset keeps the assert robust against earlier collects in
    # the same pytest process.
    monkeypatch.setattr(collect_module, "_DISCOVERY_DISABLED_LOGGED", False)
    cfg1, _ = mcwallt_world(tmp_path, monkeypatch, discovery_disabled=True)
    cfg2, _ = mcwallt_world(tmp_path, monkeypatch, name="mcwallt_world_two",
                            discovery_disabled=True)
    caplog.set_level(logging.INFO, logger="mc_wall.server")
    collect_state(cfg1)
    collect_state(cfg2)
    assert [r.getMessage() for r in caplog.records].count(
        "MC_WALL_DISCOVERY set — boot-time discovery disabled") == 1
    caplog.clear()
    cfg_on, _ = mcwallt_world(tmp_path, monkeypatch, name="mcwallt_world_on")
    collect_state(cfg_on)
    assert [r for r in caplog.records if r.name == "mc_wall.server"] == []


# --- td2: R2+R3 repos derivation (AC-5,6,8 + review coverage nits) ----------


def test_td1_repos_derivation(tmp_path, monkeypatch):
    # AC-5: repo path tokens on an accepted candidate's rows become RepoConfig
    # entries appended AFTER the declared repos, in first-row-appearance
    # order, deduped by expanded path (~ via the temp HOME, decision 10);
    # host is inferred from the (fetch) URLs; zero real subprocess.
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "td-1-repo-a").mkdir()
    (tmp_path / "td-1-home-b").mkdir()
    (tmp_path / "td-1-declared").mkdir()

    def fake_git(path):
        if "repo-a" in path:
            return (0, "origin\thttps://github.com/o/a.git (fetch)\n")
        if "home-b" in path:
            return (0, "origin\thttps://gitlab.com/o/b.git (fetch)\n")
        return (1, "")

    monkeypatch.setattr(discovery, "_run_git", fake_git)
    d = tmp_path / "td-1-rep"
    declared_glob = td_note(d, "td-1-declared.md")
    cand = d / "mission-control-derive-program.md"
    repo_a = tmp_path / "td-1-repo-a"
    cand.write_text("\n".join(["objective: td-1 derive", MCWALLT_HEADER_A, MCWALLT_SEP,
        f"| W1-L1 | W1 | L1 | {repo_a} | n/a | n/a | n/a | done |",
        "| W1-L2 | W1 | L2 | ~/td-1-home-b | n/a | n/a | n/a | done |",
        f"| W1-L3 | W1 | L3 | {repo_a} | n/a | n/a | n/a | done |",
    ]) + "\n", encoding="utf-8")
    data = td_data(declared_glob)
    data["repos"] = [{"name": "td-1-declared", "path": str(tmp_path / "td-1-declared"),
                      "host": "gitlab"}]
    cfg = tower_config_from_wall(data, tmp_path)
    declared_entry = RepoConfig("td-1-declared", str(tmp_path / "td-1-declared"), "gitlab")
    assert cfg.repos == (declared_entry,
                         RepoConfig("td-1-repo-a", str(repo_a), "github"),
                         RepoConfig("td-1-home-b", str(tmp_path / "td-1-home-b"), "gitlab"))
    assert cfg.discovery_degraded == ()


def test_td1_repo_skip_degraded(tmp_path, monkeypatch):
    # AC-6: seven isolated skip classes — each costs exactly its decision-2
    # line and keeps the repo out of the boot set (checks in decision-6 order:
    # isdir -> name -> conflict -> git rc -> fetch URL); the same-path repeat
    # across two discovered notes is the one SILENT case. _run_git always faked.
    ok_fetch = "origin\thttps://gitlab.com/o/ok.git (fetch)\n"

    def boot(sub, token, declared_repos=(), git=None):
        monkeypatch.setattr(discovery, "_run_git",
                            git if git is not None else lambda path: (0, ok_fetch))
        d = tmp_path / sub
        declared = td_note(d, "td-1-declared.md")
        td_note(d, "mission-control-cand-program.md", repo=token)
        data = td_data(declared)
        data["repos"] = list(declared_repos)
        return tower_config_from_wall(data, tmp_path)

    absent = tmp_path / "td-1-absent-repo"
    cfg = boot("td-1-skip-a", str(absent))
    assert cfg.repos == ()
    assert cfg.discovery_degraded == (f"discovery degraded: repo {absent} not a directory",)

    file_repo = tmp_path / "td-1-file-repo"
    file_repo.write_text("", encoding="utf-8")
    cfg = boot("td-1-skip-b", str(file_repo))
    assert cfg.repos == ()
    assert cfg.discovery_degraded == (f"discovery degraded: repo {file_repo} not a directory",)

    dir_c = tmp_path / "td-1-rc-repo"
    dir_c.mkdir()
    cfg = boot("td-1-skip-c", str(dir_c), git=lambda path: (1, ""))
    assert cfg.repos == ()
    assert cfg.discovery_degraded == (f"discovery degraded: repo {dir_c} git remote failed",)

    dir_d = tmp_path / "td-1-push-repo"
    dir_d.mkdir()
    cfg = boot("td-1-skip-d", str(dir_d),
               git=lambda path: (0, "origin\tgit@x:o/p.git (push)\n"))
    assert cfg.repos == ()
    assert cfg.discovery_degraded == (f"discovery degraded: repo {dir_d} has no git remote",)

    cfg = boot("td-1-skip-e", "/")
    assert cfg.repos == ()
    assert cfg.discovery_degraded == ("discovery degraded: repo / has no name",)

    dir_f = tmp_path / "td-1-clash"
    dir_f.mkdir()
    declared_clash = tmp_path / "td-1-declared-clash"
    cfg = boot("td-1-skip-f", str(dir_f),
               declared_repos=[{"name": "td-1-clash", "path": str(declared_clash),
                                "host": "gitlab"}])
    assert cfg.repos == (RepoConfig("td-1-clash", str(declared_clash), "gitlab"),)
    assert cfg.discovery_degraded == (
        f"discovery degraded: repo name td-1-clash at {dir_f}"
        f" conflicts with {declared_clash}",)

    dir_g = tmp_path / "td-1-silent-repo"
    dir_g.mkdir()
    monkeypatch.setattr(discovery, "_run_git", lambda path: (0, ok_fetch))
    d = tmp_path / "td-1-skip-g"
    declared = td_note(d, "td-1-declared.md")
    td_note(d, "mission-control-sil-one-program.md", repo=str(dir_g))
    td_note(d, "mission-control-sil-two-program.md", repo=str(dir_g))
    cfg = tower_config_from_wall(td_data(declared), tmp_path)
    assert cfg.repos == (RepoConfig("td-1-silent-repo", str(dir_g), "gitlab"),)
    assert cfg.discovery_degraded == ()


def test_td1_e2e_state_document(tmp_path, monkeypatch):
    # AC-8 (consuming surface): a discovered program flows through
    # collect_state in the declared data shape — objective, note_path, lanes —
    # and its lane's repo resolves to the DERIVED RepoConfig's name; the
    # malformed sibling surfaces as the single seeded degraded line. The repo
    # dir must EXIST or _derive_repos skips it and the lane-repo assert fails
    # for a non-obvious reason.
    monkeypatch.setenv("MC_WALL_DB", mcwallt_make_db(tmp_path))
    e2e_repo = tmp_path / "td-1-e2e-repo"
    e2e_repo.mkdir()
    monkeypatch.setattr(discovery, "_run_git",
                        lambda path: (0, "origin\thttps://gitlab.com/o/e2e.git (fetch)\n"))
    monkeypatch.setattr(netcache_module, "_run_cmd",
                        mcwallt_fake_cmd(mcwallt_world_default_handler)[0])
    d = tmp_path / "td-1-e2e"
    alpha_glob = td_note(d, "alpha-declared.md", objective="td-1 alpha")
    cand = d / "mission-control-e2e-prog-program.md"
    cand.write_text("\n".join(["objective: td-1 objective", MCWALLT_HEADER_A, MCWALLT_SEP,
        f"| W1-L1 | W1 | L1 | {e2e_repo} loop/mcwall-tower-discovery | td-1-slug | n/a | !5 | done |",
    ]) + "\n", encoding="utf-8")
    malformed = d / "mission-control-e2e-bad-program.md"
    malformed.write_text("\n".join([MCWALLT_HEADER_A, MCWALLT_SEP,
        "| W1-L1 | W1 | L1 | n/a | n/a | n/a | n/a | done |"]) + "\n", encoding="utf-8")
    doc = collect_state(tower_config_from_wall(td_wall("alpha", alpha_glob), tmp_path))
    programs = {p["program"]: p for p in doc["programs"]}
    assert set(programs) == {"alpha", "e2e-prog"}
    e2e, alpha = programs["e2e-prog"], programs["alpha"]
    assert e2e["objective"] == "td-1 objective"
    assert e2e["note_path"] == str(cand)
    assert sorted(e2e) == sorted(alpha)
    assert e2e["lanes"][0]["repo"] == "td-1-e2e-repo"
    assert doc["server"]["degraded"] == [f"discovery degraded: no objective in {malformed}"]


def test_td1_empty_dirname_glob(tmp_path):
    # td1-review coverage nit (spec Edge cases): a bare "*.md" glob contributes
    # NO search dir, but its mere presence must not force fallback-only mode —
    # the sibling real-dir glob still scans and discovers its candidate.
    real = tmp_path / "td-1-real"
    real_declared = td_note(real, "td-1-declared.md")
    cand = td_note(real, "mission-control-cand-program.md")
    cfg = tower_config_from_wall(td_data("*.md", real_declared), tmp_path)
    assert cfg.programs == (ProgramConfig("p0", "p0", "*.md", None),
                            ProgramConfig("p1", "p1", real_declared, None),
                            ProgramConfig("cand", "cand", cand, None))
    assert cfg.discovery_degraded == ()


def test_td1_degraded_multi_line_order(tmp_path, monkeypatch):
    # td1-review coverage nit (decision 7): multiple boot lines seed in
    # EMISSION order as group 7 — after the note groups, line-a before line-b.
    cfg, _ = mcwallt_world(tmp_path, monkeypatch, rows=["| W1-L1 | W1 |"],
                           discovery_degraded=("discovery degraded: line-a",
                                               "discovery degraded: line-b"))
    assert collect_state(cfg)["server"]["degraded"] == ["note rows skipped: 1",
                                                        "discovery degraded: line-a",
                                                        "discovery degraded: line-b"]
