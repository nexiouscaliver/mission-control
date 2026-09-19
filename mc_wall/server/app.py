"""Wall server: HTTP handler, boot self-test, and run loop."""

from __future__ import annotations

import dataclasses
import hashlib
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
import urllib.parse

from mc_wall.server import auth, state_contract, templates
from mc_wall.server.logging_setup import setup_logging
from mc_wall.server.matcher import Matcher
from mc_wall.server.monitor import HandshakeMonitor
from mc_wall.server.pending import (
    STATUS_AWAIT_BIRTH,
    STATUS_CLEARED,
    STATUS_PROMPT_ARMED,
    TERMINAL_STATUSES,
    PendingRecord,
    PendingStore,
)
from mc_wall.server.runner import SubprocessRunner

MAX_BODY_BYTES = 65536

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


def deep_link(repo_root: str) -> str:
    # safe="" percent-encodes the path separators too: /abs/repo -> %2Fabs%2Frepo.
    return "zcode://workspace/open?path=" + urllib.parse.quote(repo_root, safe="")


def choose_owed_action(
    state: dict, logger: logging.Logger
) -> typing.Optional[dict]:
    """Pick the one owed action the user must act on now, or None.

    Pure function (no runner, no store). Parked entries, entries without an
    int finished_signal_ms, and unknown kinds are each skipped with exactly
    ONE audit line; survivors rank by (finished_signal_ms, row_id) — the
    pinned tie-break is lower row_id wins.
    """
    valid = []
    for raw in state_contract.owed_actions(state):
        entry = raw if isinstance(raw, dict) else {}
        rid = entry.get(state_contract.OA_ROW_ID_KEY)
        rid_text = rid if isinstance(rid, str) and rid else "-"
        if entry.get(state_contract.OA_PARKED_KEY) is True:
            reason = "parked"
        else:
            signal = entry.get(state_contract.OA_FINISHED_SIGNAL_MS_KEY)
            if not isinstance(signal, int) or isinstance(signal, bool):
                reason = "invalid-finished-signal"  # never ranked oldest-by-default
            elif entry.get(state_contract.OA_KIND_KEY) not in ("verify", "merge"):
                reason = "unknown-kind"
            else:
                valid.append((signal, rid_text, entry))
                continue
        if logger is not None:
            logger.info(
                "needs-me-now-skipped reason=%s row_id=%s", reason, rid_text
            )
    if not valid:
        return None
    valid.sort(key=lambda item: (item[0], item[1]))
    signal, _rid_text, entry = valid[0]
    kind = entry.get(state_contract.OA_KIND_KEY)
    source_key = (
        state_contract.OA_VERIFY_CMD_KEY
        if kind == "verify"
        else state_contract.OA_MR_LINK_KEY
    )
    return {
        "row_id": entry.get(state_contract.OA_ROW_ID_KEY),
        "kind": kind,
        "copied": entry.get(source_key),
        "finished_signal_ms": signal,
    }


@dataclasses.dataclass
class ServerConfig:
    token: str
    port: int = DEFAULT_PORT
    wall_home: pathlib.Path = None  # default resolved in run_server: MC_WALL_HOME or ~/.zcode/mc-wall
    web_dir: pathlib.Path = None  # default repo_root/"web"
    state_dir: pathlib.Path = None  # default wall_home/"state"
    log_dir: pathlib.Path = None  # default wall_home/"logs"
    db_path: pathlib.Path = None  # since T2; None -> no monitor (T9)
    runner: typing.Any = None  # None -> SubprocessRunner() in run_server (prod only)
    monitor_interval_s: float = 2.0
    clock: typing.Optional[typing.Callable[[], int]] = None  # epoch-ms
    start_monitor: bool = True


@dataclasses.dataclass
class AppContext:
    token: str
    web_dir: pathlib.Path
    log_dir: pathlib.Path
    collect_state: typing.Optional[typing.Callable[[], dict]] = None  # None -> lazy tower wrapper (T5)
    runner: typing.Any = None  # None -> POST routes reply 500 (T6); run_server injects SubprocessRunner()
    pending: typing.Any = None  # PendingStore or None (T5 wires)
    allow_hosts: typing.FrozenSet[str] = auth.ALLOWED_HOSTS
    logger: logging.Logger = None
    clock: typing.Optional[typing.Callable[[], int]] = None  # epoch-ms; default int(time.time()*1000)


class WallRequestHandler(http.server.BaseHTTPRequestHandler):
    """Request pipeline, pinned order: Host gate -> token -> 302 -> GET/HEAD
    routes -> POST guard pipeline (411/413/415/400) + POST routes -> 405 for
    other methods. HEAD runs the GET path with the body suppressed. No
    Access-Control-* header is ever emitted (structural: _reply never adds one)."""

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
        # 5. POST routes (T6): guard pipeline, then launch / cancel / re-copy.
        if method == "POST":
            self._dispatch_post(rest)
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
            "application/json; charset=utf-8",
            head_only=head_only,
        )

    # ------------------------------------------------------------------ POST

    def _dispatch_post(self, rest: str) -> None:
        """POST guard pipeline, pinned order — before any route handler runs."""
        ctx = self.server.ctx
        # a) Transfer-Encoding present (any value) -> 411 (never chunked input).
        if self.headers.get("Transfer-Encoding") is not None:
            self._post_error(411, "length-required", "content-length required")
            return
        # b) Content-Length missing / unusable / > 65536 -> 413 BEFORE reading.
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            self._post_error(413, "payload-too-large", "content-length required")
            return
        try:
            length = int(length_header)
        except ValueError:
            self._post_error(413, "payload-too-large", "unusable content-length")
            return
        if length < 0 or length > MAX_BODY_BYTES:  # negative would block the read
            self._post_error(413, "payload-too-large", "body exceeds 65536 bytes")
            return
        # c) Content-Type must be application/json (parameters tolerated).
        content_type = self.headers.get("Content-Type")
        if content_type is None or not content_type.strip().lower().startswith(
            "application/json"
        ):
            self._post_error(415, "unsupported-media-type", "application/json required")
            return
        # d) Capped read + JSON-dict check.
        raw = self.rfile.read(min(length, MAX_BODY_BYTES))
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:  # JSONDecodeError / UnicodeDecodeError
            self._post_error(400, "bad-request", "body must be a JSON object")
            return
        if not isinstance(body, dict):
            self._post_error(400, "bad-request", "body must be a JSON object")
            return
        # Route. These routes require the pending store and the runner to be
        # configured (state_dir + runner wiring); otherwise 500, never a guess.
        if rest in ("launch", "launch/cancel", "launch/re-copy"):
            if ctx.pending is None or ctx.runner is None:
                self._post_error(500, "internal-error", "server state not configured")
                return
            if rest == "launch":
                self._launch(body)
            elif rest == "launch/cancel":
                self._cancel(body)
            else:
                self._recopy(body)
            return
        if rest in ("copy-goal", "activate-app", "needs-me-now"):
            # These touch the clipboard/app but never the pending slot.
            if ctx.runner is None:
                self._post_error(500, "internal-error", "server state not configured")
                return
            if rest == "copy-goal":
                self._copy_goal(body)
            elif rest == "activate-app":
                self._activate_app(body)
            else:
                self._needs_me_now(body)
            return
        self._not_found()

    def _post_error(self, status: int, error: str, message: str) -> None:
        self._reply(
            status,
            json.dumps({"ok": False, "error": error, "message": message}).encode(
                "utf-8"
            ),
            "application/json; charset=utf-8",
        )

    def _pending_dict(self) -> typing.Optional[dict]:
        ctx = self.server.ctx
        if ctx.pending is None:
            return None
        record = ctx.pending.snapshot()
        return record.to_dict() if record is not None else None

    def _resolve_row(self, row_id: str):
        """Row lookup with NO lock held: returns (row, None) or (None, reply-sent)."""
        ctx = self.server.ctx
        try:
            return state_contract.find_row(ctx.collect_state(), row_id), None
        except state_contract.UnknownRowError:
            self._post_error(404, "unknown-row", "no such row")
            return None, True
        except Exception as exc:  # degraded, same shape as /state
            self._state_degraded(type(exc).__name__, self._pending_dict(), False)
            return None, True

    def _launch(self, body: dict) -> None:
        ctx = self.server.ctx
        store = ctx.pending
        # 1. Validation (400) — no filesystem existence check on repo_root.
        row_id = body.get("row_id")
        repo_root = body.get("repo_root")
        if not isinstance(row_id, str) or not row_id:
            self._post_error(400, "bad-request", "row_id must be a non-empty string")
            return
        if not isinstance(repo_root, str) or not os.path.isabs(repo_root):
            self._post_error(400, "bad-request", "repo_root must be an absolute path")
            return
        # 2. Resolve the row (runner untouched, nothing persisted on failure).
        row, replied = self._resolve_row(row_id)
        if replied:
            return
        prompt = row.get(state_contract.PROMPT_TEXT_KEY, "")
        lane_tag = row.get(state_contract.LANE_TAG_KEY, "")
        rec = PendingRecord(
            status=STATUS_PROMPT_ARMED,
            row_id=row_id,
            lane_tag=lane_tag,
            repo_root=repo_root,
            prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            launch_click_ms=(ctx.clock or _epoch_ms)(),
        )
        # 3. ONE locked step: slot check + create (TOCTOU-free). update() runs
        #    the mutate under the store RLock and performs the durable write
        #    there — the record is persisted BEFORE any side effect.
        conflict: typing.Dict[str, PendingRecord] = {}

        def _occupy(current: typing.Optional[PendingRecord]):
            if (
                current is not None
                and current.status not in TERMINAL_STATUSES
            ):  # slot_occupied(), evaluated inside the lock
                conflict["current"] = current
                return None  # occupied — no write
            return rec

        created = store.update(_occupy)
        if "current" in conflict:
            self._reply(
                409,
                json.dumps(
                    {
                        "ok": False,
                        "error": "launch-pending",
                        "message": "a launch is already pending",
                        "pending": conflict["current"].to_dict(),
                    }
                ).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return
        # 4. Side effects, strictly after the durable persist; each wrapped.
        runner = ctx.runner
        warnings = []
        try:
            runner.copy(
                templates.render_file(
                    "prompt_block.txt", {"prompt_text": prompt}
                )  # identity wrapper -> prompt verbatim
            )
        except Exception as exc:
            warnings.append(f"side-effect-failed:{type(exc).__name__}")
        try:
            runner.open_url(deep_link(repo_root))
        except Exception as exc:
            warnings.append(f"side-effect-failed:{type(exc).__name__}")
        # 5. Promote to await-birth. Identity-guarded: a cancel racing the
        #    side effects replaces the record and is never resurrected.
        def _promote(current: typing.Optional[PendingRecord]):
            if current is rec:
                rec.status = STATUS_AWAIT_BIRTH
                return rec
            return None

        after = store.update(_promote) or store.snapshot() or rec
        # 6. Audit — summary() triple only; never token/prompt/repo/lane tag.
        if ctx.logger is not None:
            ctx.logger.info(
                "audit action=launch row_id=%s pending=%s",
                row_id,
                json.dumps(rec.summary()),
            )
        # 7.
        self._reply(
            200,
            json.dumps(
                {"ok": True, "pending": after.to_dict(), "warnings": warnings}
            ).encode("utf-8"),
            "application/json",
        )

    def _cancel(self, body: dict) -> None:
        # body is any dict ({} accepted) — nothing read from it.
        ctx = self.server.ctx
        store = ctx.pending
        seen: typing.Dict[str, typing.Optional[PendingRecord]] = {}

        def _clear(current: typing.Optional[PendingRecord]):
            seen["current"] = current
            if current is None or current.status in TERMINAL_STATUSES:
                return None  # nothing live — idempotent no-op, no write
            return dataclasses.replace(
                current, status=STATUS_CLEARED, reason="cancel"
            )

        cleared = store.update(_clear)
        current = seen.get("current")
        if ctx.logger is not None:
            # Post-action state when one was written; the seen record (already
            # terminal) for the idempotent no-op; shape fallback otherwise.
            record = cleared if cleared is not None else current
            summary = (
                record.summary()
                if record is not None
                else {"status": None, "row_id": "", "flag": None}
            )
            ctx.logger.info(
                "audit action=cancel row_id=%s pending=%s",
                summary["row_id"],
                json.dumps(summary),
            )
        self._reply(
            200,
            json.dumps(
                {
                    "ok": True,
                    "pending": cleared.to_dict() if cleared is not None else None,
                }
            ).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _recopy(self, body: dict) -> None:
        ctx = self.server.ctx
        store = ctx.pending
        rec = store.snapshot()
        if rec is None or rec.status not in (STATUS_PROMPT_ARMED, STATUS_AWAIT_BIRTH):
            self._post_error(
                409, "not-await-birth", "re-copy only while awaiting birth"
            )
            return
        # Cannot re-copy without the prompt: same resolution as launch.
        row, replied = self._resolve_row(rec.row_id)
        if replied:
            return
        prompt = row.get(state_contract.PROMPT_TEXT_KEY, "")
        runner = ctx.runner
        warnings = []
        try:
            runner.copy(
                templates.render_file("prompt_block.txt", {"prompt_text": prompt})
            )
        except Exception as exc:
            warnings.append(f"side-effect-failed:{type(exc).__name__}")
        try:
            runner.open_url(deep_link(rec.repo_root))
        except Exception as exc:
            warnings.append(f"side-effect-failed:{type(exc).__name__}")
        # Clear the flag; launch_click_ms and status stay untouched.
        def _unflag(current: typing.Optional[PendingRecord]):
            if current is None:
                return None
            return dataclasses.replace(current, flag=None)

        after = store.update(_unflag) or store.snapshot()
        if ctx.logger is not None:
            ctx.logger.info(
                "audit action=re-copy row_id=%s pending=%s",
                rec.row_id,
                json.dumps((after or rec).summary()),
            )
        self._reply(
            200,
            json.dumps(
                {
                    "ok": True,
                    "pending": after.to_dict() if after is not None else None,
                    "warnings": warnings,
                }
            ).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _copy_goal(self, body: dict) -> None:
        ctx = self.server.ctx
        # 1. Validation (400).
        row_id = body.get("row_id")
        if not isinstance(row_id, str) or not row_id:
            self._post_error(400, "bad-request", "row_id must be a non-empty string")
            return
        # 2. Resolve the row (runner untouched on failure).
        row, replied = self._resolve_row(row_id)
        if replied:
            return
        # 3. Render + copy. The {repo_root} token renders from the ROW's own
        #    data — the request carries only row_id (state_contract is the
        #    reconciliation point).
        block = templates.render_file(
            "goal_block.txt",
            {
                "goal_text": row.get(state_contract.GOAL_TEXT_KEY, ""),
                "lane_tag": row.get(state_contract.LANE_TAG_KEY, ""),
                "repo_root": row.get(state_contract.ROW_REPO_ROOT_KEY, ""),
            },
        )
        ctx.runner.copy(block)
        if ctx.logger is not None:
            ctx.logger.info("audit action=copy-goal row_id=%s", row_id)
        self._reply(
            200,
            json.dumps({"ok": True, "copied": "goal"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _activate_app(self, body: dict) -> None:
        ctx = self.server.ctx
        # App activation only — never a URL (the deep link is launch's job).
        ctx.runner.open_app("ZCode")
        if ctx.logger is not None:
            ctx.logger.info("audit action=activate-app")
        self._reply(
            200,
            json.dumps({"ok": True}).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _needs_me_now(self, body: dict) -> None:
        ctx = self.server.ctx
        try:
            state = ctx.collect_state()
        except Exception as exc:  # never guess — degraded, not empty
            self._state_degraded(type(exc).__name__, self._pending_dict(), False)
            return
        action = choose_owed_action(state, ctx.logger)
        if action is not None and action.get("copied") is not None:
            # verify -> copy AND raise the app; merge -> copy only.
            ctx.runner.copy(action["copied"])
            if action.get("kind") == "verify":
                ctx.runner.open_app("ZCode")
        if ctx.logger is not None:
            ctx.logger.info(
                "audit action=needs-me-now row_id=%s",
                (action or {}).get("row_id") or "-",
            )
        self._reply(
            200,
            json.dumps({"ok": True, "action": action}).encode("utf-8"),
            "application/json; charset=utf-8",
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
            "application/json; charset=utf-8",
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
        self.monitor = None  # HandshakeMonitor (T9); stopped FIRST on shutdown

    def shutdown(self) -> None:
        # Monitor first: no tick may outlive the HTTP server it serves.
        if self.monitor is not None:
            self.monitor.stop()
        super().shutdown()

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
        except Exception:
            # HTTPException (e.g. BadStatusLine) is not an OSError subclass;
            # without this guard it would escape start() before its
            # shutdown/server_close cleanup, leaking the socket + serve thread.
            return False
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
    runner: typing.Any = None,
    db_path: os.PathLike = None,
    monitor_interval_s: float = 2.0,
    clock: typing.Optional[typing.Callable[[], int]] = None,
    start_monitor: bool = True,
) -> WallServer:
    web_path = pathlib.Path(web_dir) if web_dir is not None else _repo_root() / "web"
    log_path = pathlib.Path(log_dir) if log_dir is not None else _default_wall_home() / "logs"
    allowed = (
        frozenset(allow_hosts) if allow_hosts is not None else auth.ALLOWED_HOSTS
    )
    # runner=None stays None (tests inject FakeRunner; the POST routes reply
    # 500 internal-error when the runner is missing). run_server — the only
    # prod path — injects the SubprocessRunner() default.
    ctx = AppContext(
        token=token,
        web_dir=web_path,
        log_dir=log_path,
        allow_hosts=allowed,
        collect_state=(
            collect_state if collect_state is not None else _default_collect_state
        ),
        runner=runner,
        clock=clock,
    )
    # Construct first: OSError on a busy port propagates BEFORE any logging.
    server = WallServer((host, port), ctx)
    logger = setup_logging(log_path, token)
    ctx.logger = logger
    # Boot creates both dirs (log dir via setup_logging). mkdir the RESOLVED
    # state dir — never a guessed sibling of log_dir when one is injected.
    state_path = (
        pathlib.Path(state_dir) if state_dir is not None else log_path.parent / "state"
    )
    state_path.mkdir(parents=True, exist_ok=True)
    if state_dir is not None:
        # Recovery load at construction, before start()/any request (T11 relies on this).
        # The store shares create_server's clock so launch_click_ms and every
        # monitor tick read one timeline.
        ctx.pending = PendingStore(state_path, clock=clock)
        ctx.pending.load()
    server.start()
    if (
        db_path is not None
        and start_monitor
        and server.ready
        and ctx.pending is not None
    ):
        monitor = HandshakeMonitor(
            ctx.pending,
            Matcher(pathlib.Path(db_path)),
            ctx.runner,
            ctx.collect_state,
            logger,
            interval_s=monitor_interval_s,
            clock=clock,
        )
        server.monitor = monitor
        monitor.start()
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
            runner=cfg.runner if cfg.runner is not None else SubprocessRunner(),
            db_path=cfg.db_path,
            monitor_interval_s=cfg.monitor_interval_s,
            clock=cfg.clock,
            start_monitor=cfg.start_monitor,
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
