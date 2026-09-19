"""T-1/T-5 netcache tests: _run_cmd ok/error/exception paths (T-1, local
spawns only) and fetch TTL / single-flight / backoff / no-stale semantics
(AC-CACHE-1..5) plus the raising-_run_cmd degradation seam (plan F1). The
fetch tests monkeypatch ``netcache._run_cmd`` and drive injected clocks — zero
real subprocesses, zero network."""

import sys
import threading
import time

from mc_wall.tower import netcache as netcache_module
from mc_wall.tower.netcache import NetCache, _run_cmd
from tests.tower.conftest import mcwallt_fake_cmd, mcwallt_settable_clock

KEY = ("git_ls_remote", "mcwallt-repo", "mcwallt-branch")
ARGV = ["git", "ls-remote", "origin", "mcwallt-branch"]


def test_mcwallt_run_cmd_ok(tmp_path):
    rc, out, err = _run_cmd([sys.executable, "-c", "print('mcwallt')"], cwd=str(tmp_path))
    assert (rc, out, err) == (0, "mcwallt\n", "")


def test_mcwallt_run_cmd_error_and_exceptions(tmp_path):
    # Non-zero exit code is captured, not raised.
    rc, out, _err = _run_cmd([sys.executable, "-c", "raise SystemExit(2)"],
                             cwd=str(tmp_path))
    assert rc == 2
    assert out == ""

    # Missing binary: no raise, failure result.
    assert _run_cmd(["mcwallt-no-such-bin"], cwd=str(tmp_path)) == (None, "", "")

    # Timeout: no raise, failure result.
    assert _run_cmd([sys.executable, "-c", "import time; time.sleep(5)"],
                    cwd=str(tmp_path), timeout_s=0.2) == (None, "", "")


def _fetch(cache, now_s, ttl=120):
    return cache.fetch(KEY, ARGV, "mcwallt-cwd", now_s, ttl, 30.0, 600.0)


def test_mcwallt_cache_ttl_reuse(monkeypatch):
    # AC-CACHE-1: same key within TTL -> exactly one _run_cmd; the second call
    # is served the SAME entry (identical observed_at_s).
    fake, calls = mcwallt_fake_cmd(lambda argv, cwd: (0, "mcwallt-out"))
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    now_s, _set = mcwallt_settable_clock(1_000.0)
    cache = NetCache()
    r1 = _fetch(cache, now_s)
    r2 = _fetch(cache, now_s)
    assert calls["n"] == 1
    assert r1.ok is True and r1.stdout == "mcwallt-out"
    assert r2.ok is True and r2.stdout == "mcwallt-out"
    assert r2.observed_at_s == r1.observed_at_s


def test_mcwallt_cache_ttl_expiry(monkeypatch):
    # AC-CACHE-2: both sides of the boundary — fresh at EXACTLY ttl_s,
    # expired strictly past it.
    fake, calls = mcwallt_fake_cmd(lambda argv, cwd: (0, "mcwallt-out"))
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    now_s, set_now = mcwallt_settable_clock(1_000.0)
    cache = NetCache()
    _fetch(cache, now_s)                 # observed at t=1000
    assert calls["n"] == 1
    set_now(1_120.0)                     # age == ttl_s (120) exactly
    assert _fetch(cache, now_s).ok is True
    assert calls["n"] == 1               # STILL fresh at exactly ttl
    set_now(1_121.0)                     # age 121 > 120: strictly expired
    r = _fetch(cache, now_s)
    assert calls["n"] == 2               # refetch on the far side
    assert r.ok is True and r.stdout == "mcwallt-out"


def test_mcwallt_cache_single_flight(monkeypatch):
    # AC-CACHE-3: cold key, 4 concurrent fetches, slow handler -> ONE spawn,
    # all four callers share that one result.
    def slow(argv, cwd):
        time.sleep(0.05)
        return (0, "mcwallt-one")

    fake, calls = mcwallt_fake_cmd(slow)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    now_s, _set = mcwallt_settable_clock(1_000.0)
    cache = NetCache()
    results, errors = [], []

    def worker():
        try:
            results.append(_fetch(cache, now_s))
        except Exception as exc:  # any leak from the lock path is a failure
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
    assert errors == []
    assert not any(t.is_alive() for t in threads)  # no leaked threads
    assert calls["n"] == 1                         # one spawn, shared by all four
    assert len(results) == 4
    assert all(r.ok is True and r.stdout == "mcwallt-one" for r in results)
    assert len({r.observed_at_s for r in results}) == 1  # identical results


def test_mcwallt_cache_backoff(monkeypatch):
    # AC-CACHE-4: 30 -> 60 -> ... capped 600; in-backoff keys spawn nothing;
    # one success resets the counter. Boundary pinned on both sides each time.
    fail = {"on": True}
    fake, calls = mcwallt_fake_cmd(
        lambda argv, cwd: (1, "") if fail["on"] else (0, "mcwallt-ok"))
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    now_s, set_now = mcwallt_settable_clock(0.0)
    cache = NetCache()

    def F(t):
        set_now(t)
        return _fetch(cache, now_s, ttl=0)  # always-expired: backoff is the only gate

    assert F(0).ok is False and calls["n"] == 1    # t=0: first attempt, fails
    assert F(29).ok is False and calls["n"] == 1   # inside the 30 s backoff: NO spawn
    assert F(30).ok is False and calls["n"] == 2   # allowed at exactly base (30)
    assert F(89).ok is False and calls["n"] == 2   # inside the doubled 60 s window
    assert F(90).ok is False and calls["n"] == 3   # allowed at exactly 60
    # Success at the next allowed attempt (wait 120) resets the counter...
    fail["on"] = False
    assert F(210).ok is True and calls["n"] == 4
    # ...so the NEXT failure (t=211) re-arms the base 30 s window.
    fail["on"] = True
    assert F(211).ok is False and calls["n"] == 5
    assert F(240).ok is False and calls["n"] == 5   # 29 s elapsed: short-circuit
    assert F(241).ok is False and calls["n"] == 6   # 30 s elapsed: allowed again

    # Cap: a fresh key pre-failed 6 times -> next wait min(30*2**5, 600) = 600.
    cap_key = ("mr_by_ref", "mcwallt-repo", "!1")
    cap_argv = ["glab", "mr", "view", "1", "--output", "json"]
    t, base = 0.0, calls["n"]
    for wait in (30.0, 60.0, 120.0, 240.0, 480.0, 960.0):
        t += wait
        set_now(t)
        assert cache.fetch(cap_key, cap_argv, "mcwallt-cwd",
                           now_s, 0, 30.0, 600.0).ok is False
    assert calls["n"] - base == 6
    t += 599.0
    set_now(t)
    assert cache.fetch(cap_key, cap_argv, "mcwallt-cwd",
                       now_s, 0, 30.0, 600.0).ok is False
    assert calls["n"] - base == 6            # 599 s in: still held (capped 600)
    t += 1.0
    set_now(t)
    assert cache.fetch(cap_key, cap_argv, "mcwallt-cwd",
                       now_s, 0, 30.0, 600.0).ok is False
    assert calls["n"] - base == 7            # exactly 600 s: allowed


def test_mcwallt_cache_no_stale_as_fresh(monkeypatch):
    # AC-CACHE-5: a once-good entry is NEVER served after the source turns bad;
    # failures are recorded for backoff but never served as values.
    out = {"reply": (0, "mcwallt-good")}
    fake, calls = mcwallt_fake_cmd(lambda argv, cwd: out["reply"])
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    now_s, set_now = mcwallt_settable_clock(1_000.0)
    cache = NetCache()
    assert _fetch(cache, now_s) == _fetch(cache, now_s)  # sanity: cached, equal
    set_now(1_121.0)                       # past TTL (121 > 120)
    out["reply"] = (1, "")                 # the source now fails
    r2 = _fetch(cache, now_s)
    assert r2.ok is False and r2.stdout == ""   # stale "good" NOT served
    assert calls["n"] == 2
    set_now(1_150.0)                       # inside the 30 s failure backoff
    r3 = _fetch(cache, now_s)
    assert r3.ok is False and r3.stdout == ""
    assert calls["n"] == 2                 # no spawn: failure recorded, never served


def test_mcwallt_cache_cross_key_independent(monkeypatch):
    # Review N2: single-flight is PER KEY — a blocked fetch on key A must not
    # delay key B. Fully deterministic: Events gate A's spawn, every join and
    # wait carries a timeout, and B runs in a thread so even a wrongly-global
    # lock fails an assertion instead of hanging the suite.
    a_spawned = threading.Event()
    release_a = threading.Event()

    def handler(argv, cwd):
        if argv[3] == "mcwallt-branch-a":
            a_spawned.set()
            release_a.wait(timeout=10.0)      # A's spawn blocks here, holding A's lock
            return (0, "mcwallt-a")
        return (0, "mcwallt-b")

    fake, _calls = mcwallt_fake_cmd(handler)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    now_s, _set = mcwallt_settable_clock(1_000.0)
    cache = NetCache()
    results = {}

    def fetch(key_branch, out_key):
        results[out_key] = cache.fetch(
            ("git_ls_remote", "mcwallt-repo", key_branch),
            ["git", "ls-remote", "origin", key_branch],
            "mcwallt-cwd", now_s, 120, 30.0, 600.0)

    ta = threading.Thread(target=fetch, args=("mcwallt-branch-a", "a"))
    ta.start()
    assert a_spawned.wait(timeout=10.0)       # A is in flight on ITS key lock
    tb = threading.Thread(target=fetch, args=("mcwallt-branch-b", "b"))
    tb.start()
    tb.join(timeout=5.0)
    assert not tb.is_alive()                  # B completed while A was blocked
    assert results["b"].ok is True and results["b"].stdout == "mcwallt-b"
    assert "a" not in results                 # A genuinely still in flight
    release_a.set()
    ta.join(timeout=10.0)
    assert not ta.is_alive()                  # no leaked threads
    assert results["a"].ok is True and results["a"].stdout == "mcwallt-a"


def test_mcwallt_cache_run_cmd_raising_degrades_key(monkeypatch):
    # Plan F1 seam: a RAISING _run_cmd becomes a per-key rc-None failure —
    # never an exception past fetch, never a zeroed document downstream.
    def raising(argv, cwd, timeout_s=10.0):
        raise RuntimeError("mcwallt net down")

    monkeypatch.setattr(netcache_module, "_run_cmd", raising)
    now_s, _set = mcwallt_settable_clock(1_000.0)
    cache = NetCache()
    r = _fetch(cache, now_s)
    assert r.ok is False and r.stdout == ""   # raising _run_cmd -> failure result
    r2 = _fetch(cache, now_s)                 # in-backoff retry short-circuits...
    assert r2.ok is False                     # ...BEFORE any spawn (no raise path)
    other = cache.fetch(("mr_by_ref", "mcwallt-repo", "!5"),
                        ["glab", "mr", "view", "5", "--output", "json"],
                        "mcwallt-cwd", now_s, 120, 30.0, 600.0)
    assert other.ok is False                  # the cache object itself survives
