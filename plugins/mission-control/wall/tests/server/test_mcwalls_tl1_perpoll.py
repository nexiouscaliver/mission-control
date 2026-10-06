"""W2-L1 server-side contract v2 tests: per-poll registration (wall.json mtime
re-check + discovery re-scan each collect cycle, no process restart) and the
POST /launch row resolution against a real tower document (B3 fix).
"""

import json
import os

NOTE_HEADER = ("| id | wave | lane | repo/branch | slug | base "
               "| session/MR artifacts | status |")
NOTE_SEP = "|---|---|---|---|---|---|---|---|"


def _write_note(dirpath, name, lines):
    p = dirpath / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def _wall_home(tmp_path, note_glob):
    home = tmp_path / "wallhome"
    home.mkdir()
    (home / "wall.json").write_text(json.dumps({
        "token": "tl1-token",
        "programs": [{"program": "tl1-declared", "tag": "tl1",
                      "note_glob": note_glob}],
        "repos": [],
    }), encoding="utf-8")
    return home


def test_tl1_perpoll_picks_up_wall_json_and_new_note(tmp_path, monkeypatch):
    """EXPECT-5: wall.json mtime touch AND a newly dropped program note are
    both visible to the next collect cycle without a process restart."""
    import mc_wall.server.tower_boot as tower_boot

    monkeypatch.delenv("MC_WALL_DISCOVERY", raising=False)  # discovery ON
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    declared_note = _write_note(notes_dir, "tl1_declared.md", [
        "objective: declared program",
        NOTE_HEADER, NOTE_SEP,
        "| D-1 | W1 | L1 | n/a | n/a | n/a | n/a | forged |",
    ])
    home = _wall_home(tmp_path, declared_note)
    provider = tower_boot.PerPollTowerConfig(home)
    cfg = provider.current()
    assert [p.program for p in cfg.programs] == ["tl1-declared"]

    # A new program note appears on disk (zero-beg registration): the NEXT
    # cycle's discovery re-scan finds it — no restart, no wall.json change.
    _write_note(notes_dir, "mission-control-tl1-fresh-program.md", [
        "objective: freshly forged program",
        NOTE_HEADER, NOTE_SEP,
        "| F-1 | W1 | L1 | n/a | n/a | n/a | n/a | forged |",
    ])
    cfg = provider.current()
    assert [p.program for p in cfg.programs] == ["tl1-declared", "tl1-fresh"]

    # wall.json itself changes (a second declared program) + mtime moves: the
    # config is rebuilt from wall.json on the next cycle.
    (home / "wall.json").write_text(json.dumps({
        "token": "tl1-token",
        "programs": [
            {"program": "tl1-declared", "tag": "tl1",
             "note_glob": declared_note},
            {"program": "tl1-added", "tag": "tl1-added",
             "note_glob": declared_note},
        ],
        "repos": [],
    }), encoding="utf-8")
    os.utime(home / "wall.json", (2_000_000_000, 2_000_000_000))
    cfg = provider.current()
    assert [p.program for p in cfg.programs] == \
        ["tl1-declared", "tl1-added", "tl1-fresh"]


def test_tl1_perpoll_no_rebuild_when_wall_json_unchanged(tmp_path, monkeypatch):
    import mc_wall.server.tower_boot as tower_boot

    monkeypatch.delenv("MC_WALL_DISCOVERY", raising=False)
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    declared_note = _write_note(notes_dir, "tl1_declared.md", [
        "objective: declared program",
        NOTE_HEADER, NOTE_SEP,
        "| D-1 | W1 | L1 | n/a | n/a | n/a | n/a | forged |",
    ])
    home = _wall_home(tmp_path, declared_note)
    provider = tower_boot.PerPollTowerConfig(home)
    calls = {"n": 0}
    real = tower_boot._declared_config

    def counting(data, wall_home):
        calls["n"] += 1
        return real(data, wall_home)

    monkeypatch.setattr(tower_boot, "_declared_config", counting)
    for _ in range(4):
        cfg = provider.current()
        assert [p.program for p in cfg.programs] == ["tl1-declared"]
    assert calls["n"] == 0, "unchanged wall.json must not rebuild the config"


def test_tl1_perpoll_survives_transient_bad_wall_json(tmp_path, monkeypatch):
    """A half-written wall.json mid-poll never crashes the collect cycle: the
    provider keeps serving the last good declared config."""
    import mc_wall.server.tower_boot as tower_boot

    monkeypatch.delenv("MC_WALL_DISCOVERY", raising=False)
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    declared_note = _write_note(notes_dir, "tl1_declared.md", [
        "objective: declared program",
        NOTE_HEADER, NOTE_SEP,
        "| D-1 | W1 | L1 | n/a | n/a | n/a | n/a | forged |",
    ])
    home = _wall_home(tmp_path, declared_note)
    provider = tower_boot.PerPollTowerConfig(home)
    assert [p.program for p in provider.current().programs] == ["tl1-declared"]
    (home / "wall.json").write_text("{ not json", encoding="utf-8")
    os.utime(home / "wall.json", (2_000_000_000, 2_000_000_000))
    cfg = provider.current()  # no raise: last good config stands
    assert [p.program for p in cfg.programs] == ["tl1-declared"]


def test_tl1_post_launch_resolves_real_tower_doc(tmp_path, monkeypatch):
    """EXPECT-6: find_row reads programs[].lanes[] — a POST /launch against a
    RAW tower document (no rows bridge) resolves the row instead of 404ing."""
    from mc_wall.tower import collect_state
    from tests.server.mcwalls_harness import FakeRunner, serve
    from tests.tower.conftest import mcwallt_world

    cfg, _set = mcwallt_world(tmp_path, monkeypatch)
    tower_doc = collect_state(cfg)
    with serve(collect_state=lambda: tower_doc, runner=FakeRunner(),
               state_dir=tmp_path / "state", log_dir=tmp_path / "logs") as h:
        r = h.http("POST", f"/{h.token}/launch",
                   {"row_id": "W1-L1", "repo_root": str(tmp_path)})
        assert r.status == 200
        assert r.json()["pending"]["status"] == "await-birth"


def test_tl1_run_server_accepts_provider_config(tmp_path, monkeypatch):
    """run_server resolves a PerPollTowerConfig via .current() per collect and
    a plain TowerConfig unchanged (duck-typed injection seam)."""
    import mc_wall.server.app as app
    import mc_wall.server.tower_boot as tower_boot

    monkeypatch.delenv("MC_WALL_DISCOVERY", raising=False)
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    declared_note = _write_note(notes_dir, "tl1_declared.md", [
        "objective: declared program",
        NOTE_HEADER, NOTE_SEP,
        "| D-1 | W1 | L1 | n/a | n/a | n/a | n/a | forged |",
    ])
    home = _wall_home(tmp_path, declared_note)
    provider = tower_boot.PerPollTowerConfig(home)
    resolved = app._resolve_tower_config(provider)
    assert resolved is not None
    assert [p.program for p in resolved.programs] == ["tl1-declared"]
    plain = tower_boot.build_tower_config(home)
    assert app._resolve_tower_config(plain) is plain
