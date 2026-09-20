"""T3 — scripts.mc_status: the text board renderer over the real tower.

Every test drives ``main([...])`` (or ``render(collect_state(...))``) against
the mcwalli fixture world or a synthetic degradation case — NEVER the real
network (the world monkeypatches ``netcache._run_cmd``; the degrade tests
carry no repos at all). All repo imports are INSIDE the test functions so the
RED phase fails per-item, not at collection.
"""

import json


def test_board_renders_from_fixture_world(tmp_path, monkeypatch, capsys):
    from scripts.mc_status import main
    from tests.integration.mcwalli_fixtures import mcwalli_tower_json, mcwalli_world

    monkeypatch.delenv("MC_WALL_TOWER_CONFIG", raising=False)
    monkeypatch.delenv("MC_WALL_DB", raising=False)
    monkeypatch.delenv("MC_WALL_HOME", raising=False)
    w = mcwalli_world(tmp_path, monkeypatch)
    cfgp = mcwalli_tower_json(w)

    assert main(["--config", cfgp, "--db", w.db_path]) == 0

    out = capsys.readouterr().out
    assert "mcwalli" in out
    assert "W1-L1" in out
    assert "verify queue" in out
    assert "human actions" in out
    assert "sessions unmapped" in out


def test_missing_config_degrades(tmp_path, monkeypatch, capsys):
    import time

    from scripts.mc_status import main
    from tests.integration.mcwalli_fixtures import mcwalli_union_db

    monkeypatch.delenv("MC_WALL_TOWER_CONFIG", raising=False)
    monkeypatch.delenv("MC_WALL_DB", raising=False)
    # Empty wall home: the resolved <wall_home>/tower.json does not exist.
    monkeypatch.setenv("MC_WALL_HOME", str(tmp_path / "empty-home"))
    now_ms = int(time.time() * 1000)
    fixture_db = mcwalli_union_db(tmp_path, sessions=[
        {"id": "sess_aa000000-0000-4000-8000-000000000001", "title": "loose session",
         "directory": str(tmp_path), "time_created": now_ms - 60_000,
         "time_updated": now_ms - 60_000}])

    assert main(["--db", fixture_db]) == 0

    out = capsys.readouterr().out
    assert "tower config not found" in out
    assert "(none configured)" in out
    # db-derived sections still render (the loose session shows up unmapped).
    assert "loose session" in out


def test_missing_db_degrades(tmp_path, capsys):
    from scripts.mc_status import main

    cfgp = tmp_path / "mcwalli_nodb_tower.json"
    cfgp.write_text(json.dumps({"programs": [], "repos": [],
                                "pending_launch_path": None}), encoding="utf-8")

    assert main(["--config", str(cfgp), "--db", str(tmp_path / "missing.db")]) == 0

    out = capsys.readouterr().out
    # The tower's own fail-open entry — surfaced by the renderer, never a crash.
    assert "tracking degraded: session store unreadable" in out
    assert "Traceback" not in out


def test_bad_config_shape_degrades(tmp_path, capsys):
    import time

    from scripts.mc_status import main
    from tests.integration.mcwalli_fixtures import mcwalli_union_db

    # Parses as JSON but the program row has an unknown key (and lacks the
    # required ones) — a shape failure, not a parse failure.
    bad = tmp_path / "bad_shape_tower.json"
    bad.write_text(json.dumps({"programs": [{"program": "x", "bogus_key": 1}],
                               "repos": [], "pending_launch_path": None}),
                   encoding="utf-8")
    now_ms = int(time.time() * 1000)
    fixture_db = mcwalli_union_db(tmp_path, sessions=[
        {"id": "sess_bb000000-0000-4000-8000-000000000002", "title": "stray session",
         "directory": str(tmp_path), "time_created": now_ms - 60_000,
         "time_updated": now_ms - 60_000}])

    assert main(["--config", str(bad), "--db", fixture_db]) == 0

    out = capsys.readouterr().out
    assert "tower config invalid: %s" % bad in out
    assert "(none configured)" in out
    # db-derived sections still render (the stray session shows up unmapped).
    assert "stray session" in out
    assert "Traceback" not in out


def test_bad_explicit_config_exit_2(tmp_path, capsys):
    from scripts.mc_status import main

    bad = tmp_path / "bad_tower.json"
    bad.write_text("{not json", encoding="utf-8")

    assert main(["--config", str(bad), "--db", str(tmp_path / "none.db")]) == 2

    captured = capsys.readouterr()
    assert len(captured.err.strip().splitlines()) == 1
    assert str(bad) in captured.err
