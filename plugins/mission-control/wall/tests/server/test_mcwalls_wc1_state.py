"""wc1 — server-side collect-cost pins (2026-10-07 one-shot, v1.12.1).

- the recalibrated deadline DEFAULTS (state 10→15 s, boot 60→180 s — the
  measured cold warm-collect is ~78-80 s at 8 programs; both env knobs stay
  overridable, the FATAL path untouched);
- /state end-to-end serves CACHE-FIRST: with every network spawn slow, a
  cold /state answers 200 in well under the deadline with honest null
  signals (no degraded entries — pending is absence), and the background
  refreshes warm the cache so the FOLLOW-UP poll answers populated.
"""

import time

from tests.tower.conftest import (MCWALLT_HEADER_A, MCWALLT_SEP,
                                  mcwallt_fake_cmd, mcwallt_make_db,
                                  mcwallt_make_note, mcwallt_settable_clock)
from tests.tower.test_mcwallt_wc1_collect_cost import _wc1_world


def test_wc1_deadline_defaults_recalibrated():
    """Measured basis: cold warm-collect 80.24 s (incident log) / 78.00 s
    (rig) — the boot default must clear it with headroom; the state default
    backs the now-fast serve collects. Knobs stay env-overridable (wd1)."""
    from mc_wall.server import app

    assert app.DEFAULT_STATE_DEADLINE_S == 15.0
    assert app.DEFAULT_BOOT_DEADLINE_S == 180.0
    assert app.STATE_DEADLINE_ENV == "MC_WALL_STATE_DEADLINE_S"
    assert app.BOOT_DEADLINE_ENV == "MC_WALL_BOOT_DEADLINE_S"


def test_wc1_state_cold_serve_never_blocks_on_spawns(tmp_path, monkeypatch):
    """THE /state regression: with every spawn sleeping 0.4 s (a cold 5s+
    collect serialized), a cold serve-mode /state answers 200 fast — null
    signals, EMPTY degraded (pending = absence) — and the background
    refreshes have warmed the cache by the next poll."""
    from mc_wall.tower import netcache as netcache_module
    from mc_wall.tower.collect import collect_state
    from tests.server.mcwalls_harness import serve

    repo = tmp_path / "repo"
    repo.mkdir()
    from mc_wall.tower import RepoConfig
    repos = (RepoConfig(name="wc1-repo", path=str(repo), host="gitlab"),)
    rows = [f"| W1-L{i} | W1 | L{i} | {repo} loop/wc1-s{i} | n/a | n/a | n/a | launched |"
            for i in range(5)]

    def slow(argv, cwd):
        time.sleep(0.4)
        if argv[:2] == ["git", "ls-remote"]:
            return (0, f"sha\trefs/heads/{argv[3]}\n")
        return (0, "[]")

    fake, _calls = mcwallt_fake_cmd(slow)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    cfg, _set = _wc1_world(tmp_path, rows, repos)

    with serve(collect_state=lambda: collect_state(cfg, serve=True)) as h:
        t0 = time.monotonic()
        r = h.http("GET", f"/{h.token}/state")
        elapsed = time.monotonic() - t0
        assert r.status == 200, r.body[:200]
        assert elapsed < 2.0, f"cold /state must not wait on spawns ({elapsed:.2f}s)"
        body = r.json()
        lanes = body["programs"][0]["lanes"]
        assert all(l["signals"]["pushed"] is None for l in lanes)
        assert body["server"]["degraded"] == []  # pending is absence, not failure

        assert cfg.network_cache.wait_refreshes(timeout_s=30)
        t0 = time.monotonic()
        r2 = h.http("GET", f"/{h.token}/state")
        elapsed2 = time.monotonic() - t0
        assert r2.status == 200
        assert elapsed2 < 2.0
        lanes2 = r2.json()["programs"][0]["lanes"]
        assert all(l["signals"]["pushed"] == {"value": True, "age_s": 0}
                   for l in lanes2), "the background warm must reach the next poll"
