"""Serve one embedded QA mock doc as a LIVE /state for browser E2E (mcwall-e2e).

The in-app browser cannot navigate file: URLs, so the page's QA cases
(web/index.html script ids minus the ``mock-`` prefix) are served over http
from the SAME embedded fixtures the page itself reads — extracted at runtime
via e2e_common.extract_mock_case, never duplicated here. The server injects
wall.pending from its own PendingStore, overwriting the mock doc's "wall"
key — expected; seed <home>/state/pending.json BEFORE boot to control the
armed bar. No db, no monitor: a pure page-fixture server.

Run from the run worktree root:
    python -u tests/e2e/mock_serve.py --case <name> --home <dir> [--port <n>]
"""

from __future__ import annotations

import argparse
import pathlib
import json
import os
import signal
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import e2e_common  # noqa: E402  (sys.path insert above must precede this import)
from serve import (  # noqa: E402  (shared boot plumbing, kept single-sourced)
    _cleanup_logging,
    _make_home,
    _probe_state,
)

# The one case whose doc deliberately omits schema_version: readiness for it
# is a bare 200, not 200+schema_version.
CASE_WITHOUT_SCHEMA = "no-schema-version"


def _die(code: int, message: str):
    print("e2e-mock: %s" % message, file=sys.stderr)
    raise SystemExit(code)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="mcwall-e2e QA mock-doc server")
    parser.add_argument("--case", required=True,
                        help="mock case (web/index.html script id minus 'mock-')")
    parser.add_argument("--home", required=True,
                        help="temp MC_WALL_HOME (fresh-init if no wall.json)")
    parser.add_argument("--port", type=int, default=None,
                        help="port pinned into a FRESH home's wall.json "
                             "(a reused home keeps its existing port)")
    args = parser.parse_args(argv)

    e2e_common.assert_worktree_imports()  # harness bug guard, exit 5

    from mc_wall.server import create_server
    from mc_wall.server import __main__ as entry  # load_config: the single wall.json reader
    from mc_wall.server.runner import SubprocessRunner

    # Validate the case BEFORE creating the home: a bad invocation must not
    # mint a token / write wall.json as a side effect.
    index_html = (e2e_common.WORKTREE_ROOT / "web" / "index.html").read_text(
        encoding="utf-8"
    )
    try:
        mock_doc = e2e_common.extract_mock_case(index_html, args.case)
    except ValueError as exc:
        _die(2, str(exc))  # unknown case (lists valid ones) or unparseable fixture

    home_path = pathlib.Path(args.home).resolve()
    reusing = (home_path / e2e_common.WALL_JSON_FILENAME).is_file()
    if reusing and args.port is not None:
        _die(2, "--port only applies to a fresh home; %s already pins a port"
             % (home_path / e2e_common.WALL_JSON_FILENAME))
    home = _make_home(argparse.Namespace(home=str(home_path), reuse=reusing),
                      port=args.port)
    cfg = entry.load_config(home)

    def _collect() -> dict:  # the doc is static; /state serves it verbatim
        return mock_doc

    try:
        server = create_server(
            cfg.token,
            port=cfg.port,
            web_dir=cfg.web_dir,
            log_dir=cfg.log_dir,
            state_dir=cfg.state_dir,
            collect_state=_collect,
            runner=SubprocessRunner(),
            db_path=None,  # no monitor, no live session db
            monitor_interval_s=2.0,
            start_monitor=False,
        )
    except OSError:
        _die(
            3,
            "cannot listen on 127.0.0.1:%d — busy? hint: lsof -ti tcp:%d"
            % (cfg.port, cfg.port),
        )
    if not server.ready:
        server.shutdown()
        server.wait_shutdown()
        server.server_close()
        _cleanup_logging(cfg.log_dir)
        _die(6, "boot self-test failed")

    # Same readiness contract as serve.py, minus the schema_version demand
    # for the one case that deliberately omits it.
    deadline = time.monotonic() + 120.0
    last = "never"
    while time.monotonic() < deadline:
        ok, last = _probe_state(cfg.port, cfg.token,
                                require_schema=args.case != CASE_WITHOUT_SCHEMA)
        if ok:
            break
        time.sleep(1.0)
    else:
        server.shutdown()
        server.wait_shutdown()
        server.server_close()
        _cleanup_logging(cfg.log_dir)
        _die(6, "/state not ready within 120s (last=%s)" % last)

    url_base = "http://127.0.0.1:%d" % cfg.port
    env = {
        "home": str(home),
        "port": cfg.port,
        "token_file": str(home / e2e_common.WALL_JSON_FILENAME),
        "url_base": url_base,
        "page_url": "%s/%s/" % (url_base, cfg.token),
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
