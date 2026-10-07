"""wc1 — collect-cost remediation tests (2026-10-07 one-shot, v1.12.1).

The incident: at 8 programs × 5 scanned repos the cold collect measured
80.24 s (55 spawns, ~78 s of serialized spawn time — rig BEFORE receipt:
78.00 s cold / 0.50 s warm), over the 60 s boot default (FATAL loop until
the operator's run.sh raised it) and intermittently over the 10 s state
deadline on TTL-expiry polls. These tests pin the remediation:

- bounded parallelism: warm-cycle probes run on ONE shared pool whose
  worker count is min(8, cpus), actually overlapping (wall-clock shrinks)
  and never exceeding the bound;
- parallel correctness: per-lane/per-repo probe results land on THEIR OWN
  lanes (no netcache-key cross-contamination under concurrency) and the
  document stays contract-shaped;
- cache-first serving (serve=True): a cold collect NEVER blocks on a spawn
  (nulls + pending, no degraded entry), an expired entry serves the last
  good value immediately with its honest age (mr.fetched_age_s /
  pushed.age_s / merges_age_s), and the background refresh warms the cache
  for the next poll — through the SAME NetCache single-flight.

Every test monkeypatches ``netcache._run_cmd`` — zero real subprocesses,
zero network (the hermeticity rule).
"""

import json
import threading
import time
from datetime import datetime

from mc_wall.tower import NetCache, ProgramConfig, RepoConfig, TowerConfig, contract
from mc_wall.tower import netcache as netcache_module
from mc_wall.tower import signals as signals_module
from mc_wall.tower.collect import collect_state
from mc_wall.tower.netcache import ServingCache, spawn_workers
from tests.tower.conftest import (MCWALLT_HEADER_A, MCWALLT_SEP,
                                  mcwallt_fake_cmd, mcwallt_make_db,
                                  mcwallt_make_note, mcwallt_settable_clock)

NOW = 2_000_000_050.0


def _wc1_world(tmp_path, rows, repos, clock_start=NOW, name="wc1"):
    """One program + N rows + M repos over a settable clock and a fresh cache."""
    note = mcwallt_make_note(tmp_path, name + "_note.md",
                             ["objective: wc1", MCWALLT_HEADER_A, MCWALLT_SEP, *rows])
    now_s, set_now = mcwallt_settable_clock(clock_start)
    cfg = TowerConfig(
        db_path=mcwallt_make_db(tmp_path, name=name + ".db"),
        programs=(ProgramConfig(program="wc1-prog", tag="t", note_glob=note),),
        repos=tuple(repos),
        pending_launch_path=str(tmp_path / (name + "_launch.json")),
        now_s=now_s,
        network_cache=NetCache(),
        forge_root=str(tmp_path / (name + "_forge")))
    return cfg, set_now


# ------------------------------------------------------------- pool bound

def test_wc1_spawn_workers_is_machine_sane():
    """The ONE bound for every pool consumer: min(8, cpus)."""
    import os
    assert spawn_workers() == min(8, os.cpu_count() or 1)
    assert spawn_workers() <= 8
    assert spawn_workers() >= 1


def test_wc1_warm_collect_parallelism_bounded_and_real(tmp_path, monkeypatch):
    """A warm collect's lane probes overlap (real speedup) and the PEAK
    concurrent spawn count never exceeds spawn_workers()."""
    repo = tmp_path / "repo"
    repo.mkdir()
    repos = (RepoConfig(name="wc1-repo", path=str(repo), host="gitlab"),)
    rows = [f"| W1-L{i} | W1 | L{i} | {repo} loop/wc1-b{i} | n/a | n/a | n/a | launched |"
            for i in range(12)]  # 12 lanes -> 24 spawns (ls-remote + mr list each)

    live = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def slow(argv, cwd):
        with lock:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
        time.sleep(0.08)
        with lock:
            live["now"] -= 1
        if argv[:2] == ["git", "ls-remote"]:
            return (0, "")
        return (0, "[]")

    fake, _calls = mcwallt_fake_cmd(slow)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    cfg, _set = _wc1_world(tmp_path, rows, repos)
    t0 = time.monotonic()
    state = collect_state(cfg)  # warm mode: pooled probes
    elapsed = time.monotonic() - t0
    assert live["peak"] >= 2, f"probes must actually overlap (peak={live['peak']})"
    assert live["peak"] <= spawn_workers(), (
        f"peak concurrency {live['peak']} exceeded the bound {spawn_workers()}")
    # 24 spawns x 0.08s = 1.92s serialized; pooled at >=2-way overlap the
    # same work finishes well under the serial floor.
    assert elapsed < 24 * 0.08, f"warm collect did not parallelize ({elapsed:.2f}s)"
    contract.assert_shape(state)


# ------------------------------------------------- parallel correctness

def test_wc1_parallel_probes_no_cross_contamination(tmp_path, monkeypatch):
    """Distinct lanes/repos keep THEIR OWN results under pool parallelism:
    every lane's pushed value and mr title/branch match the argv that lane's
    probes scripted — a key/result mixup anywhere fails loud."""
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    repo_a.mkdir()
    repo_b.mkdir()
    repos = (RepoConfig(name="wc1-a", path=str(repo_a), host="gitlab"),
             RepoConfig(name="wc1-b", path=str(repo_b), host="github"))
    rows = ([f"| W1-L{i} | W1 | L{i} | {repo_a} loop/wc1-a{i} | n/a | n/a | n/a | launched |"
             for i in range(4)]
            + [f"| W2-L{i} | W2 | L{i} | {repo_b} loop/wc1-b{i} | n/a | n/a | n/a | launched |"
               for i in range(4)])

    def handler(argv, cwd):
        # ls-remote: HIT only for the exact branch asked (argv[3]).
        if argv[:2] == ["git", "ls-remote"]:
            return (0, f"sha-{''.join(argv[3])}\trefs/heads/{argv[3]}\n")
        if argv[0] == "glab" and argv[2] == "list":
            branch = argv[4]
            return (0, json.dumps([{"iid": 100 + int(branch[-1]), "state": "opened",
                                    "title": f"glab-{branch}",
                                    "created_at": "2026-09-19T09:00:00Z"}]))
        if argv[0] == "gh" and argv[2] == "list":
            branch = argv[argv.index("--head") + 1]
            return (0, json.dumps([{"number": 200 + int(branch[-1]), "state": "OPEN",
                                    "title": f"gh-{branch}",
                                    "createdAt": "2026-09-19T09:00:00Z"}]))
        return (0, "[]")

    # Vary spawn latency per key so completion order differs from submission
    # order — the race the document-order gather must be immune to.
    delays = {"n": 0}

    def jittered(argv, cwd):
        delays["n"] += 1
        time.sleep(0.02 * (delays["n"] % 5))
        return handler(argv, cwd)

    fake, calls = mcwallt_fake_cmd(jittered)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    cfg, _set = _wc1_world(tmp_path, rows, repos)
    state = collect_state(cfg)
    contract.assert_shape(state)
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    for i in range(4):
        la, lb = lanes[f"W1-L{i}"], lanes[f"W2-L{i}"]
        assert la["signals"]["pushed"] == {"value": True, "age_s": 0}
        assert lb["signals"]["pushed"] == {"value": True, "age_s": 0}
        assert la["signals"]["mr"]["ref"] == f"!{100 + i}"
        assert la["signals"]["mr"]["title"] == f"glab-loop/wc1-a{i}"
        assert lb["signals"]["mr"]["ref"] == f"#{200 + i}"
        assert lb["signals"]["mr"]["title"] == f"gh-loop/wc1-b{i}"
        assert la["signals"]["mr"]["fetched_age_s"] == 0  # fresh fetch
    # Each lane's exact argv ran EXACTLY once (single-flight + no re-spawn):
    ls_argv = sorted(" ".join(a) for a in calls["argv"] if a[:2] == ["git", "ls-remote"])
    assert len(ls_argv) == 8 and len(set(ls_argv)) == 8


# ----------------------------------------------------- cache-first serving

def test_wc1_serve_cold_never_blocks_and_warms_behind(tmp_path, monkeypatch):
    """THE headline behavior: a cold serve-mode collect returns in
    milliseconds while every spawn is slow — signals render the honest
    nulls with NO degraded entry (pending = absence, not failure) — and the
    kicked background refreshes warm the cache for the NEXT poll."""
    repo = tmp_path / "repo"
    repo.mkdir()
    repos = (RepoConfig(name="wc1-repo", path=str(repo), host="gitlab"),)
    rows = [f"| W1-L{i} | W1 | L{i} | {repo} loop/wc1-w{i} | n/a | n/a | n/a | launched |"
            for i in range(6)]

    def slow(argv, cwd):
        time.sleep(0.4)  # 14+ spawns x 0.4s = 5.6s+ serialized
        if argv[:2] == ["git", "ls-remote"]:
            return (0, f"sha\trefs/heads/{argv[3]}\n")
        if argv[0] == "glab" and argv[2] == "list":
            return (0, json.dumps([{"iid": 7, "state": "opened", "title": "warmed mr",
                                    "created_at": "2026-09-19T09:00:00Z"}]))
        return (0, "[]")

    fake, _calls = mcwallt_fake_cmd(slow)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    cfg, _set = _wc1_world(tmp_path, rows, repos)
    t0 = time.monotonic()
    state = collect_state(cfg, serve=True)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, f"a cold serve collect must not block on spawns ({elapsed:.2f}s)"
    contract.assert_shape(state)
    lanes = state["programs"][0]["lanes"]
    assert all(l["signals"]["pushed"] is None for l in lanes)  # honest nulls
    assert all(l["signals"]["mr"] is None for l in lanes)
    # pending is ABSENCE: no network-degraded vocabulary fires on a cold serve
    assert state["server"]["degraded"] == []
    assert state["merges"] == [] and state["merges_age_s"] == 0
    # the refreshes warm the cache behind the poll...
    assert cfg.network_cache.wait_refreshes(timeout_s=30), "background refreshes must finish"
    # ...and the next serve poll answers from cache, fast and populated
    t0 = time.monotonic()
    warmed = collect_state(cfg, serve=True)
    elapsed2 = time.monotonic() - t0
    assert elapsed2 < 1.0
    wl = warmed["programs"][0]["lanes"]
    assert all(l["signals"]["pushed"] == {"value": True, "age_s": 0} for l in wl)
    assert all(l["signals"]["mr"] is not None and l["signals"]["mr"]["ref"] == "!7"
               for l in wl)
    assert warmed["server"]["degraded"] == []


def test_wc1_serve_stale_serves_last_good_with_age(tmp_path, monkeypatch):
    """An expired entry serves the LAST GOOD value immediately (stale=True
    never as fresh) with its honest age — mr.fetched_age_s / pushed.age_s /
    merges_age_s (the age of the observation, fresh or stale) — and the
    kicked refresh replaces it for the next poll."""
    repo = tmp_path / "repo"
    repo.mkdir()
    repos = (RepoConfig(name="wc1-repo", path=str(repo), host="gitlab"),)
    rows = [f"| W1-L1 | W1 | L1 | {repo} loop/wc1-stale | n/a | n/a | n/a | launched |"]
    versions = {"v": 1}

    def handler(argv, cwd):
        if argv[:2] == ["git", "ls-remote"]:
            return (0, f"sha{versions['v']}\trefs/heads/{argv[3]}\n")
        if argv[:2] == ["git", "remote"]:
            return (0, "origin\tgit@gitlab.com:g/x.git (fetch)\n")
        if argv[0] == "glab" and argv[2] == "list":
            v = versions["v"]
            if "--source-branch" in argv:  # the lane's by-branch signal lookup
                return (0, json.dumps([{"iid": v, "state": "opened",
                                        "title": f"mr v{v}",
                                        "created_at": "2026-09-19T09:00:00Z"}]))
            # the registry's per-repo list (-R slug --all): carries the branch
            return (0, json.dumps([{
                "iid": v, "state": "opened", "title": f"reg v{v}",
                "source_branch": "loop/wc1-stale",
                "created_at": "2026-09-19T09:00:00Z",
                "updated_at": "2026-09-19T09:00:00Z"}]))
        return (0, "[]")

    fake, _calls = mcwallt_fake_cmd(handler)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    cfg, set_now = _wc1_world(tmp_path, rows, repos)

    warm = collect_state(cfg)  # populate at T
    assert warm["programs"][0]["lanes"][0]["signals"]["mr"]["title"] == "mr v1"
    assert [m["title"] for m in warm["merges"]] == ["reg v1"]
    assert warm["merges_age_s"] == 0  # observed this cycle

    versions["v"] = 2
    set_now(NOW + 121)  # past the 120 s signals TTL; registry TTL is 300 (still fresh)
    t0 = time.monotonic()
    stale = collect_state(cfg, serve=True)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, "an expired entry must serve without waiting on the spawn"
    lane = stale["programs"][0]["lanes"][0]
    # LAST GOOD served — v1, honestly aged 121 s, never as fresh:
    assert lane["signals"]["mr"]["title"] == "mr v1"
    assert lane["signals"]["mr"]["fetched_age_s"] == 121
    assert lane["signals"]["pushed"] == {"value": True, "age_s": 121}
    # the registry entry is still inside its 300 s TTL: same rows, honest age
    # of the observation (the age is the marker — freshness is the TTL's call)
    assert [m["title"] for m in stale["merges"]] == ["reg v1"]
    assert stale["merges_age_s"] == 121

    cfg.network_cache.wait_refreshes(timeout_s=10)
    set_now(NOW + 122)
    refreshed = collect_state(cfg, serve=True)
    lane2 = refreshed["programs"][0]["lanes"][0]
    assert lane2["signals"]["mr"]["title"] == "mr v2"  # background refresh landed
    assert lane2["signals"]["mr"]["fetched_age_s"] == 1
    assert refreshed["merges_age_s"] == 122  # registry observed at NOW (not yet expired)

    # Past the registry's own 300 s TTL: the stale registry serves the last
    # good rows immediately with their age, refresh lands for the next poll.
    set_now(NOW + 301)
    reg = collect_state(cfg, serve=True)
    assert [m["title"] for m in reg["merges"]] == ["reg v1"]
    assert reg["merges_age_s"] == 301
    cfg.network_cache.wait_refreshes(timeout_s=10)
    set_now(NOW + 302)
    reg2 = collect_state(cfg, serve=True)
    assert [m["title"] for m in reg2["merges"]] == ["reg v2"]
    assert reg2["merges_age_s"] == 1


def test_wc1_serve_failure_keeps_degraded_vocabulary(tmp_path, monkeypatch):
    """Serve mode keeps the FAILURE vocabulary: a key that TRIED and failed
    (inside its backoff window) returns pending=False — nulls WITH the
    network-degraded entry — never a silent gap."""
    repo = tmp_path / "repo"
    repo.mkdir()
    repos = (RepoConfig(name="wc1-repo", path=str(repo), host="gitlab"),)
    rows = [f"| W1-L1 | W1 | L1 | {repo} loop/wc1-dead | n/a | n/a | n/a | launched |"]

    def failing(argv, cwd):
        return (1, "")

    fake, _calls = mcwallt_fake_cmd(failing)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    cfg, _set = _wc1_world(tmp_path, rows, repos)
    cold = collect_state(cfg, serve=True)
    assert cold["server"]["degraded"] == []  # first touch: pending, not failure
    cfg.network_cache.wait_refreshes(timeout_s=10)  # the refresh FAILS (rc 1)
    # Second serve: the key is inside its 30 s failure backoff — a REAL
    # failure now: nulls + the degraded entries, same as the blocking path.
    flapped = collect_state(cfg, serve=True)
    lane = flapped["programs"][0]["lanes"][0]
    assert lane["signals"]["pushed"] is None and lane["signals"]["mr"] is None
    assert flapped["server"]["degraded"] == [
        "network degraded: git wc1-repo", "network degraded: mr wc1-repo"]


def test_wc1_serve_cache_facade_unit(tmp_path, monkeypatch):
    """ServingCache is a pure router: same signature, fetch_cached under the
    hood; FetchResult carries the stale/pending markers with their defaults
    off (the blocking path's shape is unchanged)."""
    cache = NetCache()
    served = ServingCache(cache)
    now_s, set_now = mcwallt_settable_clock(1_000.0)
    argv = ["git", "ls-remote", "origin", "b"]
    key = ("git_ls_remote", "r", "b")
    fake, calls = mcwallt_fake_cmd(lambda a, c: (0, "out"))
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)

    r0 = served.fetch(key, argv, "cwd", now_s, 120, 30.0, 600.0)
    assert (r0.ok, r0.stdout, r0.stale, r0.pending) == (False, "", False, True)
    cache.wait_refreshes(timeout_s=10)
    assert calls["n"] == 1
    r1 = served.fetch(key, argv, "cwd", now_s, 120, 30.0, 600.0)
    assert (r1.ok, r1.stdout, r1.stale, r1.pending) == (True, "out", False, False)
    set_now(1_121.0)
    r2 = served.fetch(key, argv, "cwd", now_s, 120, 30.0, 600.0)
    assert (r2.ok, r2.stdout, r2.stale) == (True, "out", True)  # last good, honestly stale
    cache.wait_refreshes(timeout_s=10)
    assert calls["n"] == 2  # exactly one background re-spawn


def test_wc1_warm_mode_still_blocks_and_single_flights(tmp_path, monkeypatch):
    """The warm path's blocking contract is unchanged: fetch waits for the
    spawn, one spawn per key, TTL reuse across collect cycles (the wc1
    refactor must not have loosened AC-CACHE-1..3)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    repos = (RepoConfig(name="wc1-repo", path=str(repo), host="gitlab"),)
    rows = [f"| W1-L1 | W1 | L1 | {repo} loop/wc1-warm | n/a | n/a | n/a | launched |"]
    fake, calls = mcwallt_fake_cmd(lambda argv, cwd:
                                   (0, f"sha\trefs/heads/{argv[3]}\n") if argv[:2] == ["git", "ls-remote"]
                                   else (0, "[]"))
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    cfg, _set = _wc1_world(tmp_path, rows, repos)
    collect_state(cfg)
    collect_state(cfg)  # same config -> shared cache: everything TTL-fresh
    ls = [a for a in calls["argv"] if a[:2] == ["git", "ls-remote"]]
    assert len(ls) == 1, "warm collects must keep per-key single-flight + TTL reuse"


def test_wc1_deterministic_document_under_pool(tmp_path, monkeypatch):
    """Two warm collects over jittered spawn latencies produce IDENTICAL
    documents (degraded lines included) — pool completion order never leaks
    into the payload."""
    repo = tmp_path / "repo"
    repo.mkdir()
    repos = (RepoConfig(name="wc1-repo", path=str(repo), host="gitlab"),)
    rows = [f"| W1-L{i} | W1 | L{i} | {repo} loop/wc1-d{i} | n/a | n/a | n/a | launched |"
            for i in range(6)]
    n = {"i": 0}

    def jittered(argv, cwd):
        n["i"] += 1
        time.sleep(0.01 * (n["i"] % 7))
        if argv[:2] == ["git", "ls-remote"]:
            return (1, "")  # mixed outcomes: degraded lines exercise ordering
        return (0, "[]")

    fake, _calls = mcwallt_fake_cmd(jittered)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    cfg, _set = _wc1_world(tmp_path, rows, repos)
    import dataclasses
    a = collect_state(cfg)
    # Same world files, FRESH cache (a fresh per-cycle NetCache is exactly
    # what a second cold cycle sees): same clock, same scripted outcomes.
    cfg2 = dataclasses.replace(cfg, network_cache=NetCache())
    monkeypatch.setattr(netcache_module, "_run_cmd",
                        mcwallt_fake_cmd(jittered)[0])
    b = collect_state(cfg2)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
