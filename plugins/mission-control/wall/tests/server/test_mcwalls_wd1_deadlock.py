"""wd1 — wall-deadlock regression (W6-L6, 2026-10-07 incident).

The incident: every /state ran the FULL tower collect synchronously in its
handler thread; on cold caches the collect serializes dozens of subprocess
spawns (measured 17.5 s idle; each spawn capped at its own 10 s timeout) and
the browser polls every 5 s — service time exceeded arrival rate, handler
threads piled up without bound, and the wall read as "accepts TCP, never
answers". These tests pin the remediation: bounded requests (503 naming the
overran stage, never a hang), single-flight coalescing (no pile-up), the
MC_WALL_MONITOR kill switch, the FATAL boot self-check (exit 5), runner spawn
timeouts, git-probe failure backoff, and manifest-garbage tolerance.
"""

import json
import logging
import threading
import time


def _wd1_spawn_module():
    """The spawn-capable stdlib module, reached through the PROD runner's own
    import (the tests/server spawn ban forbids importing it here; this file
    spawns nothing — it only needs TimeoutExpired and monkeypatch targets)."""
    from mc_wall.server import runner as runner_mod

    return runner_mod.subprocess


def _drop_wall_log_handlers(log_dir):
    for h in [x for x in logging.getLogger("mc_wall.server").handlers
              if getattr(x, "baseFilename", None) == str(log_dir / "wall.log")]:
        logging.getLogger("mc_wall.server").removeHandler(h)
        h.close()


# --------------------------------------------------------------- /state bound

def test_wd1_state_deadline_returns_503_not_hang(monkeypatch):
    """THE wedge regression: a collect that never completes must yield a fast
    503 naming a stage — pre-fix this request hung until the client gave up
    (pre-fix RED runs fail here on the 5s socket timeout)."""
    from tests.server.mcwalls_harness import serve

    monkeypatch.setenv("MC_WALL_STATE_DEADLINE_S", "0.5")

    def slow_collect():
        time.sleep(30)
        return {"schema_version": 1}

    with serve(collect_state=slow_collect) as h:
        t0 = time.monotonic()
        r = h.http("GET", f"/{h.token}/state")
        elapsed = time.monotonic() - t0
        assert elapsed < 3.0, f"request must fail loud, not hang (took {elapsed:.1f}s)"
        assert r.status == 503
        body = r.json()
        assert body["degraded"] is True
        assert body["error"] == "collect_state_deadline"
        assert isinstance(body["detail"], str) and body["detail"]  # a stage is named


def test_wd1_state_deadline_names_real_mc_wall_stage(monkeypatch):
    """The 503 detail names where the wedged collect SAT — the deepest mc_wall
    frame of the in-flight worker (here pending.py, via a patched
    _write_atomic sleeping inside an mc_wall call chain)."""
    from mc_wall.server.pending import PendingRecord, PendingStore
    from tests.server.mcwalls_harness import make_tmp_root, serve

    monkeypatch.setenv("MC_WALL_STATE_DEADLINE_S", "0.5")
    store = PendingStore(make_tmp_root("wd1-store-"))
    store.create(PendingRecord(status="await-birth", row_id="r1"))
    real_write = PendingStore._write_atomic

    def slow_write(self, record):
        time.sleep(5)

    PendingStore._write_atomic = slow_write
    try:

        def collect():
            store.update(lambda r: r)  # _write_atomic under the store lock
            return {"schema_version": 1}

        with serve(collect_state=collect) as h:
            r = h.http("GET", f"/{h.token}/state")
    finally:
        PendingStore._write_atomic = real_write
    assert r.status == 503
    assert r.json()["error"] == "collect_state_deadline"
    detail = r.json()["detail"]
    assert detail.startswith("pending.py:"), f"stage not a real frame: {detail!r}"


def test_wd1_concurrent_requests_coalesce_no_pileup():
    """The pile-up amplifier is dead: N concurrent /state during one slow
    collect share AT MOST one in-flight run + one follow-up — pre-fix each
    request ran its OWN collect (N executions)."""
    from tests.server.mcwalls_harness import StubTower, serve

    class SleepTower(StubTower):
        def __call__(self):
            self.calls += 1
            time.sleep(1.0)
            return {"schema_version": 1}

    slow = SleepTower({"schema_version": 1})
    with serve(collect_state=slow) as h:  # default 10s deadline > 1s collect
        results = []
        lock = threading.Lock()

        def hit():
            r = h.http("GET", f"/{h.token}/state")
            with lock:
                results.append(r.status)

        threads = [threading.Thread(target=hit) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        assert len(results) == 6
        assert all(s == 200 for s in results)
        assert slow.calls <= 2, (
            f"concurrent requests must coalesce onto the in-flight run "
            f"(got {slow.calls} collect executions)")


def test_wd1_sequential_requests_still_collect_per_request():
    """Coalescing must NOT become caching: sequential requests each run the
    collector (the pinned no-caching contract rides unchanged)."""
    from tests.server.mcwalls_harness import StubTower, serve

    stub = StubTower({"schema_version": 1})
    with serve(collect_state=stub) as h:
        for _ in range(3):
            assert h.http("GET", f"/{h.token}/state").status == 200
        assert stub.calls == 3


# ------------------------------------------------------------- kill switch

def test_wd1_monitor_kill_switch_off(monkeypatch):
    """MC_WALL_MONITOR=0: the handshake thread is entirely absent, /state
    still serves fast (EXPECT-3)."""
    from tests.server.mcwalls_harness import StubTower, make_fixture_db, make_tmp_root, serve

    monkeypatch.setenv("MC_WALL_MONITOR", "0")
    db, _anchor = make_fixture_db()
    state = make_tmp_root("wd1-state-")
    with serve(collect_state=StubTower({"schema_version": 1}),
              db_path=db, state_dir=state) as h:
        assert h.server.monitor is None, "monitor thread must be absent"
        t0 = time.monotonic()
        r = h.http("GET", f"/{h.token}/state")
        elapsed = time.monotonic() - t0
        assert r.status == 200
        assert elapsed < 1.0, f"/state must serve fast with monitor off ({elapsed:.2f}s)"
        log = (h.log_dir / "wall.log").read_text(encoding="utf-8")
        assert "MC_WALL_MONITOR set — handshake monitor disabled" in log


def test_wd1_monitor_kill_switch_default_on(monkeypatch):
    """Without the env knob the monitor starts as before (the switch is
    opt-OUT, not a behavior change)."""
    from tests.server.mcwalls_harness import StubTower, make_fixture_db, make_tmp_root, serve

    monkeypatch.delenv("MC_WALL_MONITOR", raising=False)
    db, _anchor = make_fixture_db()
    with serve(collect_state=StubTower({"schema_version": 1}),
              db_path=db, state_dir=make_tmp_root("wd1-state-")) as h:
        assert h.server.monitor is not None


# ------------------------------------------------------- boot self-check

def test_wd1_boot_self_check_overrun_fatals_exit_5(monkeypatch, tmp_path):
    """A boot whose collect never completes must NOT reach the serve loop: it
    logs FATAL, dumps every thread's stack, and exits 5 (launchd restarts)."""
    import mc_wall.server.app as app
    import mc_wall.tower as tower_mod
    from tests.server.mcwalls_harness import make_web_dir

    def wedged_collect(_config, serve=False):  # wc1: collect_state grew a serve kwarg
        time.sleep(30)

    monkeypatch.setattr(tower_mod, "collect_state", wedged_collect)
    monkeypatch.setenv("MC_WALL_BOOT_DEADLINE_S", "0.5")

    class FakeTowerConfig:
        def current(self):
            return object()

    def _no_serve(*_a, **_k):
        raise AssertionError("run_server reached the serve loop despite a"
                             " wedged boot self-check")

    monkeypatch.setattr(app.WallServer, "wait_shutdown", _no_serve)

    logs = tmp_path / "logs"
    cfg = app.ServerConfig(
        # token "zz": RedactToken replaces EVERY substring occurrence of the
        # token — "t" would mangle "FATAL boot self-check" itself.
        token="zz",
        port=0,
        wall_home=tmp_path,
        web_dir=make_web_dir(),
        state_dir=tmp_path / "state",
        log_dir=logs,
        tower_config=FakeTowerConfig(),
    )
    try:
        assert app.run_server(cfg) == 5
        log = (logs / "wall.log").read_text(encoding="utf-8")
        assert "FATAL boot self-check" in log
        assert "stage" in log
    finally:
        _drop_wall_log_handlers(logs)


# ------------------------------------------------------------- runner bound

def test_wd1_runner_spawns_carry_explicit_timeouts():
    """pbcopy/open/osascript are GUI services that can wedge — every spawn
    must carry an explicit timeout (pre-fix: none did)."""
    from mc_wall.server.runner import RUNNER_TIMEOUT_S, SubprocessRunner

    seen = []

    def fake_exec(argv, **kwargs):
        seen.append((argv[0], kwargs.get("timeout")))

    r = SubprocessRunner(exec=fake_exec)
    r.copy("x")
    r.open_url("zcode://workspace/open")
    r.open_app("ZCode")
    r.notify("t", "b")
    assert {argv for argv, _t in seen} == {"pbcopy", "open", "osascript"}
    assert all(t == RUNNER_TIMEOUT_S for _a, t in seen), seen


def test_wd1_runner_timeout_raises_bounded_not_hang():
    """A wedged GUI call fails at the deadline instead of blocking the
    monitor tick forever (the fake emulates the stdlib's enforcement:
    sleep up to the passed timeout, then raise TimeoutExpired)."""
    from mc_wall.server.runner import SubprocessRunner

    timeout_expired = _wd1_spawn_module().TimeoutExpired
    duration = 10.0

    def wedged_exec(argv, timeout=None, **kwargs):
        time.sleep(duration if timeout is None else min(duration, timeout))
        if timeout is not None and duration > timeout:
            raise timeout_expired(argv, timeout)

    r = SubprocessRunner(exec=wedged_exec, timeout_s=0.3)
    t0 = time.monotonic()
    try:
        r.copy("x")
        raise AssertionError("copy must raise, not return")
    except timeout_expired:
        pass
    assert time.monotonic() - t0 < 3.0


# --------------------------------------------------- git-probe backoff

def test_wd1_git_probe_failure_backoff(monkeypatch, tmp_path):
    """A FAILING `git remote -v` probe must not respawn every cycle
    (success-only caching was the livelock vector): failures are held for a
    bounded backoff window, successes cache permanently."""
    import mc_wall.tower.discovery as discovery
    from mc_wall.server.tower_boot import PerPollTowerConfig

    class FakeClock:
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            return self.now

    clock = FakeClock()
    probes = {"n": 0}

    def failing_git(path):
        probes["n"] += 1
        return (1, "")

    monkeypatch.setattr(discovery, "_run_git", failing_git)
    ppc = PerPollTowerConfig(tmp_path, data={"token": "t"}, clock=clock)

    assert ppc._cached_run_git("/x") == (1, "")
    assert ppc._cached_run_git("/x") == (1, "")  # backoff window: no respawn
    assert ppc._cached_run_git("/x") == (1, "")
    assert probes["n"] == 1, "failing probes must not retry every cycle"

    clock.now += PerPollTowerConfig.GIT_PROBE_FAIL_BACKOFF_S + 1
    ppc._cached_run_git("/x")
    assert probes["n"] == 2, "after the backoff window the probe retries once"

    def ok_git(path):
        probes["n"] += 1
        return (0, "origin git@github.com:x/y.git (fetch)")

    monkeypatch.setattr(discovery, "_run_git", ok_git)
    clock.now += PerPollTowerConfig.GIT_PROBE_FAIL_BACKOFF_S + 1
    assert ppc._cached_run_git("/x") == (0, "origin git@github.com:x/y.git (fetch)")
    assert ppc._cached_run_git("/x") == (0, "origin git@github.com:x/y.git (fetch)")
    assert probes["n"] == 3, "successes cache permanently"


# ------------------------------------------------- manifest garbage tolerance

def test_wd1_manifest_garbage_fields_never_reach_argv(monkeypatch, tmp_path):
    """The incident's W5-L5 manifest shape — a NON-SHA behavioral base_sha and
    garbage extra fields — parses (or skips) WITHOUT ever spawning a process
    or looping: manifests are json-read for program/row_id/branch only."""
    import mc_wall.tower.forge as forge
    import mc_wall.tower.merges as merges

    prog_dir = tmp_path / "wall-overhaul" / "W5-L5"
    prog_dir.mkdir(parents=True)
    (prog_dir / "manifest.json").write_text(json.dumps({
        "row_id": "W5-L5",
        "program": "wall-overhaul",
        "branch": "loop/wall-honesty",
        "base_sha": "behavioral:origin/main-after-PR18+W4-merges",
        "weird": {"nested": [1, None, True]},
    }), encoding="utf-8")

    def no_spawns(*_a, **_k):
        raise AssertionError("manifest reading must never spawn a subprocess")

    spawn_mod = _wd1_spawn_module()
    monkeypatch.setattr(spawn_mod, "run", no_spawns)
    monkeypatch.setattr(spawn_mod, "Popen", no_spawns)

    # A5 cross-check: present row -> no defect; absent row -> one TOP defect.
    assert forge.missing_row_defects(str(tmp_path), "wall-overhaul",
                                     {"W5-L5"}, "note.md") == []
    defects = forge.missing_row_defects(str(tmp_path), "wall-overhaul",
                                        set(), "note.md")
    assert len(defects) == 1 and defects[0]["row_id"] == "W5-L5"

    # Registry join fallback reads branch/program/row_id; garbage elsewhere is
    # skipped silently.
    assert merges._manifest_branch_map(str(tmp_path)) == {
        "loop/wall-honesty": ("wall-overhaul", "W5-L5")}
