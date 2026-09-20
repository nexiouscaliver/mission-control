"""F-2 — server lifetime: `python -m mc_wall.server` must serve indefinitely
under its normal runner (launchd KeepAlive=CrashedOnly never restarts a clean
exit-0, so a timed self-exit is a silent wall death).

The subprocess is spawned exactly as tests/server/test_mcwalls_entry.py does
(temp MC_WALL_HOME, wall.json, MC_WALL_DB pinned to a nonexistent tmp path);
that file is read-only for this task, so its importable helpers are reused —
including its already-imported subprocess module object, because the structural
spawn ban (test_mcwalls_structural.py) allows ONLY test_mcwalls_entry.py to
import it. The Popen argv below keeps the pinned [sys.executable, "-m", ...]
shape, never a shell.
"""

import json
import os
import sys
import time

from tests.server.test_mcwalls_entry import subprocess


def test_mcwallf_server_stays_alive_past_10s():
    """F-2 RED (deterministic): the legacy wait_shutdown join(timeout=10) made
    run_server exit 0 ~10 s after boot, killing the daemon serve loop; launchd
    KeepAlive=CrashedOnly does not restart clean exit-0. Legacy death lands at
    ~ boot + interpreter import + self-test + 10 s; the anchor is t=15 s from
    boot (widened from the spec's 12 s — on a loaded runner startup alone can
    eat 1.5-2 s, which would let a 12 s poll already see exit at HEAD,
    silently invalidating the RED). The fixed process must still be alive."""
    from tests.server import mcwalls_harness as H
    from tests.server.test_mcwalls_entry import _free_port, _mcwallf_probe_state

    port = _free_port()
    wall_home = H.make_tmp_root("mcwallf-life-")
    (wall_home / "wall.json").write_text(
        json.dumps({"token": "entrytok", "port": port}), encoding="utf-8"
    )
    env = {**os.environ, "PYTHONPATH": str(H.REPO_ROOT),
           "MC_WALL_HOME": str(wall_home),
           "MC_WALL_DB": str(wall_home / "no-db.sqlite")}
    start = time.monotonic()
    proc = subprocess.Popen([sys.executable, "-m", "mc_wall.server"],
                            cwd=str(H.REPO_ROOT), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        got = {}
        assert H.wait_for(_mcwallf_probe_state(port, "entrytok", got), timeout=10.0), \
            "server never answered /state"
        remain = 15.0 - (time.monotonic() - start)  # anchor at BOOT: the legacy
        if remain > 0:                              # 10 s join started at boot
            time.sleep(remain)
        assert proc.poll() is None, (
            f"server exited rc={proc.returncode} "
            f"~{time.monotonic() - start:.1f}s after boot — wall must serve indefinitely"
        )
        time.sleep(0.3)  # handlers installed past the serve-start window
    finally:
        proc.terminate()
        _stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode == 0
    assert b"Traceback" not in stderr
