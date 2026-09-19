"""Wall server: HTTP handler, boot self-test, and run loop."""

from __future__ import annotations

import dataclasses
import http.client
import http.server
import json
import logging
import os
import pathlib
import re
import signal
import threading
import time
import typing

from mc_wall.server import auth, templates
from mc_wall.server.logging_setup import setup_logging
from mc_wall.server.pending import PendingStore

DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"

ASSET_TYPES = {
    ".js": "text/javascript",
    ".css": "text/css",
    ".html": "text/html; charset=utf-8",
}

_ASSET_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _default_wall_home() -> pathlib.Path:
    env = os.environ.get("MC_WALL_HOME")
    if env:
        return pathlib.Path(env)
    return pathlib.Path.home() / ".zcode" / "mc-wall"


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def _epoch_ms() -> int:
    return int(time.time() * 1000)


def _default_collect_state() -> dict:
    from mc_wall.tower import collect_state  # LAZY — the only place mc_wall.tower is named in L2

    return collect_state()


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
    allow_hosts: typing.FrozenSet[str] = auth.ALLOWED_HOSTS
    logger: logging.Logger = None
    clock: typing.Optional[typing.Callable[[], int]] = None  # epoch-ms; default int(time.time()*1000)


class WallRequestHandler(http.server.BaseHTTPRequestHandler):
    """Request pipeline, pinned order: Host gate -> token -> 302 -> GET/HEAD
    routes -> POST placeholder (T6 wires) -> 405 for other methods. HEAD runs
    the GET path with the body suppressed. No Access-Control-* header is ever
    emitted (structural: _reply never adds one)."""

    def log_message(self, format, *args):  # noqa: A002
        # No-op: the default would log self.path — a token leak (AC-11).
        return

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_HEAD(self) -> None:
        self._dispatch("HEAD")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_OPTIONS(self) -> None:
        self._dispatch("OPTIONS")

    def _dispatch(self, method: str) -> None:
        ctx = self.server.ctx
        head_only = method == "HEAD"
        # 1. Host gate first (DNS-rebinding defense) — before anything that
        #    would echo token-shaped input.
        if not auth.host_allowed(self.headers.get("Host"), ctx.allow_hosts):
            self._reply(403, b"forbidden\n", "text/plain", head_only=head_only)
            return
        # 2. Token segment from the RAW path — never unquoted, so %2e%2e%2f
        #    can never become a path separator.
        raw = self.path.split("?", 1)[0]
        token_seg, sep, rest = raw.lstrip("/").partition("/")
        if not auth.token_matches(token_seg, ctx.token):
            self._reply(
                404,
                templates.load("wrong_token.html").encode("utf-8"),
                "text/html; charset=utf-8",
                head_only=head_only,
            )
            return
        # 3. GET/HEAD /<token> (no slash) -> canonical /<token>/.
        if not sep and method in ("GET", "HEAD"):
            self._reply(
                302,
                b"",
                None,
                head_only=head_only,
                extra_headers={"Location": f"/{ctx.token}/"},
            )
            return
        # 4. GET/HEAD routes.
        if method in ("GET", "HEAD"):
            if rest == "":
                self._serve_static("index.html", head_only=head_only)
            elif rest == "state":
                self._serve_state(head_only=head_only)
            elif rest.startswith("assets/"):
                self._serve_static(rest[len("assets/"):], head_only=head_only)
            else:
                self._not_found(head_only=head_only)
            return
        # 5. POST routes arrive in T6; until then 404 JSON not-found.
        if method == "POST":
            self._not_found(head_only=head_only)
            return
        # 6. PUT/DELETE/PATCH/OPTIONS (and anything else routed here).
        self._reply(
            405,
            json.dumps(
                {
                    "ok": False,
                    "error": "method-not-allowed",
                    "message": "method not allowed",
                }
            ).encode("utf-8"),
            "application/json",
            head_only=head_only,
        )

    def _serve_static(self, name: str, head_only: bool = False) -> None:
        ctx = self.server.ctx
        if (
            name in ("", ".", "..")
            or "/" in name
            or "\\" in name
            or name.startswith(".")
            or _ASSET_NAME_RE.fullmatch(name) is None
        ):
            self._not_found(head_only=head_only)
            return
        suffix = pathlib.Path(name).suffix
        if suffix not in ASSET_TYPES:
            self._not_found(head_only=head_only)
            return
        path = ctx.web_dir / name
        if not path.is_file():
            if ctx.logger is not None:
                # Asset name only — never self.path (token leak), no traceback.
                ctx.logger.warning("static-missing name=%s", name)
            self._not_found(head_only=head_only)
            return
        self._reply(
            200,
            path.read_bytes(),
            ASSET_TYPES[suffix],
            head_only=head_only,
            extra_headers={"Cache-Control": "no-store"},
        )

    def _serve_state(self, head_only: bool = False) -> None:
        # Verbatim tower passthrough — the collector runs on EVERY request
        # (no caching) and any failure degrades to 503 instead of guessing.
        ctx = self.server.ctx
        pending_dict = None
        if ctx.pending is not None:
            record = ctx.pending.snapshot()
            if record is not None:
                pending_dict = record.to_dict()
        try:
            tower = ctx.collect_state()
        except Exception as exc:  # ImportError, RuntimeError, anything
            self._state_degraded(type(exc).__name__, pending_dict, head_only)
            return
        if not isinstance(tower, dict):
            # Never guess a shape the tower did not return.
            self._state_degraded("NotADict", pending_dict, head_only)
            return
        body = dict(tower)
        body["wall"] = {"pending": pending_dict}
        self._reply(
            200,
            json.dumps(body).encode("utf-8"),
            "application/json; charset=utf-8",
            head_only=head_only,
            extra_headers={"Cache-Control": "no-store"},
        )

    def _state_degraded(
        self,
        detail: str,
        pending_dict: typing.Optional[dict],
        head_only: bool = False,
    ) -> None:
        ctx = self.server.ctx
        if ctx.logger is not None:
            # Exception type name only — never str(exc) (AC-11 discipline).
            ctx.logger.warning("state-degraded error=%s", detail)
        self._reply(
            503,
            json.dumps(
                {
                    "ok": False,
                    "degraded": True,
                    "error": "collect_state_failed",
                    "detail": detail,
                    "occurred_at_ms": (ctx.clock or _epoch_ms)(),
                    "wall": {"pending": pending_dict},
                }
            ).encode("utf-8"),
            "application/json; charset=utf-8",
            head_only=head_only,
            extra_headers={"Cache-Control": "no-store"},
        )

    def _not_found(self, head_only: bool = False) -> None:
        self._reply(
            404,
            json.dumps(
                {"ok": False, "error": "not-found", "message": "not found"}
            ).encode("utf-8"),
            "application/json",
            head_only=head_only,
        )

    def _reply(
        self,
        status: int,
        body: bytes,
        content_type: typing.Optional[str],
        head_only: bool = False,
        extra_headers: typing.Optional[typing.Dict[str, str]] = None,
    ) -> None:
        self.send_response(status)
        if content_type is not None:
            self.send_header("Content-Type", content_type)
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
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
    collect_state: typing.Optional[typing.Callable[[], dict]] = None,
    state_dir: os.PathLike = None,
) -> WallServer:
    web_path = pathlib.Path(web_dir) if web_dir is not None else _repo_root() / "web"
    log_path = pathlib.Path(log_dir) if log_dir is not None else _default_wall_home() / "logs"
    allowed = (
        frozenset(allow_hosts) if allow_hosts is not None else auth.ALLOWED_HOSTS
    )
    ctx = AppContext(
        token=token,
        web_dir=web_path,
        log_dir=log_path,
        allow_hosts=allowed,
        collect_state=(
            collect_state if collect_state is not None else _default_collect_state
        ),
    )
    # Construct first: OSError on a busy port propagates BEFORE any logging.
    server = WallServer((host, port), ctx)
    logger = setup_logging(log_path, token)
    ctx.logger = logger
    (log_path.parent / "state").mkdir(parents=True, exist_ok=True)
    if state_dir is not None:
        # Recovery load at construction, before start()/any request (T11 relies on this).
        ctx.pending = PendingStore(pathlib.Path(state_dir))
        ctx.pending.load()
    server.start()
    return server


def run_server(cfg: ServerConfig) -> int:
    wall_home = cfg.wall_home if cfg.wall_home is not None else _default_wall_home()
    web_dir = cfg.web_dir if cfg.web_dir is not None else _repo_root() / "web"
    log_dir = cfg.log_dir if cfg.log_dir is not None else wall_home / "logs"
    state_dir = cfg.state_dir if cfg.state_dir is not None else wall_home / "state"
    logger = setup_logging(log_dir, cfg.token)
    try:
        server = create_server(
            cfg.token,
            port=cfg.port,
            web_dir=web_dir,
            log_dir=log_dir,
            state_dir=state_dir,
        )
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
