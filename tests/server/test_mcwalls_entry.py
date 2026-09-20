"""T14 — `python -m mc_wall.server` module entry.

The subprocess test spawns ONLY `sys.executable -m mc_wall.server` with
MC_WALL_HOME and MC_WALL_DB pinned to nonexistent tmp paths: the child never
touches the real wall home, never queries the live session db, and never
opens any app. Everything else is in-process with a tmp MC_WALL_HOME.
"""

import http.client as http_client
import json
import os
import socket
import subprocess
import sys
import time


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_module_entry_serves_state():
    from tests.server import mcwalls_harness as H

    port = _free_port()
    wall_home = H.make_tmp_root("mcwalls-entry-")
    (wall_home / "wall.json").write_text(
        json.dumps({"token": "entrytok", "port": port}), encoding="utf-8"
    )
    (wall_home / "state").mkdir()
    (wall_home / "logs").mkdir()

    argv = [sys.executable, "-m", "mc_wall.server"]
    assert argv[0] == sys.executable  # never a shell, never another binary
    env = {
        **os.environ,
        "PYTHONPATH": str(H.REPO_ROOT),
        "MC_WALL_HOME": str(wall_home),
        "MC_WALL_DB": str(wall_home / "no-db.sqlite"),
    }
    proc = subprocess.Popen(
        argv,
        cwd=str(H.REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        got = {}

        def probe():
            try:
                conn = http_client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    conn.request(
                        "GET", "/entrytok/state", headers={"Host": "127.0.0.1"}
                    )
                    resp = conn.getresponse()
                    got["status"] = resp.status
                    got["body"] = resp.read()
                    return True
                finally:
                    conn.close()
            except OSError:
                return False

        assert H.wait_for(probe, timeout=10.0), "server never answered /state"
        # 503 degraded is EXPECTED while mc_wall.tower lacks collect_state
        # (the never-crash degradation path); 200 once lane L1 lands it.
        assert got["status"] in (200, 503), got["status"]
        data = json.loads(got["body"].decode("utf-8"))
        assert isinstance(data, dict)
        # Let the entry finish wiring its SIGTERM/SIGINT handlers past the
        # serve-start window so terminate() hits the clean-shutdown path.
        time.sleep(0.3)
    finally:
        proc.terminate()
        _stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode == 0
    assert b"Traceback" not in stderr


def test_missing_wall_json_clear_error(capsys, monkeypatch):
    from tests.server import mcwalls_harness as H

    empty = H.make_tmp_root("mcwalls-empty-")
    monkeypatch.setenv("MC_WALL_HOME", str(empty))
    from mc_wall.server import __main__ as entry

    assert entry.main() == 1
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert len(lines) == 1  # ONE clear line
    assert "wall.json" in lines[0]
