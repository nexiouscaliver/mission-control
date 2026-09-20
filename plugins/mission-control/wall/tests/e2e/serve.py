"""Boot the REAL mc-wall stack in-process for browser E2E (goal mcwall-e2e).

Fresh mode writes <home>/wall.json with a random token on port 8799; --reuse
re-reads the SAME wall.json so a restart keeps the browser's tokened URL valid
(a new token would 404 the page and fake a product FAIL). The collect_state
closure injects a real TowerConfig because the prod default is broken (F-1:
_default_collect_state calls tower collect_state() with no config -> /state 503).

Run from the run worktree root (PYTHONPATH is added defensively below):
    python -u tests/e2e/serve.py [--home <dir>] [--reuse]
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import pathlib
import secrets
import signal
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import e2e_common  # noqa: E402  (sys.path insert above must precede this import)


def _die(code: int, message: str):
    print("e2e-serve: %s" % message, file=sys.stderr)
    raise SystemExit(code)


def _cleanup_logging(log_dir) -> None:
    # Mirror tests/server/mcwalls_harness.py:179-185: remove+close THIS boot's
    # RotatingFileHandler from the process-global "mc_wall.server" logger.
    import logging

    lg = logging.getLogger("mc_wall.server")
    for h in [
        x
        for x in lg.handlers
        if getattr(x, "baseFilename", None) == str(pathlib.Path(log_dir) / "wall.log")
    ]:
        lg.removeHandler(h)
        h.close()


def _make_home(args, port: int = None) -> pathlib.Path:
    if args.reuse and args.home is None:
        _die(2, "--reuse requires --home (the restart reuses one temp home)")
    if args.home is not None:
        home = pathlib.Path(args.home).resolve()
        wall = home / e2e_common.WALL_JSON_FILENAME
        if args.reuse:
            if not wall.is_file():
                _die(2, "no %s under %s — nothing to reuse" % (wall.name, home))
            return home  # same token/port/state/logs; NEVER re-seed pending.json
        if wall.exists():
            _die(
                2,
                "%s already exists — pass --reuse to restart the same token" % wall,
            )
        try:
            home.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _die(1, "cannot create home %s (%s)" % (home, type(exc).__name__))
    else:
        home = pathlib.Path(tempfile.mkdtemp(prefix="mcwall-e2e-"))
    token = secrets.token_urlsafe(24)
    (home / e2e_common.WALL_JSON_FILENAME).write_text(
        json.dumps({"token": token, "port": port or e2e_common.E2E_PORT}),
        encoding="utf-8",
    )
    return home


def _probe_state(port: int, token: str, require_schema: bool = True):
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        try:
            conn.request("GET", "/%s/state" % token, headers={"Host": "127.0.0.1"})
            resp = conn.getresponse()
            body = resp.read()
            ok = resp.status == 200 and (not require_schema or b"schema_version" in body)
            return ok, str(resp.status)
        finally:
            conn.close()
    except OSError:
        return False, "conn-refused"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="mcwall-e2e real-stack boot")
    parser.add_argument("--home", help="temp MC_WALL_HOME (fresh-init if no wall.json)")
    parser.add_argument(
        "--reuse", action="store_true", help="restart an existing home: same token/port/state"
    )
    args = parser.parse_args(argv)

    e2e_common.assert_worktree_imports()  # harness bug guard, exit 5

    from mc_wall.server import create_server  # app.py:808
    from mc_wall.server import __main__ as entry  # load_config: the single wall.json reader
    from mc_wall.server.runner import SubprocessRunner
    from mc_wall.tower import collect_state as tower_collect_state  # collect.py:21

    home = _make_home(args)
    cfg = entry.load_config(home)
    config = e2e_common.build_tower_config()

    def _collect() -> dict:  # works around F-1 (app.py:63-66 vs collect.py:21)
        return tower_collect_state(config)

    try:
        server = create_server(
            cfg.token,
            port=cfg.port,
            web_dir=cfg.web_dir,
            log_dir=cfg.log_dir,
            state_dir=cfg.state_dir,
            collect_state=_collect,
            runner=SubprocessRunner(),  # REAL pbcopy/open path (spec §1)
            db_path=e2e_common.REAL_DB_PATH,  # explicit: immune to ambient MC_WALL_DB
            monitor_interval_s=2.0,  # real HandshakeMonitor cadence
        )
    except OSError:
        _die(
            3,
            "cannot listen on 127.0.0.1:%d — busy? hint: lsof -ti tcp:%d"
            % (e2e_common.E2E_PORT, e2e_common.E2E_PORT),
        )
    if not server.ready:
        server.shutdown()
        server.wait_shutdown()
        server.server_close()
        _cleanup_logging(cfg.log_dir)
        _die(4, "boot self-test failed")

    # Readiness = GET /state returns 200 carrying schema_version (proves the
    # injected closure defeats F-1). The FIRST collect spawns git/glab per
    # repo through the cold NetCache — generous timeout, 1 s cadence.
    deadline = time.monotonic() + 120.0
    last = "never"
    while time.monotonic() < deadline:
        ok, last = _probe_state(cfg.port, cfg.token)
        if ok:
            break
        time.sleep(1.0)
    else:
        server.shutdown()
        server.wait_shutdown()
        server.server_close()
        _cleanup_logging(cfg.log_dir)
        _die(6, "/state not ready within 120s (last=%s)" % last)

    env = {
        "home": str(home),
        "port": cfg.port,
        "token_file": str(home / e2e_common.WALL_JSON_FILENAME),
        "url_base": e2e_common.URL_BASE,
        "page_url": "%s/%s/" % (e2e_common.URL_BASE, cfg.token),
        "pid": os.getpid(),
    }
    env_path = home / e2e_common.ENV_FILENAME
    env_path.write_text(json.dumps(env, indent=2), encoding="utf-8")
    print("E2E-SERVE READY home=%s" % home)
    print("E2E-SERVE URL %s" % env["page_url"])
    print("E2E-SERVE ENV %s" % env_path)
    print("E2E-SERVE PID %d" % env["pid"])
    sys.stdout.flush()

    # Mirror run_server (app.py:911-918): signal -> shutdown thread -> wait.
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(
            sig,
            lambda *_: threading.Thread(target=server.shutdown, daemon=True).start(),
        )
    # Product wait_shutdown's 10s join cap returns early — F-2; the harness
    # blocks until shutdown() completes.
    server._serve_thread.join()
    server.server_close()
    _cleanup_logging(cfg.log_dir)
    print("E2E-SERVE STOPPED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
