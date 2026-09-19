"""Memory-only network cache; the ONLY module allowed to spawn subprocesses.

T-5 records the verified glab/gh flag spellings here and fleshes out
``NetCache.fetch`` (TTL / single-flight / backoff). This module must never
import ``config`` (no import cycle: config imports NetCache, fetch receives
its settings as scalars).
"""

import subprocess


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


class NetCache:
    """Memory-only network cache. TTL/single-flight/backoff arrive in T-5."""

    def __init__(self) -> None:
        self._entries = {}    # key -> cached (stdout, observed_at_s)
        self._locks = {}      # key -> threading.Lock   (used from T-5)
        self._fails = {}      # key -> consecutive-failure count
        self._last_fail = {}  # key -> failure timestamp
