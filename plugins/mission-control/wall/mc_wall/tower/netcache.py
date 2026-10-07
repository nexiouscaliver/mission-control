"""Memory-only network cache; the ONLY module allowed to spawn subprocesses.

This module must never import ``config`` (no import cycle: config imports
NetCache, ``fetch`` receives its settings as scalars).

Verified CLI flag spellings (spec §4.4 / assumption 13) — checked 2026-09-19
against the INSTALLED CLIs via read-only ``--help`` only (glab 1.67.0,
gh 2.78.0, /opt/homebrew; no network, no auth). Every subprocess argv used by
``signals.py`` routes through ``_run_cmd`` here:

- pushed (git):        ["git", "ls-remote", "origin", <branch>]
- gitlab MR by ref:    ["glab", "mr", "view", <N>, "--output", "json"]
- gitlab MR by branch: ["glab", "mr", "list", "--source-branch", <branch>,
                        "--output", "json"]
- github PR by ref:    ["gh", "pr", "view", <N>, "--json",
                        "number,state,title,createdAt"]
- github PR by branch: ["gh", "pr", "list", "--head", <branch>, "--json",
                        "number,state,title,createdAt"]

glab 1.67.0 has NO per-field ``--json`` picker (unlike gh); its machine-readable
switch is ``-F/--output json`` (per ``glab mr list --help`` / ``glab mr view
--help``: ``-F, --output string  Format output as: text, json``), which emits
the raw GitLab API object/array — iid/state/title/created_at, with the pipeline
status read from the API object's ``head_pipeline.status`` falling back to
``pipeline.status`` (see ``signals.adapt_cli_mr``). gh's pipeline would need the
``statusCheckRollup`` --json field plus rollup aggregation; v1 requests the
four plan fields only and leaves github ``pipeline`` "" (recorded deferral).
``glab mr list`` defaults to opened-state MRs and ``gh pr list`` to ``--state
open`` (both confirmed in their help texts); the by-branch reader still filters
``state == "open"`` client-side, so those defaults are not load-bearing.

Recorded v1 choice: a BY-REF lookup that returns rc 0 with empty/unusable
stdout (NOT-FOUND) is treated as a lookup FAILURE (mr null + entry 6 /
precondition unknown) — absence-as-degraded, per plan T-5. A BY-BRANCH rc 0
with a parseable empty list is a VALID empty result (mr null, no entry).

Cache keys are normalized tuples ``(kind, repo_name, ref_or_branch)`` with kind
in {"git_ls_remote", "mr_by_ref", "mr_by_branch"} — repo by CONFIG name, ref
verbatim (the notes grammar yields canonical ``!N``/``#N`` tokens) — so an
artifacts-ref lookup and a precondition lookup of the same ref on one repo
share one entry. Semantics per spec §7: TTL 120 s (an entry is expired STRICTLY
past ttl; still fresh at exactly ttl), per-key single-flight (one in-flight
spawn shared by concurrent callers), per-key failure backoff base 30 s doubling
to the 600 s cap with success reset; failures are recorded for backoff but
never served as values. All timings come from the injected ``now_s``.
Memory-only: the tower writes nothing to disk, ever.

wc1 (collect-cost, 2026-10-07): two fetch semantics over one cache.
``fetch`` keeps the blocking contract above (warm collects — the boot
self-check and the CLI). ``fetch_cached`` is the CACHE-FIRST serving path:
a fresh entry serves as-is; an EXPIRED entry serves immediately as the
last good value (``stale=True`` — the caller carries its age; a stale
last-good is never served as fresh, and the age tells the truth); a key
with NO entry yet returns failure-shaped with ``pending=True`` (the caller
renders the same nulls as a failure, MINUS the degraded entry — "not yet
observed, refresh in flight" is not a failure) while a bounded background
refresh runs; a key inside its failure backoff window keeps serving stale
and throttles refresh retries exactly like ``fetch``. Background refreshes
run on the process-wide ``shared_spawn_pool()`` — the SAME bounded pool
the warm collect's per-lane/per-repo probes use — so concurrent spawn
work is machine-sane (``spawn_workers()`` = min(8, cpus)) no matter how
many keys expire at once. Single-flight is preserved: ``_refreshing``
(one entry per key, guarded by the table lock) makes every spawn path —
blocking fetch, pool item, background refresh — share one in-flight spawn
per key, and NetCache stays the ONLY module that spawns.
"""

import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable


def spawn_workers() -> int:
    """The machine-sane bound on CONCURRENT spawn work: min(8, cpus). One
    number for every pool consumer (warm-collect probes, background cache
    refreshes) so the whole process never exceeds it."""
    return min(8, os.cpu_count() or 1)


_SHARED_POOL: ThreadPoolExecutor | None = None
_SHARED_POOL_LOCK = threading.Lock()


def shared_spawn_pool() -> ThreadPoolExecutor:
    """Process-wide bounded executor for ALL spawn work. Created lazily on
    first use (a process that never spawns — most of the test suite — never
    pays for worker threads); never shut down (the server process owns its
    lifetime; short-lived callers just submit and gather)."""
    global _SHARED_POOL
    if _SHARED_POOL is None:
        with _SHARED_POOL_LOCK:
            if _SHARED_POOL is None:
                _SHARED_POOL = ThreadPoolExecutor(
                    max_workers=spawn_workers(),
                    thread_name_prefix="mc-wall-spawn")
    return _SHARED_POOL


def _run_cmd(argv: list[str], cwd: str, timeout_s: float = 10.0) -> tuple[int | None, str, str]:
    """Run argv in cwd, capture output; NEVER propagates an exception.

    Spec §4.4: timeout, rc != 0, OR ANY exception (FileNotFoundError, OSError,
    subprocess.TimeoutExpired, spawn failures) = failure -> (None, "", "").
    BaseException is caught deliberately (per plan): a probe helper must not
    swallow-interrupt its caller either way, and spec §4.4 says ANY exception;
    SystemExit/KeyboardInterrupt from a probe spawn is a failure result, not a
    propagating signal.
    """
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout_s)
        return p.returncode, p.stdout, p.stderr
    except BaseException:
        return None, "", ""


@dataclass(frozen=True)
class FetchResult:
    ok: bool
    stdout: str
    observed_at_s: float
    stale: bool = False    # cache-first: a LAST GOOD entry served past its TTL (age is the honest marker)
    pending: bool = False  # cache-first: no entry yet; a refresh is in flight (absence, not failure)


class NetCache:
    """Memory-only network cache (spec §7): TTL / single-flight / backoff.

    ``_table_lock`` guards ONLY the key->state dict identities (the locks
    table); every entry/failure mutation happens under the per-key lock, which
    is held ACROSS the spawn so concurrent callers share one subprocess run.
    """

    def __init__(self) -> None:
        self._entries = {}               # key -> cached (stdout, observed_at_s)
        self._locks = {}                 # key -> threading.Lock
        self._fails = {}                 # key -> consecutive-failure count
        self._last_fail = {}             # key -> failure timestamp
        self._refreshing = set()         # key -> a spawn is in flight (blocking OR background)
        self._table_lock = threading.Lock()

    def fetch(self, key: tuple[str, str, str], argv: list[str], cwd: str,
              now_s: Callable[[], float], ttl_s: int, backoff_base_s: float,
              backoff_max_s: float) -> FetchResult:
        """Fetch through the cache for one normalized key (BLOCKING — the
        warm-collect path; ``fetch_cached`` is the never-blocking serve path).

        Fresh entry (age <= ttl_s, so still fresh at EXACTLY ttl) is served
        as-is. Otherwise, when the key is inside its failure backoff window
        (elapsed < min(base * 2**(n-1), cap) since the last failure), the call
        short-circuits to a failure result WITHOUT spawning. On a spawn, any
        raising ``_run_cmd`` (monkeypatched or broken) degrades to the rc=None
        failure path (plan F1): this key fails, the document survives with its
        degraded entries — never an exception past fetch. A success replaces
        the entry and resets the backoff counter; a failure is recorded for
        backoff but never served as a value.
        """
        with self._table_lock:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:  # single-flight per key, held across the spawn
            now = now_s()
            ent = self._entries.get(key)
            if ent is not None and now - ent[1] <= ttl_s:
                return FetchResult(True, ent[0], ent[1])
            n = self._fails.get(key, 0)
            last = self._last_fail.get(key)
            if n and last is not None:
                wait = min(backoff_base_s * 2 ** (n - 1), backoff_max_s)
                if now - last < wait:
                    return FetchResult(False, "", now)  # short-circuit: NO spawn
            with self._table_lock:
                self._refreshing.add(key)  # wc1: visible to fetch_cached kicks
            try:
                try:
                    rc, out, _err = _run_cmd(argv, cwd)
                except Exception:
                    # A raising _run_cmd (monkeypatched in tests, or a broken
                    # helper) degrades THIS key per plan F1 — the AC-FAIL-9
                    # mechanism: never a raise, never a zeroed document.
                    rc, out, _err = None, "", ""
                t = now_s()
                if rc == 0:
                    self._entries[key] = (out, t)
                    self._fails[key] = 0
                    self._last_fail[key] = None
                    return FetchResult(True, out, t)
                self._fails[key] = n + 1
                self._last_fail[key] = t
                return FetchResult(False, "", t)
            finally:
                with self._table_lock:
                    self._refreshing.discard(key)

    def fetch_cached(self, key: tuple[str, str, str], argv: list[str],
                     cwd: str, now_s: Callable[[], float], ttl_s: int,
                     backoff_base_s: float, backoff_max_s: float) -> FetchResult:
        """Cache-first fetch (wc1): this caller NEVER waits on a spawn.

        Fresh entry -> (ok, stdout, observed_at, stale=False), no spawn.
        Expired entry -> the LAST GOOD value served IMMEDIATELY with
        stale=True (never as fresh; the caller renders its age) while a
        background refresh is kicked (single-flight via ``_refreshing``,
        retry-throttled by the same failure backoff as ``fetch``).
        No entry -> failure-shaped with pending=True (a refresh is kicked
        unless one is in flight or backoff throttles it) — the caller
        renders nulls WITHOUT a degraded entry: "not yet observed" is
        absence, not failure. A no-entry key inside its failure backoff
        window returns pending=False (recently TRIED and failed — the
        degraded entry is honest there, exactly like ``fetch``).

        The entries read is deliberately lock-free (dict get + an immutable
        tuple): taking the per-key lock here would block on any in-flight
        spawn for that key — the exact wait this method exists to remove —
        and the worst race is serving the previous entry, a valid
        observation either way.
        """
        now = now_s()
        ent = self._entries.get(key)
        if ent is not None:
            if now - ent[1] <= ttl_s:
                return FetchResult(True, ent[0], ent[1])
            self._kick(key, argv, cwd, now_s, ttl_s, backoff_base_s,
                       backoff_max_s, now)
            return FetchResult(True, ent[0], ent[1], stale=True)
        kicked = self._kick(key, argv, cwd, now_s, ttl_s, backoff_base_s,
                            backoff_max_s, now)
        return FetchResult(False, "", now, pending=kicked)

    def _kick(self, key, argv, cwd, now_s, ttl_s, backoff_base_s,
              backoff_max_s, now) -> bool:
        """Start ONE background refresh for ``key`` on the shared bounded
        pool if none is in flight and the failure backoff allows a retry.
        Returns whether a refresh is now in flight or was just kicked."""
        with self._table_lock:
            if key in self._refreshing:
                return True
            n = self._fails.get(key, 0)
            last = self._last_fail.get(key)
            if n and last is not None:
                wait = min(backoff_base_s * 2 ** (n - 1), backoff_max_s)
                if now - last < wait:
                    return False  # throttled: keep serving stale / nulls
            self._refreshing.add(key)
        try:
            shared_spawn_pool().submit(
                self._refresh_task, key, argv, cwd, now_s, ttl_s,
                backoff_base_s, backoff_max_s)
        except Exception:  # a shut-down pool (process teardown) — unmark, never raise
            with self._table_lock:
                self._refreshing.discard(key)
            return False
        return True

    def _refresh_task(self, key, argv, cwd, now_s, ttl_s, backoff_base_s,
                      backoff_max_s) -> None:
        try:
            # fetch re-checks freshness under the per-key lock first: a
            # blocking fetch (warm path) or an earlier task may have
            # refreshed this key while we sat in the pool queue.
            self.fetch(key, argv, cwd, now_s, ttl_s, backoff_base_s,
                       backoff_max_s)
        finally:
            with self._table_lock:
                self._refreshing.discard(key)

    def refreshes_in_flight(self) -> int:
        """Diagnostic/test seam: how many background refreshes are live."""
        with self._table_lock:
            return len(self._refreshing)

    def wait_refreshes(self, timeout_s: float = 10.0) -> bool:
        """Block until no background refresh is in flight (bounded wait).
        Test/diagnostic seam — the serving path never calls this."""
        import time as _time
        deadline = _time.monotonic() + timeout_s
        while _time.monotonic() < deadline:
            if self.refreshes_in_flight() == 0:
                return True
            _time.sleep(0.01)
        return self.refreshes_in_flight() == 0


class ServingCache:
    """Cache-first facade (wc1): the serve-mode collect's drop-in for a
    ``NetCache``. Same ``fetch`` signature, every call routed to
    ``fetch_cached`` — a /state collect serves fresh/stale entries
    immediately and kicks bounded background refreshes; it NEVER waits on a
    subprocess spawn. The warm paths (boot self-check, CLI) keep calling the
    NetCache directly (blocking semantics)."""

    def __init__(self, inner: NetCache) -> None:
        self._inner = inner

    def fetch(self, key: tuple[str, str, str], argv: list[str], cwd: str,
              now_s: Callable[[], float], ttl_s: int, backoff_base_s: float,
              backoff_max_s: float) -> FetchResult:
        return self._inner.fetch_cached(key, argv, cwd, now_s, ttl_s,
                                        backoff_base_s, backoff_max_s)
