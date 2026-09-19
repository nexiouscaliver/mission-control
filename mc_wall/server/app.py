"""Wall server: HTTP handler, boot self-test, and run loop."""

from __future__ import annotations

import dataclasses
import http.client
import http.server
import json
import logging
import os
import pathlib
import signal
import threading
import typing

from mc_wall.server.logging_setup import setup_logging

DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"


def _default_wall_home() -> pathlib.Path:
    env = os.environ.get("MC_WALL_HOME")
    if env:
        return pathlib.Path(env)
    return pathlib.Path.home() / ".zcode" / "mc-wall"


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


@dataclasses.dataclass
class ServerConfig:
    token: str
    port: int = DEFAULT_PORT
    wall_home: pathlib.Path = None  # default resolved in run_server: MC_WALL_HOME or ~/.zcode/mc-wall
    web_dir: pathlib.Path = None  # default repo_root/"web"
    state_dir: pathlib.Path = None  # default wall_home/"state"
    log_dir: pathlib.Path = None  # default wall_home/"logs"
    db_path: pathlib.Path = None  # wired in T9; None -> no monitor


@dataclasses.dataclass
class AppContext:
    token: str
    web_dir: pathlib.Path
    log_dir: pathlib.Path
    collect_state: typing.Optional[typing.Callable[[], dict]] = None  # None -> lazy tower wrapper (T5)
    runner: typing.Any = None  # None -> SubprocessRunner (T4 wires; until then unused)
    pending: typing.Any = None  # PendingStore or None (T5 wires)
    allow_hosts: typing.FrozenSet[str] = frozenset({"127.0.0.1", "localhost"})  # inline literal in T2 (auth.py does not exist yet); T3 switches this default to auth.ALLOWED_HOSTS
    logger: logging.Logger = None
    clock: typing.Optional[typing.Callable[[], int]] = None  # epoch-ms; default int(time.time()*1000)


class WallRequestHandler(http.server.BaseHTTPRequestHandler):
    """T2 placeholder routing: Host gate + 404 JSON for everything else.

    Full routing (token check, static, redirect/HEAD) arrives in T3; until
    then every request that passes the Host gate answers the not-found JSON.
    """

    def log_message(self, format, *args):  # noqa: A002
        # No-op: the default would log self.path — a token leak (AC-11).
        return

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_HEAD(self) -> None:
        self._dispatch(head_only=True)

    def _dispatch(self, head_only: bool = False) -> None:
        # Minimal inline Host gate in T2 (auth.py arrives in T3): normalize the
        # same way T3's auth.host_allowed will — strip, lower, drop :port.
        host = self.headers.get("Host")
        host_name = host.strip().lower().rsplit(":", 1)[0] if host else None
        if not host_name or host_name not in self.server.ctx.allow_hosts:
            self._reply(403, b"forbidden\n", "text/plain", head_only=head_only)
            return
        body = json.dumps(
            {"ok": False, "error": "not-found", "message": "not found"}
        ).encode("utf-8")
        self._reply(404, body, "application/json", head_only=head_only)

    def _reply(self, status: int, body: bytes, content_type: str, head_only: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)


class WallServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: typing.Tuple[str, int], ctx: "AppContext"):
        super().__init__(address, WallRequestHandler)
        self.ctx = ctx
        self.ready = False
        self._serve_thread = None

    def start(self) -> bool:
        self._serve_thread = threading.Thread(
            target=self.serve_forever, daemon=True, name="mc-wall-serve"
        )
        self._serve_thread.start()
        if not _self_test(self):
            if self.ctx.logger is not None:
                self.ctx.logger.error("self-test failed; not marking server ready")
            self.shutdown()
            self.server_close()
            return False
        self.ready = True
        return True

    def wait_shutdown(self) -> None:
        if self._serve_thread is not None:
            self._serve_thread.join(timeout=10)


def _self_test(server: "WallServer") -> bool:
    port = server.server_address[1]
    probes = (("evil.selftest.invalid", False), ("127.0.0.1", True))  # (Host header, must_not_be_403)
    for host_header, must_pass in probes:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request("GET", f"/{server.ctx.token}/", headers={"Host": host_header})
            resp = conn.getresponse()
            resp.read()
            status = resp.status
        finally:
            conn.close()
        if (status == 403) == must_pass:  # 403 on the ALLOWED probe, or non-403 on the EVIL probe -> fail
            return False
    return True


def create_server(
    token: str,
    *,
    port: int = DEFAULT_PORT,
    host: str = DEFAULT_HOST,
    web_dir: os.PathLike = None,
    log_dir: os.PathLike = None,
    allow_hosts: typing.Optional[typing.Iterable[str]] = None,
) -> WallServer:
    web_path = pathlib.Path(web_dir) if web_dir is not None else _repo_root() / "web"
    log_path = pathlib.Path(log_dir) if log_dir is not None else _default_wall_home() / "logs"
    allowed = (
        frozenset(allow_hosts)
        if allow_hosts is not None
        else frozenset({"127.0.0.1", "localhost"})
    )
    ctx = AppContext(token=token, web_dir=web_path, log_dir=log_path, allow_hosts=allowed)
    # Construct first: OSError on a busy port propagates BEFORE any logging.
    server = WallServer((host, port), ctx)
    logger = setup_logging(log_path, token)
    ctx.logger = logger
    (log_path.parent / "state").mkdir(parents=True, exist_ok=True)
    server.start()
    return server


def run_server(cfg: ServerConfig) -> int:
    wall_home = cfg.wall_home if cfg.wall_home is not None else _default_wall_home()
    web_dir = cfg.web_dir if cfg.web_dir is not None else _repo_root() / "web"
    log_dir = cfg.log_dir if cfg.log_dir is not None else wall_home / "logs"
    logger = setup_logging(log_dir, cfg.token)
    try:
        server = create_server(cfg.token, port=cfg.port, web_dir=web_dir, log_dir=log_dir)
    except OSError as exc:
        logger.error(
            "startup failed: cannot listen on 127.0.0.1:%s (%s)",
            cfg.port,
            type(exc).__name__,
        )
        return 1
    if not server.ready:
        return 3
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(
            sig,
            lambda *_: threading.Thread(target=server.shutdown, daemon=True).start(),
        )
    server.wait_shutdown()
    server.server_close()
    return 0
