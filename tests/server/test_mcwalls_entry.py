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
        assert got["status"] == 200, got["status"]  # F-1: default boot serves live
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


MCWALLF_HEADER = "| id | wave | lane | repo/branch | slug | base | session/MR artifacts | status |"
MCWALLF_SEP = "|---|---|---|---|---|---|---|---|"


def _mcwallf_make_tower_db(path, sessions=(), inputs=()):
    """Tower LIVE schema (mcwallt_make_db pattern from tests/tower/conftest.py,
    incl. time_updated/time_archived) — NOT the harness's simplified builder."""
    import sqlite3

    con = sqlite3.connect(path)
    con.executescript("""
      CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT, title TEXT,
        title_source TEXT, directory TEXT, task_type TEXT, time_updated INTEGER,
        time_created INTEGER, time_archived INTEGER);
      CREATE TABLE session_input (session_id TEXT, kind TEXT, payload TEXT,
        delivery TEXT, status TEXT, time_created INTEGER);""")
    con.executemany("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?)",
                    [(s["id"], None, s.get("title"), None, s.get("directory", ""),
                      None, s["time_updated"], s["time_created"],
                      s.get("time_archived")) for s in sessions])
    con.executemany("INSERT INTO session_input VALUES (?,?,?,?,?,?)",
                    [(i["session_id"], i.get("kind", "sendText"), i["payload"],
                      None, None, i["time_created"]) for i in inputs])
    con.commit()
    con.close()


def _mcwallf_probe_state(port, token, got):
    def probe():
        try:
            conn = http_client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.request("GET", f"/{token}/state", headers={"Host": "127.0.0.1"})
                resp = conn.getresponse()
                got["status"] = resp.status
                got["body"] = resp.read()
                return True
            finally:
                conn.close()
        except OSError:
            return False
    return probe


def test_mcwallf_module_entry_serves_live_board():
    import time as _time

    from tests.server import mcwalls_harness as H

    port = _free_port()
    wall_home = H.make_tmp_root("mcwallf-entry-")
    (wall_home / "repo").mkdir()  # configured repo path exists -> no entry-4
    now = _time.time()
    sid = "sess_aaaa1111bbbb2221"  # hex-only: SESS_RE-safe, prefix-joins the db id
    db = wall_home / "mcwallf_sessions.db"
    _mcwallf_make_tower_db(
        db,
        sessions=[{"id": sid, "title": "mcwallf lane", "directory": "/mcwallf/l",
                   "time_updated": int((now - 400) * 1000),
                   "time_created": int((now - 400) * 1000)}],  # aged 400s >= grace 300s
    )
    (wall_home / "mcwallf_note.md").write_text("\n".join([
        "objective: mcwallf boot objective",
        MCWALLF_HEADER, MCWALLF_SEP,
        f"| mcwallf-l1 | W1 | L1 | n/a | n/a | n/a | {sid} | done |",
    ]) + "\n", encoding="utf-8")
    (wall_home / "wall.json").write_text(json.dumps({
        "token": "entrytok", "port": port,
        "programs": [{"program": "mcwallf-prog", "tag": "mcwallf",
                      "note_glob": str(wall_home / "mcwallf_note*.md")}],
        "repos": [{"name": "mcwallf-repo", "path": str(wall_home / "repo"),
                   "host": "gitlab"}],
    }), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(H.REPO_ROOT),
           "MC_WALL_HOME": str(wall_home), "MC_WALL_DB": str(db)}
    proc = subprocess.Popen([sys.executable, "-m", "mc_wall.server"],
                            cwd=str(H.REPO_ROOT), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        got = {}
        assert H.wait_for(_mcwallf_probe_state(port, "entrytok", got), timeout=10.0), \
            "server never answered /state"
        assert got["status"] == 200, got["status"]
        data = json.loads(got["body"].decode("utf-8"))
        assert data["schema_version"] == 1
        lanes = data["programs"][0]["lanes"]
        assert [l["row_id"] for l in lanes] == ["mcwallf-l1"]
        vq = [r for r in data["verify_queue"] if r["row_id"] == "mcwallf-l1"]
        assert vq, "verify_queue must carry the fixture done lane"
        assert vq[0]["verify_cmd"] == f"/mission-control-verify {sid}"
        time.sleep(0.3)  # let the signal handlers pass the serve-start window
    finally:
        proc.terminate()
        _stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode == 0
    assert b"Traceback" not in stderr


def test_mcwallf_default_boot_zero_env_empty_board():
    from tests.server import mcwalls_harness as H

    port = _free_port()
    home = H.make_tmp_root("mcwallf-zeroenv-")
    (home / ".zcode" / "cli" / "db").mkdir(parents=True)
    _mcwallf_make_tower_db(home / ".zcode" / "cli" / "db" / "db.sqlite")
    (home / "wall.json").write_text(json.dumps({"token": "entrytok", "port": port}),
                                    encoding="utf-8")
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(H.REPO_ROOT),
           "HOME": str(home), "MC_WALL_HOME": str(home)}
    proc = subprocess.Popen([sys.executable, "-m", "mc_wall.server"],
                            cwd=str(H.REPO_ROOT), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        got = {}
        assert H.wait_for(_mcwallf_probe_state(port, "entrytok", got), timeout=10.0), \
            "server never answered /state"
        assert got["status"] == 200, got["status"]
        data = json.loads(got["body"].decode("utf-8"))
        assert data["programs"] == []  # honestly-empty board (pin, resolved Q4)
        assert data["server"]["degraded"] == []
        time.sleep(0.3)
    finally:
        proc.terminate()
        proc.communicate(timeout=10)
    assert proc.returncode == 0


def test_mcwallf_malformed_programs_entry_one_line(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("MC_WALL_HOME", str(tmp_path))
    monkeypatch.setenv("MC_WALL_DB", str(tmp_path / "no-db.sqlite"))
    port = _free_port()  # never a hardcoded port in wall.json
    (tmp_path / "wall.json").write_text(json.dumps({
        "token": "t", "port": port,
        "programs": [{"program": "p", "tag": "g"}],  # note_glob missing
    }), encoding="utf-8")
    from mc_wall.server import __main__ as entry

    # DEFUSED for the RED: at HEAD load_config IGNORES "programs", so main()
    # would fall through to run_server — a REAL server on wall.json's port,
    # the real session db, and signal handlers in the pytest main thread. The
    # stub records the call and returns immediately; the RED fires on the
    # rc/one-line assertions (HEAD returns the stub's 0). At GREEN the stub is
    # unreachable: tower_config_from_wall raises before run_server is called.
    reached = []

    def _stub_run_server(cfg):
        reached.append(cfg)
        return 0

    monkeypatch.setattr(entry, "run_server", _stub_run_server)

    assert entry.main() == 1
    assert reached == []  # the malformed config must stop the boot BEFORE serving
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1  # ONE clear line
    assert "wall.json" in lines[0]
