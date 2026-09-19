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
"""

import subprocess
import threading
from dataclasses import dataclass
from typing import Callable


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
        self._table_lock = threading.Lock()

    def fetch(self, key: tuple[str, str, str], argv: list[str], cwd: str,
              now_s: Callable[[], float], ttl_s: int, backoff_base_s: float,
              backoff_max_s: float) -> FetchResult:
        """Fetch through the cache for one normalized key.

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
