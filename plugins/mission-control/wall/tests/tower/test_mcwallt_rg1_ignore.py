"""rg1 — wall.json ``ignore[]`` (registration commands D2, v1.12.0): discovery
never registers an ignored slug (a retired program's grammar-correct note
must not silently re-register), declared entries still win structurally, and
``tower_boot`` parses/validates the key loudly. Same fixture-only discipline
as the other tower files (mcwallt_ helpers, zero live reads)."""

from mc_wall.server import tower_boot
from mc_wall.tower import ProgramConfig, discovery

HEADER = "| id | wave | lane | repo/branch | slug | base | session/MR artifacts | status |"
SEP = "|---|---|---|---|---|---|---|---|"


def _note(tmp_path, slug, rows=("| W1-L1 | W1 | L1 | — | s | n/a | n/a | done |",)):
    p = tmp_path / ("mission-control-%s-program.md" % slug)
    p.write_text("objective: %s\n%s\n%s\n%s\n" % (slug, HEADER, SEP, "\n".join(rows)),
                 encoding="utf-8")
    return str(p)


def _fake_git_factory(host_urls):
    def run_git(path):
        return (0, "origin %s (fetch)\n" % host_urls)
    return run_git


def test_rg1_discovery_skips_ignored_slug(tmp_path):
    # A grammar-correct, undeclared note whose slug sits in ignore[] is NOT
    # registered — deregistration is real (D2's whole point).
    _note(tmp_path, "retired")
    declared = (ProgramConfig(program="live", tag="live",
                              note_glob=str(tmp_path / "none-*.md")),)
    disc = discovery.discover_programs(declared, run_git=_fake_git_factory(
        "git@example.com:x/y.git"), ignored_slugs=("retired",))
    assert [p.program for p in disc.programs] == []
    assert disc.degraded == ()


def test_rg1_discovery_registers_same_slug_without_ignore(tmp_path):
    # Control: the identical scan WITHOUT the ignore list finds it (the skip
    # is the ignore list's doing, not fixture shape).
    _note(tmp_path, "retired")
    declared = (ProgramConfig(program="live", tag="live",
                              note_glob=str(tmp_path / "none-*.md")),)
    disc = discovery.discover_programs(declared, run_git=_fake_git_factory(
        "git@example.com:x/y.git"))
    assert [p.program for p in disc.programs] == ["retired"]


def test_rg1_declared_beats_ignore(tmp_path):
    # A slug both declared and ignored still runs as declared — declared
    # entries always win structurally (register removes the ignore entry, but
    # the tower must never hide a declaration because of a stale ignore).
    note = _note(tmp_path, "dual")
    cfg_data = {"token": "t", "programs": [{"program": "dual", "tag": "dual",
                                            "note_glob": note}],
                "ignore": ["dual"]}
    cfg = tower_boot.tower_config_from_wall(cfg_data, str(tmp_path))
    assert [p.program for p in cfg.programs] == ["dual"]
    assert cfg.discovery_ignore == ("dual",)


def test_rg1_boot_parses_ignore_and_validates(tmp_path):
    note = _note(tmp_path, "kept")
    good = {"token": "t", "programs": [{"program": "kept", "tag": "kept",
                                        "note_glob": note}], "ignore": ["gone "]}
    cfg = tower_boot._declared_config(good, str(tmp_path))
    assert cfg.discovery_ignore == ("gone",)  # trimmed

    for bad in (["ok", 3], [""], "not-a-list", {"a": 1}):
        try:
            tower_boot._declared_config({"token": "t", "ignore": bad}, str(tmp_path))
        except ValueError as exc:
            assert '"ignore"' in str(exc)
        else:
            raise AssertionError("bad ignore %r parsed without error" % (bad,))

    # Absent / null -> () (a pre-1.12.0 wall.json keeps booting unchanged).
    assert tower_boot._declared_config({"token": "t"}, str(tmp_path)).discovery_ignore == ()
    assert tower_boot._declared_config({"token": "t", "ignore": None},
                                       str(tmp_path)).discovery_ignore == ()


def test_rg1_per_poll_refresh_carries_ignore(tmp_path, monkeypatch):
    # PerPollTowerConfig.current() re-runs discovery with the ignore list —
    # a deregistered program cannot reappear on a later poll.
    _note(tmp_path, "retired")
    note = _note(tmp_path, "live")
    data = {"token": "t", "programs": [{"program": "live", "tag": "live",
                                        "note_glob": note}], "ignore": ["retired"]}
    provider = tower_boot.PerPollTowerConfig(str(tmp_path), data)
    cfg = provider.current()
    assert [p.program for p in cfg.programs] == ["live"]
    assert cfg.discovery_ignore == ("retired",)
