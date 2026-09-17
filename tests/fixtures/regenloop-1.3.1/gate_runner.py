#!/usr/bin/env python3
"""gate_runner.py — the deterministic, zero-dependency gate engine (regenloop).

This is the *objective gate* described in the regenloop design spec, §4. It is
plain stdlib Python — NOT an LLM — so it is trustworthy and reproducible. It
runs every loop iteration, so it must run anywhere with no install step:

    python3 scripts/gate_runner.py --base <ref> [--config regenloop/gates.toml]
                                   [--json report.json] [--apply-autofix]
                                   [--gate <name>] [--timeout <seconds>]
                                   [--tier fast|full] [--no-baseline]
                                   [--allow-meta-changes] [--forbid-fenced]
                                   [--require-evidence] [--allow-dirty-autofix]
                                   [--allow-default-branch] [--forbid-default-branch]
                                   [--ledger <path>] [--ledger-source <str>]
                                   [--ledger-run-id <id>]
                                   [--jobs N] [--sequential]
                                   [--safe | --no-safe]

    python3 scripts/gate_runner.py --verify-tdd <red.json> <green.json>

Zero third-party imports (spec §4.1): config is TOML via stdlib ``tomllib``
(Python >= 3.12 required); everything else is ``argparse``/``subprocess``/
``json``/``socket``/``shlex``/``re``/``xml.etree.ElementTree``.

In addition to command gates, this also runs ``kind = "regression"`` gates
(spec §4): each runs a JUnit-emitting test suite on the working tree and on a
cached baseline at the merge-base, then reports only tests that regressed
(passing on base, failing on branch). See ``evaluate_regression_gate``.

What it does (spec §4.2-§4.4)
----------------------------
- Loads ``gates.toml``: a top-level ``never_touch`` fence (globs) plus a list
  of ``[[gate]]`` tables (``name``, ``ci_job``, ``when`` globs, ``check`` shell
  string, optional ``autofix``/``needs``/``needs_hint``/``ci_only``/``chdir``).
- Computes the changed set vs a base with
  ``git diff --name-only --diff-filter=ACMR <base>...HEAD`` (deletions
  excluded). If ``--base`` names a branch, the effective base is
  ``git merge-base HEAD <branch>``; otherwise it is used verbatim.
- Selects gates whose ``when`` globs match any changed file. Glob matching
  implements real ``**`` recursive semantics (stdlib ``fnmatch`` does not).
- Before running a gate, expands ``{base}`` -> the base ref/sha (verbatim) and
  ``{changed}`` -> the gate's matching changed files MINUS any ``never_touch``
  match, each ``shlex.quote``-d and space-joined. If the gate has ``chdir``
  set, ``{changed}`` is further rebased to paths relative to ``chdir`` (files
  outside ``chdir`` are excluded), and the command runs with that directory as
  cwd instead of the repo root.
- OPT-IN path-based test selection: a **command** gate (not a regression gate)
  may declare ``path_filters`` — a list of ``{ paths = ["<glob>", ...], select =
  "<test-path-or-arg>" }`` tables. When present, ``{selected}`` expands to the
  space-joined, shell-quoted union of ``select`` values from entries whose
  ``paths`` globs match any fence-filtered changed file (repo-relative). If no
  entry matches, ``select_default`` (optional string field) is used as the
  fallback; if that is also absent, the gate is reported as ``skip`` (verdict-
  neutral — treated the same as an early ``pass`` in the green computation).
  Gates without ``path_filters`` are completely unaffected.
- Reports each gate as one of three states ``pass`` | ``fail`` |
  ``could_not_run`` (never collapsing ``could_not_run`` into ``pass``), plus a
  coverage block of changed files matched by no gate. ``verdict`` is ``green``
  only if every applicable gate passed AND the coverage gap is empty.

Hardening flags (top-level report fields in parentheses)
----------------------------------------------------------
- ``--allow-meta-changes``: by default, if the changed set includes either
  canonical gates config location (``regenloop/gates.toml`` or
  ``.claude/gates.toml``, matched case-insensitively regardless of which one
  this run loaded), the verdict is forced to ``fail`` (``meta_config_changed``)
  so a diff cannot silently edit its own gate — this flag suppresses the
  forcing (the field is still reported).
- ``--forbid-fenced``: a non-empty ``coverage.fenced_changed_files`` forces
  the verdict to ``fail`` (``fence_violations``); without the flag this stays
  advisory, as before.
- ``--require-evidence``: when ``evidence.gates_executed`` is 0 an otherwise
  green run becomes ``verdict: "no_evidence"`` (exit 1). ``all()`` over an empty
  result list is ``True``, so an empty diff — or a diff whose every matching
  file is fenced — is green with nothing verified. That is correct pre-MR
  behaviour, so the guard is opt-in; enforcement callers (the pre-push hook,
  per-task gates) pass it. Escalation only: never turns a fail into a pass.
- ``--allow-default-branch`` / ``--forbid-default-branch``: ``--apply-autofix``
  on branch ``main``/``master`` is refused (exit 2) unless
  ``--allow-default-branch`` is passed; ``--forbid-default-branch`` opts plain
  check mode into the same refusal (using the same override flag).
- ``--gate NAME`` matching no configured gate is now a hard error (exit 2,
  lists available gate names) rather than a silent empty-gate report.
- ``--ledger PATH`` / ``--ledger-source STR``: after a check-mode report is
  built, append a ledger entry via ``ledger.append_entry``; a failure to append
  is a stderr warning only and never changes the verdict.
- Every report also carries ``head_sha`` (``git rev-parse HEAD`` at report
  time) so downstream tooling can verify a report matches its commit, and
  (when non-empty) ``config_warnings`` — a non-blocking sanity check that at
  least one gate's ``check`` references a recognized test/lint/build runner.
- ``--verify-tdd RED_JSON GREEN_JSON`` is a standalone mode (mutually
  exclusive with, and not requiring, ``--base``) that verifies RED/GREEN TDD
  evidence: exit 0 iff RED shows a failing new test and GREEN is clean against
  the same base, else exit 1 (reason on stdout) or exit 2 (unreadable files).

Exit codes: 0 = green, 1 = not green (fail / could_not_run / coverage gap),
2 = could not produce a report at all (bad config, not a git repo, unresolvable
base, refused default-branch autofix, unmatched ``--gate``, malformed
``--verify-tdd`` input). So the tool is usable directly as an objective gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import tomllib
from pathlib import Path

# ``venv_resolve`` is a sibling loose script (T1). The test harness loads
# ``gate_runner`` via ``importlib.util.spec_from_file_location`` (see
# ``tests/test_gate_runner.py``), which does NOT add ``scripts/`` to
# ``sys.path`` — so a bare ``import venv_resolve`` would raise
# ``ModuleNotFoundError`` under the tests even though it works under direct
# execution (``python3 scripts/gate_runner.py``, where Python auto-adds the
# script's own directory to ``sys.path[0]``). Insert the script's own directory
# first so the sibling import resolves identically in both contexts.
sys.path.insert(0, str(Path(__file__).resolve().parent))
# AR-3: bind this module under its import name when executed as a script
# (``python3 scripts/gate_runner.py`` → __name__ == "__main__"), so the carve
# module's ``import gate_runner`` back-reference resolves to THIS running
# module instead of re-executing the file as a second module object (split
# caches, patch-invisible state). No-op under normal import and under the
# test harness's load_module (registration precedes exec there).
sys.modules.setdefault("gate_runner", sys.modules[__name__])
import ledger  # noqa: E402  (sibling script; see venv_resolve note above)
import venv_resolve  # noqa: E402  (deliberately after the sys.path.insert above)

# Host for the ``docker-db`` precondition probe. There is no default PORT:
# every gate that declares ``needs = ["docker-db"]`` MUST set its own
# ``probe_port`` (enforced by ``validate_config`` below) — the right port is
# entirely repo-specific (whatever that repo's docker-compose maps), so a
# shared hardcoded fallback here previously baked one repo's port into the
# generic tool and silently misprobed every other repo that omitted the
# field. ``regenloop_doctor._check_probe_port`` reads the same per-gate value, so
# the two tools agree by construction.
DOCKER_DB_HOST = "localhost"
PROBE_TIMEOUT = 0.5  # seconds

# Default timeout (seconds) for internal git plumbing invocations (``_git``).
# Bounds a hung `git` (credential prompt, stalled network fetch) so it degrades
# to a clean could_not_run / GitError instead of blocking the run forever.
GIT_TIMEOUT = 300.0

# LEGACY alias (released as 1.1.0 on main): the per-command gate timeout env
# var was $REGENLOOP_GATE_TIMEOUT there; this branch's canonical knob is
# $REGENLOOP_GATE_TIMEOUT_S (richer semantics: 0/empty/malformed = unbounded
# escape hatch — see _default_check_timeout). The legacy var is still honored
# when the canonical one is unset, with its released tolerant semantics
# (malformed/non-positive warns and falls back to the default), so operators
# who set it before the merge keep their bound.
GATE_TIMEOUT_ENV = "REGENLOOP_GATE_TIMEOUT"

# How many output lines to retain per failing gate in the report.
MAX_ITEMS = 100

# JUnit XML size cap (spec §4B). Guards against oversized output files from a
# tooling error — NOT against billion-laughs (that payload is a small file whose
# expansion happens in memory, after this size check). The substantive defense
# against malicious XML is that the ``{junit}`` path is runner-controlled: the
# file is written by the gate's own (already-trusted) check command. Python
# 3.11+ bundles expat with built-in entity-expansion limits, which reduce but do
# not eliminate the billion-laughs risk; defusedxml (excluded by the zero-dep
# constraint) would add defense-in-depth if that constraint were relaxed.
JUNIT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB

# Regression gate names are used in cache filenames and JUnit paths, so they
# must be filesystem-safe (spec §4A rule 0).
_REGRESSION_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# The two locations gate_runner has ever loaded gates.toml from (see
# ``_resolve_gates_path``). The meta-config tamper fence checks a changed file
# against BOTH, case-insensitively, regardless of which one this run actually
# loaded — a diff that edits either path is gate-config tampering either way.
_META_CONFIG_PATHS = ("regenloop/gates.toml", ".claude/gates.toml")

# Gate statuses that mean the check actually ran and produced a verdict about
# the code. Everything else ("skip", "skipped_no_files") means no evidence was
# produced. Used for the report's ``evidence`` block so that "verified nothing"
# is never indistinguishable from "verified everything and passed".
_EXECUTED_STATUSES = ("pass", "fail", "could_not_run")

# Vacuous-check rejection (contract item 5): a gate whose entire check command
# is a shell no-op provides zero verification value and should be rejected at
# config load, not silently reported "pass" forever. Two shapes:
#   - bare "true" or ":" (the canonical POSIX no-op commands)
#   - a bare "echo ..." with no pipe/&&/; — i.e. not chained with real work.
# Deliberately narrow: a compound command like "echo start && pytest" contains
# "&&" so it does not match and remains valid.
_RE_VACUOUS_TRUE = re.compile(r"^\s*(true|:)\s*$")
_RE_VACUOUS_ECHO = re.compile(r"^\s*echo\b[^|&;]*$")


def _is_vacuous_check(check: str) -> bool:
    """True if ``check`` (the raw, uninterpolated command string) is a no-op."""
    return bool(_RE_VACUOUS_TRUE.match(check) or _RE_VACUOUS_ECHO.match(check))


# Runner-adequacy warning (contract item 6): pattern text for recognizing a
# gate's check command as invoking a known test/lint/build tool. Partially
# derived from scripts/coverage_gap.py's ``_RE_RUNNER`` (that module's own
# copy is narrower — it only needs enough runners to drive path-based test
# selection); this copy is intentionally broader since it exists purely to
# flag a gates.toml where NO gate looks like it runs anything real. Duplicated
# rather than imported: coverage_gap.py owns its pattern, and gate_runner has
# no import dependency on it.
_RE_KNOWN_RUNNER = re.compile(
    r"\b("
    r"pytest|python3?\s+-m\s+pytest|unittest|"
    r"jest|vitest|mocha|"
    r"tsc|eslint|biome|"
    r"ruff|mypy|pyright|flake8|"
    r"go\s+test|go\s+vet|"
    r"cargo\s+test|cargo\s+clippy|"
    r"node\s+--test|bun\s+test|bunx|"
    r"rspec|phpunit|"
    r"gradle|mvn|"
    r"make\s+test|sqlfluff"
    r")\b"
)


# --------------------------------------------------------------------------- #
# Glob matching with real ``**`` semantics
# --------------------------------------------------------------------------- #
_REGEX_CACHE: dict[str, re.Pattern[str]] = {}


def glob_to_regex(pattern: str) -> str:
    """Translate a path glob to an anchored regex body (no ^...$).

    Supports the patterns the spec requires (§4.2): ``**`` matches across path
    segments, a single ``*`` matches within one segment (never ``/``), and
    ``?`` matches one non-slash char. Rules:

    - ``**/``  -> ``(?:.*/)?``   (zero or more leading path segments)
    - ``/**``  (trailing) and bare ``**`` -> ``.*``
    - ``*``    -> ``[^/]*``
    - ``?``    -> ``[^/]``

    Note: ``[...]`` character-class syntax is NOT supported; brackets are
    treated as literals. The spec patterns do not require them.
    """
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":  # globstar '**'
                j = i + 2
                if j < n and pattern[j] == "/":
                    # '**/' -> zero or more directory segments
                    out.append("(?:.*/)?")
                    i = j + 1  # consume the trailing slash too
                else:
                    # trailing '**' (or '**' not followed by '/') -> anything
                    out.append(".*")
                    i = j
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "/":
            out.append("/")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


def _compiled(pattern: str) -> re.Pattern[str]:
    rx = _REGEX_CACHE.get(pattern)
    if rx is None:
        rx = re.compile(glob_to_regex(pattern))
        _REGEX_CACHE[pattern] = rx
    return rx


def glob_match(path: str, pattern: str) -> bool:
    """True if ``path`` matches the single glob ``pattern``."""
    return _compiled(pattern).fullmatch(path) is not None


def matches_any(path: str, patterns: list[str]) -> bool:
    """True if ``path`` matches any glob in ``patterns``."""
    return any(glob_match(path, p) for p in patterns or [])


# Case-insensitive glob matching — used ONLY for the ``never_touch`` fence.
# The fence must over-match, not under-match, on case-insensitive filesystems
# (macOS / Windows). ``when`` gate-selection stays case-sensitive because it
# mirrors exactly what git returns.
_REGEX_CACHE_NC: dict[str, re.Pattern[str]] = {}


def _compiled_nocase(pattern: str) -> re.Pattern[str]:
    rx = _REGEX_CACHE_NC.get(pattern)
    if rx is None:
        rx = re.compile(glob_to_regex(pattern), re.IGNORECASE)
        _REGEX_CACHE_NC[pattern] = rx
    return rx


def matches_any_nocase(path: str, patterns: list[str]) -> bool:
    """True if ``path`` matches any glob in ``patterns`` (case-insensitive).

    Used exclusively for the ``never_touch`` fence so that a file named
    ``Auth0_routes.py`` is still fenced by a pattern like
    ``SEO/backend/api/services/auth0_*``.
    """
    return any(_compiled_nocase(p).fullmatch(path) is not None for p in patterns or [])


def meta_config_changed(changed: list[str]) -> list[str]:
    """Repo-relative changed files that ARE a canonical gates.toml location.

    Checked case-insensitively against BOTH ``_META_CONFIG_PATHS`` regardless
    of which one this run actually loaded (highest-priority tamper fence —
    contract item 1): the gates config must not be silently editable by the
    same diff it is meant to gate.
    """
    canon = {p.lower() for p in _META_CONFIG_PATHS}
    return [f for f in changed if f.lower() in canon]


def uncommitted_files(cwd: str) -> list[str] | None:
    """Repo-relative paths with staged or unstaged modifications, or untracked.

    Returns ``None`` when git cannot answer, so callers can distinguish "clean"
    from "unknown" — the two must not be conflated by anything that decides
    whether it is safe to overwrite a file.
    """
    proc = _git(["status", "--porcelain"], cwd)
    if proc.returncode != 0:
        return None
    out: list[str] = []
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        # Renames report "old -> new"; the destination is what exists on disk.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        if path:
            out.append(path)
    return out


def config_identity(config_path: str | None, cwd: str) -> tuple[str | None, bool | None]:
    """Return ``(sha256 of the loaded config bytes, dirty-vs-HEAD)``.

    The runner obeys the *working-tree* config, but ``meta_config_changed`` only
    inspects the *committed* diff — so an uncommitted loosening (say
    ``never_touch = ["**/*"]``) governs the run while the tamper fence sees
    nothing. Recording both the identity of the bytes actually loaded and
    whether they diverge from HEAD makes that visible to every consumer.

    Dirtiness is decided with git's own content addressing (the committed blob
    sha vs ``hash-object`` of the working file) rather than by comparing decoded
    text, so a lenient decode cannot mask a real difference.

    ``dirty`` is ``None`` when it cannot be determined — no config path, the
    file is unreadable, it is not present at HEAD, or git is unavailable —
    because "unknown" and "clean" are different answers.
    """
    if not config_path:
        return None, None
    path = Path(config_path)
    try:
        raw = path.read_bytes()
    except OSError:
        return None, None
    sha = hashlib.sha256(raw).hexdigest()

    try:
        rel = os.path.relpath(str(path.resolve()), str(Path(cwd).resolve()))
    except (OSError, ValueError):
        return sha, None
    if rel.startswith(".."):
        return sha, None  # outside the repo; nothing at HEAD to compare against

    committed = _git(["rev-parse", f"HEAD:{rel}"], cwd)
    if committed.returncode != 0:
        return sha, None
    working = _git(["hash-object", "--", str(path)], cwd)
    if working.returncode != 0:
        return sha, None
    return sha, committed.stdout.strip() != working.stdout.strip()


def runner_adequacy_warnings(config: dict) -> list[str]:
    """Non-blocking sanity check: does ANY gate's check invoke a known runner?

    A gates.toml where zero gates mention a recognized test/lint/build tool
    (see ``_RE_KNOWN_RUNNER``) is not necessarily wrong, but is a common
    misconfiguration smell (e.g. every gate is a placeholder). Returns a list
    of warning strings — empty when at least one gate looks adequate, or when
    there are no gates at all (a different, unrelated problem). Never raises
    and never affects the verdict (contract item 6).
    """
    gates = config.get("gate", []) or []
    if not gates:
        return []
    for gate in gates:
        if _RE_KNOWN_RUNNER.search(str(gate.get("check") or "")):
            return []
    return [
        "no gate's check command references a recognized test/lint/build "
        "runner (pytest, jest, eslint, ruff, go test, cargo test, ...) — "
        "verify gates.toml is actually running something meaningful"
    ]


# --------------------------------------------------------------------------- #
# Git helpers
# --------------------------------------------------------------------------- #
class GitError(RuntimeError):
    """Raised when a required git invocation fails."""


def _git(
    args: list[str], cwd: str, timeout: float | None = GIT_TIMEOUT
) -> subprocess.CompletedProcess[str]:
    """Run a git plumbing command, decoding output leniently and bounding runtime.

    ``errors="replace"`` means a non-UTF-8 byte in git's output (e.g. from a
    submodule/hook with unusual encoding) degrades that one call's output
    instead of raising ``UnicodeDecodeError`` and crashing the whole process.

    A hung invocation (credential prompt, stalled network fetch) is converted
    to a synthetic failed ``CompletedProcess`` after ``timeout`` seconds rather
    than propagating ``subprocess.TimeoutExpired`` — every call site already
    treats a non-zero ``returncode`` as a clean git failure, so this reuses
    that existing handling instead of requiring each call site to also catch
    ``TimeoutExpired``.

    A missing git binary (``FileNotFoundError`` from ``subprocess.run``) is
    converted the same way, with returncode 127 ("command not found"), for the
    same reason — otherwise a git-absent environment would raise an uncaught
    exception through call sites that only expect a non-zero ``returncode`` (e.g.
    ``--verify-tdd``'s lineage probe, which must degrade to its lenient fallback
    rather than break the 0/1/2 exit contract).
    """
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=["git", *args],
            returncode=124,
            stdout="",
            stderr=f"git {' '.join(args)} timed out after {_fmt_seconds(timeout)}s",
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            args=["git", *args],
            returncode=127,
            stdout="",
            stderr="git executable not found on PATH",
        )


def repo_root(cwd: str) -> str:
    """Absolute path to the git work tree root containing ``cwd``."""
    proc = _git(["rev-parse", "--show-toplevel"], cwd)
    if proc.returncode != 0:
        raise GitError(f"not a git repository (or git unavailable): {proc.stderr.strip()}")
    return proc.stdout.strip()


_DEFAULT_BRANCHES = ("main", "master")


def current_branch(cwd: str) -> str:
    """The current branch name (``git rev-parse --abbrev-ref HEAD``).

    Returns ``"HEAD"`` (git's own sentinel) for a detached checkout — that
    value never matches ``_DEFAULT_BRANCHES``, so a detached HEAD is never
    treated as the default branch by the safety checks in ``main``.
    """
    proc = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    return proc.stdout.strip() or "HEAD"


def is_branch(ref: str, cwd: str) -> bool:
    """True if ``ref`` names a local or remote branch (vs a sha/tag/expr)."""
    for full in (f"refs/heads/{ref}", f"refs/remotes/{ref}"):
        if _git(["show-ref", "--verify", "--quiet", full], cwd).returncode == 0:
            return True
    return False


def resolve_base(base: str, cwd: str) -> str:
    """Resolve ``--base`` to the effective base revision.

    If ``base`` names a branch, the effective base is the merge-base of HEAD and
    that branch (so a feature branch is diffed against its fork point). Otherwise
    ``base`` is returned verbatim (it is already a concrete ref/sha).
    """
    if is_branch(base, cwd):
        proc = _git(["merge-base", "HEAD", base], cwd)
        if proc.returncode != 0:
            raise GitError(f"cannot compute merge-base of HEAD and {base!r}: {proc.stderr.strip()}")
        return proc.stdout.strip()
    # Verify the verbatim base actually resolves, so we fail clearly up front.
    proc = _git(["rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"], cwd)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise GitError(f"--base {base!r} does not resolve to a commit")
    return base


def changed_files(base: str, cwd: str) -> list[str]:
    """Files changed on HEAD vs ``base`` (added/copied/modified/renamed only).

    Uses ``-z`` (NUL-delimited) so paths with spaces or unusual characters are
    preserved verbatim. Paths are relative to the repository root.
    """
    proc = _git(
        ["diff", "--name-only", "-z", "--diff-filter=ACMR", f"{base}...HEAD"],
        cwd,
    )
    if proc.returncode != 0:
        raise GitError(f"git diff against {base!r} failed: {proc.stderr.strip()}")
    return [p for p in proc.stdout.split("\0") if p]


# --------------------------------------------------------------------------- #
# Preconditions (``needs``)
# --------------------------------------------------------------------------- #
def _tcp_open(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_need(need: str, probe_port: int | None = None) -> tuple[bool, str]:
    """Probe a single precondition. Returns ``(satisfied, reason_if_not)``.

    ``probe_port`` is the gate's configured ``probe_port`` field, the same
    value ``regenloop_doctor._check_probe_port`` checks, so the two tools agree by
    construction. There is no hardcoded fallback port: ``validate_config``
    rejects any ``needs = ["docker-db"]`` gate that omits ``probe_port`` at
    config-load time, so a ``None`` here means that guard was bypassed (e.g. a
    caller building a gate dict directly rather than through the normal load
    path) — reported as an unsatisfied precondition rather than guessed at.

    Unknown needs are treated as unsatisfied (``could_not_run``) rather than
    silently passing, matching the spec's anti-false-green stance.
    """
    if need == "docker-db":
        if probe_port is None:
            return False, "docker-db precondition missing 'probe_port' in gates.toml"
        if _tcp_open(DOCKER_DB_HOST, probe_port):
            return True, ""
        return False, f"docker-db unreachable :{probe_port}"
    return False, f"unknown precondition: {need!r}"


# --------------------------------------------------------------------------- #
# Interpolation + command execution
# --------------------------------------------------------------------------- #
def interpolate(template: str, base: str, files: list[str]) -> str:
    """Expand ``{base}`` and ``{changed}`` in a check/autofix template.

    Both ``{base}`` and ``{changed}`` are shell-quoted: ``{base}`` via
    ``shlex.quote`` (handles unusual reflog specs or any whitespace);
    ``{changed}`` via per-file ``shlex.quote``, space-joined, so the command
    is safe even for paths containing spaces or shell metacharacters.
    """
    quoted = " ".join(shlex.quote(f) for f in files)
    return template.replace("{base}", shlex.quote(base)).replace("{changed}", quoted)


def compute_selected(
    path_filters: list[dict],
    files: list[str],
) -> list[str]:
    """Return the union of ``select`` values whose ``paths`` globs matched.

    Iterates *path_filters* in order; for each entry, if any file in *files*
    matches any glob in the entry's ``paths``, the entry's ``select`` string is
    included in the result.  Duplicates are dropped while preserving first-match
    order so the result is deterministic.

    *files* should be the gate's repo-relative fence-filtered changed set (the
    same pool that ``{changed}`` draws from, before any ``chdir`` rebasing).
    ``select`` values are returned verbatim — shell-quoting is performed by the
    caller when building the ``{selected}`` expansion.
    """
    seen: set[str] = set()
    result: list[str] = []
    for entry in path_filters:
        paths = entry.get("paths") or []
        select = str(entry.get("select") or "").strip()
        if not select:
            continue
        if any(matches_any(f, paths) for f in files):
            if select not in seen:
                seen.add(select)
                result.append(select)
    return result


# Environment variables git EXPORTS when it invokes a hook (pre-push,
# pre-commit, …). Each one redirects git's notion of *which* repo, index, or
# worktree to operate on. A gate check runs arbitrary commands — a test suite,
# a linter, a build — that must act on the repo at ``cwd``, discovered the
# normal way, never on the hook's inherited context. If these leak into a
# check, any command inside it that runs plain ``git`` (e.g. a test fixture
# doing ``git commit`` with only ``cwd`` set) targets the REAL repository the
# hook fired in, not its own temp dir — observed as a downstream suite's
# ``init`` fixture commits landing on the branch being pushed and moving it off
# the developer's HEAD. Strip them from every check subprocess so a check can
# never mutate the outer repo through an inherited hook environment.
_HOOK_GIT_ENV_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_PREFIX",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_QUARANTINE_PATH",
    "GIT_QUARANTINE_ID",
)


def _check_subprocess_env() -> dict[str, str]:
    """The parent environment minus git's hook-injected repo-location vars,
    with ANSI color-forcing neutralized.

    See ``_HOOK_GIT_ENV_VARS``. A no-op when no hook set them (a direct
    ``/green-gate`` run), and the corruption fix when a hook did.

    Also strips ``FORCE_COLOR``/``CLICOLOR_FORCE`` and sets ``NO_COLOR=1``/
    ``PY_COLORS=0`` unconditionally: an ambient color-forcing var in the
    caller's shell would otherwise make a gate's subprocess (e.g. pytest)
    emit ANSI escapes that leak verbatim into the JSON report's ``items``.
    """
    env = os.environ.copy()
    for var in _HOOK_GIT_ENV_VARS:
        env.pop(var, None)
    env.pop("FORCE_COLOR", None)
    env.pop("CLICOLOR_FORCE", None)
    env["NO_COLOR"] = "1"
    env["PY_COLORS"] = "0"
    return env


# --------------------------------------------------------------------------- #
# Process-group spawn + teardown (UNCONDITIONAL — independent of --safe)
# --------------------------------------------------------------------------- #
# Windows has neither process groups in the POSIX sense nor SIGKILL; there the
# teardown degrades to the pre-existing child-only kill rather than failing.
_HAVE_PGROUP = hasattr(os, "killpg") and hasattr(signal, "SIGKILL")
_DEFAULT_KILL_GRACE_S = 10.0


def _gate_kill_grace_s() -> float:
    """SIGTERM -> SIGKILL grace, in seconds, for ``_run_subprocess_pgroup``.

    ``$REGENLOOP_GUARD_KILL_GRACE_S``; unset / malformed / non-positive /
    non-finite resolves to 10.0. Zero is rejected on purpose — an instantaneous
    "grace" is not a grace period (same escape-hatch-vs-default discipline as
    ``_default_check_timeout``).

    Deliberately INDEPENDENT of ``regenloop_guard.py``'s own resolver even
    though both read the same operator-facing name: the teardown below is a
    standalone correctness fix that must keep working when that module is
    absent, so it may not import it. The two defaults are pinned together by
    ``tests/test_pgroup_teardown_parity.py``, not by review discipline.
    """
    raw = os.environ.get("REGENLOOP_GUARD_KILL_GRACE_S")
    if raw is None:
        return _DEFAULT_KILL_GRACE_S
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_KILL_GRACE_S
    if not math.isfinite(value) or value <= 0:
        return _DEFAULT_KILL_GRACE_S
    return value


def _killpg_quiet(proc, sig: int) -> None:
    """Signal *proc*'s whole process group, best effort. Never raises.

    The ``poll() is None`` guard is the identity proof: POSIX cannot recycle
    the pid of a child that has not been reaped, so the group is provably still
    the one we created. Past that point the pid is fair game and must never be
    signalled.
    """
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, sig)
    except OSError:  # ProcessLookupError / PermissionError included
        pass


def _terminate_process_group(proc) -> None:
    """SIGTERM the group, wait out the grace period, then SIGKILL. Best effort."""
    if not _HAVE_PGROUP:
        try:
            proc.kill()
        except OSError:
            pass
        return
    _killpg_quiet(proc, signal.SIGTERM)
    deadline = time.monotonic() + _gate_kill_grace_s()
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.05)
    _killpg_quiet(proc, signal.SIGKILL)


def _run_subprocess_pgroup(args, *, shell: bool, cwd: str | None, env,
                           timeout: float | None) -> tuple[int, str]:
    """Run *args* in its OWN process group; tear the WHOLE group down on timeout.

    Returns ``(returncode, stdout + stderr)`` — stdout concatenated BEFORE
    stderr, the exact shape ``run_command`` has always returned. On timeout it
    raises ``subprocess.TimeoutExpired`` with the drained partial output
    attached, matching ``subprocess.run``'s own contract so no caller sees a
    behavioural change.

    Two properties are load-bearing, neither optional:

    * ``start_new_session=True`` makes the child its own group leader, so
      ``killpg`` reaches grandchildren. ``subprocess.run(timeout=)`` kills only
      the direct child, which provably leaves a backgrounded grandchild running
      on this host — the reproduced defect this closes.
    * ``communicate(timeout=)``, never ``wait(timeout=)``: a chatty command
      fills the OS pipe buffer (~64 KB) and deadlocks before the timeout can
      ever fire.

    UNCONDITIONAL: every caller gets this — checks, autofix, and the regression
    baseline/branch runs — with ``--safe`` on, off, or unset, and with no
    dependency on ``regenloop_guard.py`` existing.
    """
    proc = subprocess.Popen(
        args,
        shell=shell,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        start_new_session=_HAVE_PGROUP,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_process_group(proc)
        # Second communicate(): drains the pipes and reaps the child, so the
        # partial output is not lost and no zombie is left behind.
        stdout, stderr = proc.communicate()
        raise subprocess.TimeoutExpired(
            cmd=args, timeout=timeout, output=stdout, stderr=stderr,
        ) from None
    return proc.returncode, (stdout or "") + (stderr or "")


def run_command(argv, cwd: str, timeout: float | None,
                *, env_extra: dict[str, str] | None = None) -> tuple[int, str]:
    """Run *argv* and return ``(exit_code, combined_output)``.

    Foundational cross-platform execution primitive. *argv* is normally a
    ``list[str]`` executed via ``subprocess.run(shell=False)``: each element is
    handed to the OS as one literal argv word, so paths containing spaces or
    shell metacharacters are safe on macOS, Linux AND Windows with NO quoting.
    This removes the POSIX-only ``shlex.quote`` + ``shell=True`` blocker that
    broke single-quoted commands under Windows ``cmd.exe`` (where ``'a b'`` is
    three characters, not one quoted argument).

    A bare ``str`` is accepted for backward compatibility with unmigrated
    callers — the regression baseline install/run paths, the autofix path
    (deliberately left on the string form: parallel autofix is hazardous and
    out of scope for the foundational change), and the ``fake_run_command(cmd,
    cwd, timeout)`` test doubles. A string is run with ``shell=True`` unchanged
    so existing shell-style gates (those containing redirects/pipes/builtins,
    see ``_needs_shell``) behave byte-for-byte as before. New callers should
    pass a ``list[str]`` built by ``build_check_argv``.

    ``env_extra`` (D4) is an optional per-spawn env overlay merged OVER the
    ``_check_subprocess_env()`` base — the mechanism safe-mode pipelined
    regression anchors use to override ``PYTEST_XDIST_AUTO_NUM_WORKERS`` for
    their two spawns only, WITHOUT mutating the process-global ``os.environ``
    (which races the outer gate pool's concurrent command gates). Default
    ``None`` keeps every existing caller byte-identical.

    ``errors="replace"`` decodes non-UTF-8 bytes leniently (replacement
    character) instead of raising ``UnicodeDecodeError`` — a failing tool that
    echoes binary/non-UTF-8 output must degrade that one gate to
    ``could_not_run``/``fail``, not crash the entire run.

    The check runs with git's hook-injected repo-location env vars stripped
    (``_check_subprocess_env``) so it can never mutate the outer repo through an
    inherited hook context — the push-corruption fix.

    Raises ``subprocess.TimeoutExpired`` or ``OSError`` to the caller, which maps
    those tool-level failures to ``could_not_run``.

    Both paths spawn through ``_run_subprocess_pgroup``: same capture / text /
    ``errors="replace"`` semantics and the same stdout-then-stderr return shape
    as the ``subprocess.run`` calls it replaced, plus whole-process-group
    teardown on timeout. That teardown is unconditional — it is a correctness
    fix, not a ``--safe``-mode feature.
    """
    # Computed once here and threaded into BOTH branches so the shell-string
    # and argv paths carry the identical env (env_extra=None included).
    env = _check_subprocess_env()
    if env_extra:
        env.update(env_extra)
    if isinstance(argv, str):
        # Legacy shell-string path: preserved verbatim for shell-dependent gates
        # (redirects/pipes/builtins) and unmigrated callers. shell=True is the
        # ONLY way POSIX shell semantics survive; do not silently downgrade it.
        return _run_subprocess_pgroup(
            argv, shell=True, cwd=cwd, env=env, timeout=timeout
        )

    argv_list = _resolve_program0(list(argv))
    return _run_subprocess_pgroup(
        argv_list, shell=False, cwd=cwd, env=env, timeout=timeout
    )


# ``shlex.split`` POSIX mode is the common pragmatic cross-platform choice: it
# correctly strips single/double quoting around literals and is a faithful
# inverse of ``shlex.quote``. Its one Windows caveat is that a raw backslash in
# a *literal* is treated as an escape; gates should prefer forward-slash paths
# or the ``{python}``/``{junit}``/``{changed}`` tokens (slotted verbatim into
# the argv list by ``build_check_argv``, never re-split) over hand-written
# backslash paths. Kept as a constant so the policy is named in one place.
_SHLEX_POSIX = True


def _resolve_program0(argv: list[str]) -> list[str]:
    """Resolve a gate command's ``argv[0]`` to an executable found on PATH.

    Safety net for the two legacy ``gates.toml`` checks that spell the
    interpreter literally as ``python3`` (``python-test`` and ``validate``): on
    Windows ``python3`` is usually absent — the launcher is ``py -3`` or bare
    ``python`` — which under ``shell=False`` would raise / exit 127 and
    false-fail the gate. The preferred fix is for gates to use the ``{python}``
    token (resolved cross-platform, including ``Scripts/python.exe``, by
    ``venv_resolve``); this fallback keeps the legacy literal spellings runnable
    while that migration is pending.

    Only acts when the named program is genuinely NOT on PATH
    (``shutil.which`` returns ``None``), so a real ``python3`` on POSIX is left
    untouched and the no-op fast path is the common one.
    """
    if not argv:
        return argv
    prog = argv[0]
    if shutil.which(prog) is not None:
        return argv  # already runnable as-is — the common case
    if prog in ("python3", "python"):
        rest = argv[1:]
        if os.name == "nt":
            # Windows convention: the ``py`` launcher first, else ``python``.
            if shutil.which("py"):
                return ["py", "-3", *rest]
            if shutil.which("python"):
                return ["python", *rest]
        elif shutil.which("python"):
            # POSIX but ``python3`` missing (some macOS pyenv / minimal
            # distros): fall back to ``python`` — modern systems alias it to
            # python3, so this is safe.
            return ["python", *rest]
    return argv


def _carve_module():
    """The SOLE reference to the carved-out regression engine (AR-3 lazy seam).

    Kept to exactly one import statement in this file, pinned by
    ``TestCarveSingleLazySeamSourceScan``: a top-level import would make every
    command-gate-only repo pay for the regression engine at load time, which is
    the entire reason the carve exists. Returns the module so callers use
    attribute access — never a from-import — which is what keeps the suite's
    direct patches on THIS module (``gr.run_command = fn``) intercepting calls
    made from inside it.
    """
    import gate_runner_regression as _reg  # noqa: PLC0415 — lazy seam
    return _reg


# --------------------------------------------------------------------------- #
# --safe mode: the regenloop_guard admission-control seam
#
# Everything in this section is inert unless ``--safe``/``$REGENLOOP_SAFE`` is
# active. The process-group teardown above is deliberately NOT part of it.
# --------------------------------------------------------------------------- #
class _GuardRefusalError(OSError):
    """The guard declined to run a command (refused / internal error / no root).

    Subclasses ``OSError`` for defence in depth only — any future call site that
    forgets an explicit clause still fails closed as ``could_not_run`` instead
    of crashing the run. Every REAL call site must nonetheless catch it with its
    own ``except _GuardRefusalError`` ordered BEFORE any ``except OSError`` in
    the same try block: the ``OSError`` clause builds a ``"tool error running
    check"`` reason, which ``_cnr_is_transient`` treats as re-probable — so a
    refusal caught there would be re-queued at the back of the guard's FIFO,
    up to the max-wait ceiling, once per re-probe.

    The message carries NO ``"regenloop_guard: "`` self-prefix: the catching
    clause adds it, so it is never doubled.
    """


def _guard_module():
    """Lazily import ``regenloop_guard``; paid only when ``--safe`` is active.

    Mirrors ``_carve_module``'s lazy seam directly above. There is no
    top-level import on purpose: the unconditional
    process-group teardown must keep working with ``regenloop_guard.py`` absent
    entirely (an older or partial install).

    An import failure becomes a ``_GuardRefusalError``, never a bare
    ``ModuleNotFoundError``: that is an ``ImportError``, NOT an ``OSError``, so
    it would escape a ``ThreadPoolExecutor`` worker's future and crash the whole
    run instead of degrading that one gate to ``could_not_run``.
    """
    try:
        import regenloop_guard  # noqa: PLC0415 — lazy seam, --safe-only
    except ImportError as exc:
        raise _GuardRefusalError(f"regenloop_guard unavailable: {exc}") from exc
    return regenloop_guard


# Exit 1 is defensive: the current regenloop_guard never returns it (a
# guard-internal failure before spawn surfaces as a raised exception, not an
# rc), so that entry is forward-compatibility only. Kept rather than dropped so
# a future guard that does use the code degrades to could_not_run instead of
# being mistaken for a wrapped command's own exit 1.
_GUARD_REFUSAL_REASONS = {
    1: "guard-internal error before spawn",
    2: "admission refused: no turn within the max-wait ceiling",
    3: "guard root unusable (fail-closed; nothing was run)",
}


def _run_via_guard(run_target, cwd: str, timeout: float | None, *,
                   klass: str, label: str) -> tuple[int, str]:
    """Run a check/autofix command through the guard's admission queue.

    Same ``(exit_code, combined_output)`` return and the same
    ``subprocess.TimeoutExpired`` contract as ``run_command``, so callers map
    outcomes exactly as before. Guard exit codes 1/2/3 become
    ``_GuardRefusalError`` (the caller turns that into this gate's
    ``could_not_run``); 124 is the guard's own timeout kill and becomes
    ``TimeoutExpired``.

    A shell-string *run_target* is bridged to ``["/bin/sh", "-c", cmd]``: the
    guard always spawns ``shell=False``, and this preserves shell semantics
    without teaching it a second execution mode.
    """
    argv = ["/bin/sh", "-c", run_target] if isinstance(run_target, str) else run_target
    result = _guard_module().run(
        argv,
        root=None,
        klass=klass,
        weight_mb=None,
        policy="serial",
        timeout=timeout,
        # A TOTAL budget, not just a post-admission --timeout: the gate's own
        # clock starts when the CHECK starts, and the queue wait is part of
        # that. Without it, a queued gate sat behind a slow holder for up to
        # REGENLOOP_GUARD_MAX_WAIT_S (6h) and then reported could_not_run —
        # on a run whose documented ceiling is fifteen minutes. Passing the
        # gate timeout keeps the empty-queue case at the full budget (the
        # static split was correctly rejected on thread 6001d8bb0c4d); None
        # stays None, so an explicitly unbounded gate is not inventively
        # bounded here.
        total_budget_s=timeout,
        max_wait_s=None,
        kill_grace_s=None,
        label=label,
        degrade_open=False,
        cwd=cwd,
        env=_check_subprocess_env(),
        # A gate engine can find itself running INSIDE a guarded command, and
        # then it must not queue for the heavy lease its own parent holds.
        # Observed, not theorised: with `REGENLOOP_SAFE=1` exported — the
        # documented adoption path — a pre-push gate ran the suite under the
        # lease, that suite's own nested-gate test spawned this engine again,
        # the inner acquire joined the queue behind its parent, and the pair
        # sat deadlocked for 56 minutes with three tickets stacked behind them.
        # It would have waited out REGENLOOP_GUARD_MAX_WAIT_S (6h).
        #
        # `_check_subprocess_env()` copies os.environ verbatim, so
        # REGENLOOP_GUARD_ACTIVE reaches us whenever a parent guard set it —
        # which is exactly the condition that makes queueing unsatisfiable.
        # The guard's own CLI already armed this for the same reason; leaving
        # the in-process path unarmed left the hole open on the one caller that
        # can recurse into itself.
        nested_passthrough=True,
    )
    exit_code = result["exit_code"]

    # `spawned`/`timed_out` disambiguate what the number MEANS. Keying on the
    # number alone is wrong in both directions: 1/2/3 are the ordinary failure
    # codes of the wrapped command (pytest exits 1 on a test failure, ruff on
    # findings), so a red gate was being reported as could_not_run — an
    # infrastructure excuse for a real failure, and one that `_cnr_is_transient`
    # could then re-probe. 124 is likewise a legitimate rc for a command that
    # invokes `timeout` itself.
    #
    # `.get(..., default)` rather than `[...]`: the guard is loaded through a
    # lazy import seam, so an older sibling copy without these fields is
    # reachable. The defaults reproduce the previous number-only behaviour
    # exactly, which is the safe reading when the guard cannot say.
    spawned = result.get("spawned", exit_code not in _GUARD_REFUSAL_REASONS)
    timed_out = result.get("timed_out", exit_code == 124)

    if not spawned:
        reason = _GUARD_REFUSAL_REASONS.get(exit_code, "admission refused")
        raise _GuardRefusalError(
            f"{reason} (guard_status={result.get('guard_status')})"
        )
    if timed_out:
        raise subprocess.TimeoutExpired(
            cmd=run_target, timeout=timeout, output=result.get("output") or "",
        )
    return exit_code, result.get("output") or ""


def _run_regression_gate_via_guard(gate: dict, run_ctx, name: str) -> dict:
    """Hold ONE heavy lease across a whole regression-gate evaluation.

    The regression engine spawns several subprocesses (baseline install,
    baseline run, branch run) and lives in a sibling module this task does not
    edit, so admission control wraps the evaluation rather than each spawn —
    which still satisfies "acquire before the first baseline/branch spawn". The
    lease records no pgid (there is no single supervised child at this scope);
    the reaper handles a null-pgid lease safely, and each individual subprocess
    is still process-group-torn-down by ``run_command``.

    ``release`` is in a ``finally`` that begins immediately after the grant, so
    "granted implies released, promptly" holds even if evaluation raises.
    """
    def _refused(reason: str) -> dict:
        return {
            "name": name,
            "ci_job": gate.get("ci_job"),
            "status": "could_not_run",
            "reason": f"regenloop_guard: {reason}",
        }

    # ``_guard_module()`` converts an import failure into _GuardRefusalError;
    # catching it here (rather than relying on main() having already imported
    # the module successfully) keeps this call site consistent with every other
    # one. An escape from here would ride out of a ThreadPoolExecutor future
    # and crash the whole run instead of failing this one gate.
    try:
        guard = _guard_module()
    except _GuardRefusalError as exc:
        return _refused(f"{exc} — regression baseline/run not started")

    # The THIRD admission site, and the one that deadlocked for real. The other
    # two route through `run(nested_passthrough=True)`; this one calls
    # `acquire()` directly, so arming the flag on `run` left it untouched — a
    # pre-push gate held the heavy lease while running the suite, the suite's
    # nested-gate test invoked this engine on a fixture whose gate is
    # `kind = "regression"`, and evaluation queued behind its own parent for
    # 40 minutes with two tickets stacked before it was killed by hand.
    #
    # Already inside a guarded command, the parent's lease ALREADY accounts for
    # this work: the correct move is to evaluate under it, not to queue for a
    # second one that can never be granted.
    if guard.nested_under_guard():
        return _carve_module().evaluate_regression_gate(gate, run_ctx)

    try:
        outcome = guard.acquire(
            None, "heavy", None, "serial",
            # No single supervised child here, so there is no run to fold
            # into a total budget — the bound is the queue wait alone, set
            # to the gate's own timeout. Without it the evaluation queued
            # behind a slow holder for up to the 6h ceiling before reporting
            # could_not_run. None stays None (explicitly unbounded).
            getattr(run_ctx, "timeout", None) if run_ctx is not None else None,
            label=f"regression:{name}",
        )
    except _GuardRefusalError as exc:
        return _refused(f"{exc} — regression baseline/run not started")
    if outcome["outcome"] != "granted":
        return _refused(
            f"{outcome['outcome']} — regression baseline/run not started"
        )
    try:
        return _carve_module().evaluate_regression_gate(gate, run_ctx)
    finally:
        guard.release(None, outcome["ticket_id"], outcome["lease_lock_fh"])


def build_check_argv(
    template: str,
    *,
    base: str | None = None,
    files: list[str] | None = None,
    selected: list[str] | None = None,
    junit_path: str | None = None,
    python_path: "Path | str | None" = None,
) -> list[str]:
    """Tokenize a gate ``check`` template into a cross-platform argv list.

    This is the argv counterpart of the legacy string-based ``interpolate`` +
    ``_expand_junit`` + ``_expand_python`` resolvers. Because the result is run
    with ``subprocess.run(shell=False)`` via ``run_command``, NO ``shlex.quote``
    is ever produced: each list element is one literal argv word handed to the
    OS, so spaces and shell metacharacters in paths are safe on macOS, Linux,
    and Windows alike. The POSIX-only single-quote quoting that broke under
    Windows ``cmd.exe`` is eliminated by construction.

    Token slotting rules:

    - ``{changed}`` / ``{selected}`` (whole argv elements) expand to N
      elements — one per file / select value. They MUST appear as standalone
      argv words (``pytest {changed}``), which is how every real gate uses
      them; an attached form like ``--files={changed}`` is intentionally not
      supported (it could not map N files into one ``--files=`` value anyway).

    - ``{base}``, ``{junit}``, ``{python}`` may be standalone OR attached via
      ``=`` (e.g. ``--junitxml={junit}``): they are substituted in-place as a
      single argv word, since each is always a single path/sha.

    See ``_SHLEX_POSIX`` for the cross-platform tokenization policy and its
    Windows backslash caveat.
    """
    files = files or []
    parts = shlex.split(template, posix=_SHLEX_POSIX)
    argv: list[str] = []
    for part in parts:
        if part == "{changed}":
            argv.extend(files)
        elif part == "{selected}":
            argv.extend(selected or [])
        elif part == "{base}":
            argv.append(base if base is not None else part)
        else:
            if junit_path is not None and "{junit}" in part:
                part = part.replace("{junit}", junit_path)
            if python_path is not None and "{python}" in part:
                part = part.replace("{python}", str(python_path))
            if base is not None and "{base}" in part:
                part = part.replace("{base}", base)
            argv.append(part)
    return argv


# Conservative secret-value redaction applied to captured gate output before it
# is persisted into the JSON report (spec follow-up: failing tools that echo
# env vars/API keys must not leak them into a saved report). Two shapes:
#   - "KEY: value" / "KEY=value" for a recognized keyword (api_key, secret,
#     token, password/passwd, authorization) — the keyword is kept, only the
#     value token is masked.
#   - "bearer <value>" (no separator required) — the common
#     "Authorization: Bearer <token>" header shape, where the value follows a
#     bare space rather than ':'/'='.
# Deliberately narrow: only fires when one of these keywords is present, so it
# prefers false negatives (an unrecognized secret format slipping through)
# over false positives (mangling ordinary output that merely mentions one of
# these words with no attached value). Known gap, accepted: the leading ``\b``
# means a compound env-var name like ``DB_PASSWORD=x`` or ``AUTH_TOKEN=x``
# is NOT redacted (the '_' is a word character, so there is no boundary right
# before "password"/"token") — widening the match to catch that would also
# start matching ordinary non-secret diagnostics like
# ``mismatched_token: 5 vs 10``, replacing a plain count with a redaction
# marker, which is the mangling this is meant to avoid.
_SECRET_BEARER_RE = re.compile(r"(?i)\b(bearer)(\s+)(\S+)")
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|passw(?:or)?d|authorization)\b(\s*[:=]\s*)(\S+)"
)


def _redact_secrets(line: str) -> str:
    """Mask likely secret VALUES in one line of captured gate output."""
    line = _SECRET_BEARER_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}***REDACTED***", line)
    line = _SECRET_ASSIGN_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}***REDACTED***", line)
    return line


def _output_items(output: str) -> list[str]:
    lines = [ln for ln in output.splitlines() if ln.strip()]
    return [_redact_secrets(ln) for ln in lines[-MAX_ITEMS:]]


def fence_filtered(gate: dict, changed: list[str], never_touch: list[str]) -> list[str]:
    """The gate's matching changed files, minus the ``never_touch`` fence.

    Fence matching is case-insensitive (``matches_any_nocase``) so that
    mixed-case paths like ``Auth0_routes.py`` are blocked on macOS/Windows
    just as the lower-case form would be.

    Returns repo-relative paths. Use ``rebase_to_chdir`` afterwards if the
    gate has ``chdir`` set.
    """
    when = gate.get("when", [])
    return [
        f for f in changed
        if matches_any(f, when) and not matches_any_nocase(f, never_touch)
    ]


# --------------------------------------------------------------------------- #
# chdir support — run gates inside a monorepo sub-directory
# --------------------------------------------------------------------------- #
def _validate_chdir_str(chdir: str, gate_name: str) -> None:
    """Raise ``ValueError`` if ``chdir`` is absolute or contains path traversal.

    Called both in ``validate_config`` (early, at load time) and defensively
    in ``resolve_gate_cwd`` (at run time).
    """
    if Path(chdir).is_absolute():
        raise ValueError(
            f"gate {gate_name!r}: chdir {chdir!r} must be relative to the repo root"
        )
    if ".." in Path(chdir).parts:
        raise ValueError(
            f"gate {gate_name!r}: chdir {chdir!r} must not contain '..'"
        )


def resolve_gate_cwd(gate: dict, root: str) -> tuple[str, str | None]:
    """Return ``(effective_cwd, chdir_prefix)`` for running a gate's commands.

    ``effective_cwd`` is the absolute directory passed as ``cwd`` to
    ``subprocess``.  ``chdir_prefix`` is the normalized ``chdir`` value with a
    trailing ``/`` (e.g. ``"SEO/"``), used to rebase ``{changed}`` paths to
    sub-directory-relative; it is ``None`` when ``chdir`` is absent, empty, or
    ``"."`` (cwd = repo root, no rebasing).

    Raises ``ValueError`` for invalid ``chdir`` — caught by ``validate_config``
    at load time, but also present here as a runtime guard.
    """
    raw = str(gate.get("chdir") or "").strip().rstrip("/")
    if not raw or raw == ".":
        return root, None
    name = str(gate.get("name", "<unnamed>"))
    _validate_chdir_str(raw, name)

    # Lexical validation above only rejects an absolute path or a literal
    # ".." segment; it cannot catch a symlink INSIDE the repo that points
    # outside of it (e.g. chdir = "vendor" where repo/vendor is a symlink to
    # /etc). Resolve the joined path and require it stay under the resolved
    # repo root — both sides are resolved the same way, so this does not
    # false-reject a plain, non-symlinked sub-directory.
    effective = Path(root) / raw
    if not effective.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError(
            f"gate {name!r}: chdir {raw!r} resolves outside the repo root "
            f"(symlink escape not allowed)"
        )
    prefix = raw + "/"
    return str(effective), prefix


def rebase_to_chdir(files: list[str], prefix: str) -> list[str]:
    """Return the subset of (repo-relative) ``files`` that live under ``prefix``,
    with the prefix stripped so paths are relative to the ``chdir`` directory.

    ``prefix`` must end with ``/`` (e.g. ``"SEO/"``). Files not under the
    prefix are excluded — they cannot be addressed by a simple relative path
    inside that working directory.
    """
    return [f[len(prefix):] for f in files if f.startswith(prefix)]


# --------------------------------------------------------------------------- #
# Tool-error classification: a nonzero exit code that reflects a missing/
# misconfigured tool rather than a real check finding (GitLab loop-plugin#1).
#
# Without this, a bare `python`/`pytest`/`eslint` invocation with no interpreter
# on PATH, an empty test directory, or a missing lint config all collapse into
# the same generic `fail` as a real red test or a real lint violation — the
# gate then blocks every push forever, regardless of what changed, until a
# human notices and disables the gate outright (the failure mode this fixes).
#
# `could_not_run` already has this exact meaning for OTHER causes (see
# `_check_legacy_venv_create`, `{python}` resolution, `JUnitEmptyError` for
# regression-kind gates) — this generalizes it to the plain command-gate path,
# which had no such protection.
_RE_PYTEST_CMD = re.compile(r"\bpytest\b")
_RE_ESLINT_CMD = re.compile(r"\beslint\b")
_RE_DESIGN_LINT_CMD = re.compile(r"\bdesign_lint(?:\.py)?\b")

# Shell operators that ``subprocess.run(shell=False)`` cannot express — a raw
# redirect (``>``/``<``), pipe (``|``, ``||``, ``&&``), sequence (``;``),
# backtick, or command substitution ``$(``. ``_needs_shell`` uses this to route
# a gate's check to the legacy ``shell=True`` path so existing shell-style gates
# (``printf '%s' {changed} > captured.txt``) keep working, while plain commands
# (``python3 -m pytest tests/``) take the cross-platform argv path.
_RE_SHELL_OP = re.compile(r"&&|\|\||[<>;|`]|\$\(")


def _needs_shell(template: str) -> bool:
    """True if a check template needs a real shell rather than argv execution.

    Two reasons force ``shell=True``:

    1. Shell operators are present (redirect ``>``/``<``, pipe ``|``/``||``/
       ``&&``, sequence ``;``, backtick, ``$(``): ``subprocess.run(shell=False)``
       cannot express them.

    2. The command's first token is not resolvable to an executable on PATH
       (``shutil.which`` → ``None``): either a shell builtin with no
       executable form (``exit``, ``cd``, ``:``) or a genuinely missing tool.
       Routing these to ``shell=True`` preserves the current behavior for the
       many placeholder checks in the test-suite (``exit 0``) and yields exit
       127 — mapped to ``could_not_run`` by ``_classify_tool_error`` — for a
       missing tool, identical to before.

    A first token that IS a placeholder (``{python}``) or a real executable
    (``python3``, ``true``, ``eslint`` when installed) routes to the
    cross-platform argv path. ``{python}`` is exempted from the PATH check
    because ``venv_resolve`` substitutes a real interpreter path for it at run
    time.
    """
    if _RE_SHELL_OP.search(template):
        return True
    try:
        first = shlex.split(template, posix=_SHLEX_POSIX)[0]
    except (ValueError, IndexError):
        return True  # unparseable → safest to let the shell handle it
    if first.startswith("{"):
        return False  # {python} etc. resolves to a real executable; argv path
    if shutil.which(first) is None:
        return True
    return False


def _classify_tool_error(cmd: str, rc: int) -> str | None:
    """Return a ``could_not_run`` reason for ``rc``, or ``None`` to keep ``fail``.

    Conservative and exit-code-based, mirroring the existing regex-based
    guards in this module: only reclassifies exit codes that the shell or the
    tool itself documents as "didn't run the check" rather than "ran it and
    found a problem" (a real pytest/eslint failure is never touched).
    """
    # 126/127 are POSIX shell conventions ("found but not executable" /
    # "command not found") — universal across every tool, not tool-specific.
    if rc == 127:
        return "check exited 127 (command not found) — a required tool/interpreter is missing from PATH, not a real check failure"
    if rc == 126:
        return "check exited 126 (found but not executable) — a required tool is misconfigured, not a real check failure"

    if _RE_PYTEST_CMD.search(cmd):
        # pytest exit codes: 1 = tests failed (real signal, left as fail);
        # 2 = interrupted, 3 = internal error, 4 = usage error, 5 = no tests
        # collected — none of these mean "a test ran and failed".
        if rc == 5:
            return (
                "pytest exited 5 (no tests collected) — the test path/pattern "
                "matched nothing; check the target directory exists and is non-empty"
            )
        if rc in (2, 3, 4):
            return f"pytest exited {rc} (interrupted/internal/usage error), not a test failure"

    if _RE_ESLINT_CMD.search(cmd) and rc == 2:
        # ESLint exit codes: 1 = lint problems found (real signal, left as
        # fail); 2 = a configuration problem or internal error.
        return "eslint exited 2 (configuration problem or internal error), not a lint finding"

    if _RE_DESIGN_LINT_CMD.search(cmd) and rc == 2:
        # design_lint exit codes: 1 = violations found (real signal, left as
        # fail); 2 = config error (missing/invalid design-tokens file).
        return (
            "design_lint exited 2 (missing or invalid design-tokens config), "
            "not a lint finding — check regenloop/design/design-tokens.toml"
        )

    return None


# --------------------------------------------------------------------------- #
# pytest-xdist auto-parallelism (the single biggest win on I/O-bound suites)
# --------------------------------------------------------------------------- #
# Recognizes a pytest invocation at the point a check command is about to run.
# pytest must be the COMMAND (or an interpreter-module form ``<py> -m pytest``),
# never a bare argument to another program: ``echo pytest`` and
# ``make pytest-run`` are NOT pytest invocations. So the match is anchored to a
# command-start position — beginning of line or just after a shell separator
# (``;`` ``&`` ``|``, covering ``&&``/``||``) — and ``pytest`` is rejected when
# it is merely the prefix of a longer token (``pytest-run``) via the negative
# lookahead ``(?![\w-])``.
#
# Distinct name from the classifier's ``_RE_PYTEST_CMD`` (a loose ``\bpytest\b``
# used only to map an exit code to could_not_run): this one is stricter and is
# used solely to decide whether to *rewrite* the command. Keeping them separate
# prevents the injection regex's command-anchoring from accidentally tightening
# the classifier's behavior (which would re-classify real pytest calls as
# non-pytest and drop the rc==5 -> could_not_run mapping).
_RE_PYTEST_INVOCATION = re.compile(
    r"(?:^|[;&|]\s*)"        # command start: BOL or after ; & |
    r"(?:\S+\s+-m\s+)?"      # optional: <interpreter> -m  (\S+ => any path,
                             #   incl. homebrew python@3.14, conda, .venv/...)
    r"pytest(?![\w-])"       # pytest, not the stem of a longer word
)
# Explicit xdist disable (sequential intent — INV-4 keeps it sacred).
_RE_PYTEST_NO_XDIST = re.compile(r"-p\s+no:xdist")
# Already-parallelized / explicitly-disabled authors must not be re-injected.
# Accepts ``-n auto``, ``-n4``, ``--numprocesses=4``, ``--numprocesses 4``.
# The ``-p no:xdist`` alternative is _RE_PYTEST_NO_XDIST's pattern, derived
# (not duplicated) so the presence check and the explicit-disable check cannot
# drift apart.
_RE_PYTEST_PARALLEL_PRESENT = re.compile(
    r"(?:^|\s)(?:-n|--numprocesses)(?:\s|=|\d)|" + _RE_PYTEST_NO_XDIST.pattern
)
# A sequencing operator AFTER the pytest invocation: appending at end-of-string
# would land "-n {cap}" on the DOWNSTREAM command (tee/echo), erroring there and
# leaving the fan-out uncapped. Derived from the carve module's
# ``_RE_SHELL_SEQ_AFTER`` (B7's vitest guard), extended with ``\n`` — a literal
# newline is a shell command separator for the same reason ``;`` is. Shell
# REDIRECTION (``>``, ``2>&1``, ``<``) is deliberately NOT matched: it is
# parsed out-of-band and position-independent, so ``pytest … > out.log`` +
# append still hands the flag to pytest. The ``&`` alternative matches a
# backgrounding ``&`` with NO whitespace required on either side (round 1
# C-F1's glued ``… &echo``; round 2 R2-F1's glued-to-preceding-token
# ``…tests/api& echo`` — a shell control operator needs no spacing at all).
# BOTH lookarounds are load-bearing: ``(?<!>)`` keeps ``>&``-redirect forms
# (``2>&1``) un-matched, and ``(?!>)`` keeps bash's redirect-BOTH
# (``&> log``, ``&>> log``) un-matched — those are redirection, not
# sequencing, and must stay clampable.
_RE_SHELL_SEQ_AFTER = re.compile(r"\s*(?:\|\||&&|[|;\n])|(?<!>)&(?!>)")
# The interpreter token immediately preceding "-m pytest" (e.g. "python3",
# ".venv/bin/python", "/opt/.../python@3.14/bin/python3.14"). Used to probe
# xdist with the SAME interpreter the gate will actually invoke. \S+ keeps it
# robust to arbitrary install paths (homebrew @-versioned dirs, conda, etc.).
_RE_PYTEST_INTERP = re.compile(r"(\S+)\s+-m\s+pytest(?![\w-])")

# Python runner wrappers: ``uv run pytest …``, ``poetry run python -m pytest …``.
# Neither _RE_PYTEST_INVOCATION nor the argv detector sees these — argv[0] is the
# wrapper, not pytest — so the worker cap silently never fired for them.
#
# Recognised ONLY behind an explicit ``allow_runner_wrappers`` opt-in, which
# only safe mode passes. Reason: ``_pytest_parallel_jobs()`` DEFAULTS to
# ``"auto"``, so matching these unconditionally would start appending
# ``-n auto`` to every repo whose gate is ``uv run pytest`` and would silently
# override a worker count declared in that repo's ``addopts`` — which guard 2
# cannot see, because it inspects the command line only. Safe mode is an
# explicit operator request for a specific, low worker count, so there the
# override is the intent rather than a surprise.
_PYTEST_RUNNER_WRAPPERS = ("uv", "poetry", "pdm", "rye", "hatch", "pipenv")
_RE_PYTEST_RUNNER_WRAPPER = re.compile(
    r"(?:^|[;&|]\s*)"                                     # command start
    r"((?:\S*/)?(?:" + "|".join(_PYTEST_RUNNER_WRAPPERS) + r")(?:\.exe)?)"
    r"\s+run\s+"                                          # the wrapper's `run`
    r"(?:(\S+)\s+-m\s+)?"                                # optional <interp> -m
    r"pytest(?![\w-])"                                    # not a longer word
)


def _runner_wrapper_name(token: str) -> str | None:
    """``"/usr/bin/uv"`` / ``"uv.exe"`` -> ``"uv"``; None if not an allowlisted wrapper."""
    name = os.path.basename(token)
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name if name in _PYTEST_RUNNER_WRAPPERS else None


def _pytest_wrapper_probe_argv(argv: list[str]) -> list[str] | None:
    """xdist-probe prefix if *argv* is ``<runner> run [<interp> -m] pytest …``.

    Returns ``None`` when *argv* is not a recognised wrapper invocation, which
    is how the caller decides whether the wrapper branch applies at all.
    """
    if len(argv) < 3 or argv[1] != "run" or _runner_wrapper_name(argv[0]) is None:
        return None
    rest = argv[2:]
    if rest[0] == "pytest":
        return [argv[0], "run", "python"]
    if len(rest) >= 3 and rest[1] == "-m" and rest[2] == "pytest":
        return [argv[0], "run", rest[0]]
    return None


# Launcher-keyed cache: {" ".join(prefix): importable?}. Probed once per run per
# launcher (a regression branch-run + a command gate sharing one venv share a
# single probe). A single-element prefix keys on the bare exe exactly as the
# original interpreter-keyed cache did.
_xdist_importable_cache: dict[str, bool] = {}


def _pytest_xdist_importable_argv(prefix: list[str]) -> bool:
    """True iff ``[*prefix, "-c", "import xdist"]`` succeeds (memoized).

    pytest-xdist is the sole source of pytest's ``-n`` flag. Without it,
    pytest rejects ``-n auto`` as ``unrecognized arguments`` and exits 4 —
    which would silently turn a passing suite into a spurious ``fail``. So
    ``-n`` injection is strictly gated on this probe: a missing dep must
    degrade to "ran sequentially" (still correct), never to a false red.

    *prefix* is the launcher that will actually run the tests, so a per-venv
    install decision is honored: ``["python3"]`` or ``[".venv/bin/python"]`` for
    a direct invocation, and ``["uv", "run", "python"]`` for a runner-wrapper
    command — probing a bare ``python3`` there would consult an entirely
    different interpreter from the one pytest ends up in. ``timeout`` bounds a
    wedged interpreter; any failure is cached as "not importable" so the gate
    proceeds sequentially without retrying on every gate.
    """
    key = " ".join(prefix)
    if key in _xdist_importable_cache:
        return _xdist_importable_cache[key]
    try:
        proc = subprocess.run(
            [*prefix, "-c", "import xdist"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
        ok = proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    _xdist_importable_cache[key] = ok
    return ok


def _pytest_parallel_jobs() -> str | None:
    """Resolve the requested pytest worker spec, or None to leave cmd alone.

    Priority: ``$REGENLOOP_PYTEST_JOBS`` → default ``"auto"`` (parallelism ON
    by default whenever xdist is importable). Accepted values:
      ``auto`` | ``logical`` | ``count``  -> ``-n auto``  (one worker per core)
      a positive integer (``4``)          -> ``-n 4``     (cap the fan-out)
      ``off`` | ``none`` | ``0`` | ``""`` -> None         (run sequentially)
    An unrecognized value is treated as None (safe: never injects a bad flag).
    The ``--pytest-parallel`` CLI flag feeds this same resolver by setting the
    env var in ``main()`` (so both knobs share one code path and any in-process
    caller of ``evaluate_gate`` is covered, not just the CLI entrypoint).
    """
    raw = (os.environ.get("REGENLOOP_PYTEST_JOBS") or "auto").strip().lower()
    if raw in ("", "off", "none", "0", "false"):
        return None
    if raw in ("auto", "logical", "count"):
        return "auto"
    # isdecimal, not isdigit: '²'.isdigit() is True but int('²') raises — a
    # hostile/exotic value must degrade to sequential, never crash the run
    # (single parse; t2's guard commit set the isdecimal precedent).
    if raw.isdecimal() and int(raw) >= 1:
        return raw
    return None


# -- safe mode: the resource-derived pytest worker ceiling ------------------- #
_SAFE_PYTEST_CAP_FALLBACK = 2   # legacy --safe default when the guard cannot size (D1)


def _safe_pytest_cap() -> int:
    """Resource-derived pytest worker ceiling (INV-2: sized by regenloop_guard, never here).

    Called only under ``--safe`` (the ``evaluate_gate`` call sites pass
    ``cap=None`` otherwise, keeping non-safe output byte-identical). Resolved
    through the same lazy ``_guard_module()`` seam every other guard consumer
    uses; any failure — a refused import, an older sibling copy without
    ``plan_worker_cap`` — degrades to the legacy flat default instead of
    killing the run.
    """
    try:
        return int(_guard_module().plan_worker_cap()["cap"])
    except Exception:  # noqa: BLE001 — sizing must never kill the gate run
        return _SAFE_PYTEST_CAP_FALLBACK


_RE_PYTEST_WORKER_SPEC = re.compile(
    r"(?:^|\s)(?:-n(?:\s+|=|(?=\d))(\S+)|--numprocesses(?:\s+|=)(\S+))"
)


def _classify_worker_spec(value: str) -> tuple[str, str]:
    """Classify one ``-n``/``--numprocesses`` value: ``num`` | ``auto`` | ``seq``.

    ``off``/``0``/``none``/``false`` are sequential intent (INV-4 — never
    touched); a positive integer is clampable; anything else (``auto``,
    ``logical``, malformed) is left alone — the env var is the net there, and
    an unrecognized spec must never be text-surgered into a bad flag.
    """
    low = value.strip().lower()
    if low in ("off", "none", "0", "false"):
        return ("seq", value)
    if low.isdecimal():
        # isdecimal, not isdigit: '²' passes isdigit but int('²') raises, and
        # the clamp compares int(spec[1]) — a hostile spec must land in the
        # untouched bucket below, never a ValueError out of evaluate_gate.
        return ("num", low)
    return ("auto", value)


def _explicit_worker_spec(cmd: str) -> tuple[str, str] | None:
    """LAST ``-n``/``--numprocesses`` spec in *cmd*, classified.

    Returns ``("num", "4")`` | ``("auto", "auto")`` | ``("seq", "0")``, or None
    when the command declares no worker spec. The LAST match wins because
    argparse does (AC-1.4a — pytest parses repeated ``-n`` last-wins, which is
    also what makes the clamp's trailing append authoritative).
    """
    last: str | None = None
    for m in _RE_PYTEST_WORKER_SPEC.finditer(cmd):
        last = m.group(1) or m.group(2)
    if last is None:
        return None
    return _classify_worker_spec(last)


def _p_no_xdist(cmd: str) -> bool:
    return bool(_RE_PYTEST_NO_XDIST.search(cmd))


def _explicit_worker_spec_argv(argv: list[str]) -> tuple[str, str] | None:
    """Token-scanning twin of ``_explicit_worker_spec`` for the argv path."""
    last: str | None = None
    for i, tok in enumerate(argv):
        value: str | None = None
        if tok == "-n" and i + 1 < len(argv):
            value = argv[i + 1]
        elif len(tok) > 2 and tok[:2] == "-n" and (tok[2].isdigit() or tok[2] == "="):
            value = tok[2:].lstrip("=")
        elif tok.startswith("--numprocesses"):
            if "=" in tok:
                value = tok.partition("=")[2]
            elif i + 1 < len(argv):
                value = argv[i + 1]
        if value:
            last = value
    if last is None:
        return None
    return _classify_worker_spec(last)


def _pytest_parallel_present_argv(argv: list[str]) -> bool:
    """Guard-2 presence check: the token twin of ``_RE_PYTEST_PARALLEL_PRESENT``.

    Mirrors the pre-cap scan exactly (standalone ``-n``, attached ``-n4`` /
    ``-n=…``, ``--numprocesses[=…]``, ``-p no:xdist``) so ``cap=None`` keeps
    the argv path byte-identical.
    """
    for i, tok in enumerate(argv):
        if tok == "-n":
            return True
        if len(tok) > 2 and tok[:2] == "-n" and (tok[2].isdigit() or tok[2] == "="):
            return True
        if tok.startswith("--numprocesses"):
            return True
        if tok == "-p" and i + 1 < len(argv) and argv[i + 1] == "no:xdist":
            return True
    return False


def _p_no_xdist_argv(argv: list[str]) -> bool:
    return any(
        tok == "-p" and i + 1 < len(argv) and argv[i + 1] == "no:xdist"
        for i, tok in enumerate(argv)
    )


def _apply_pytest_parallelism(cmd: str, *,
                              allow_runner_wrappers: bool = False,
                              cap: int | None = None) -> str:
    """Append ``-n <jobs>`` to a pytest check command when safe; else no-op.

    Called once per check, in ``evaluate_gate``, AFTER every token (``{base}``,
    ``{changed}``, ``{selected}``, ``{python}``) is expanded and BEFORE
    ``run_command`` — so the decision sees the literal command that will run,
    including the real interpreter path, and the injected ``-n`` reaches the
    single shell-execution chokepoint unchanged.

    Five independent guards, any one of which makes this a passthrough:
      1. Not a pytest command (``_RE_PYTEST_CMD``).
      2. The author already set ``-n``/``--numprocesses`` or disabled xdist
         via ``-p no:xdist`` (respect explicit intent; idempotency).
      3. Mode resolved to None (``REGENLOOP_PYTEST_JOBS=off``).
      4. We are ourselves running inside a pytest-xdist worker
         (``$PYTEST_XDIST_WORKER``): an outer ``pytest -n auto`` is already
         driving the suite — nesting ``-n auto`` here would fan out to
         N×N workers and oversubscribe. (xdist sets PYTEST_XDIST_WORKER in
         every worker process; verified in xdist/remote.py.)
      5. pytest-xdist is not importable by this gate's interpreter: a missing
         dep must never convert a pass into ``unrecognized arguments -n``.

    ``allow_runner_wrappers`` (safe mode only — see ``_RE_PYTEST_RUNNER_WRAPPER``
    for why it is opt-in) additionally recognises ``uv run pytest …``-style
    launcher commands, which guard 1 otherwise treats as non-pytest. Default
    ``False`` keeps every existing caller byte-identical.

    ``cap`` (safe mode only — ``evaluate_gate`` passes ``_safe_pytest_cap()``)
    is the resource-derived worker ceiling. Under a cap the guard-2 passthrough
    becomes a CLAMP DECISION that runs BEFORE guard 3, so
    ``REGENLOOP_PYTEST_JOBS=off`` plus a gate command ``pytest -n 14`` is still
    clamped (INV-3 — cap ALL ``-n`` forms): an explicit numeric spec above the
    cap gets `` -n {cap}`` appended (argparse last-wins, AC-1.4a); at/below the
    cap, ``-n auto``, ``0``/``off``, and ``-p no:xdist`` are untouched (OQ-7,
    INV-4). A COMPOUND command (a sequencing operator — ``|``/``&&``/``;``/
    ``&``/newline — after the invocation, ``_RE_SHELL_SEQ_AFTER``) skips the
    append, clamp AND injection alike: either would land on the downstream
    segment; redirection-only shapes still clamp. (Non-safe injection on
    compounds keeps its pre-existing behavior — INV-5.) A ``#`` anywhere in
    the command skips both appends the same way: it MAY open a /bin/sh
    comment (word-initial ``#``; mid-word is an ordinary token) and the
    append could land inside it — refusal covers both, fail-closed, and the
    env var still caps ``-n auto`` forms. (Non-safe injection into a comment
    keeps its pre-existing behavior — INV-5.) With no
    explicit spec, the cap also becomes the injected default and clamps a
    numeric ``$REGENLOOP_PYTEST_JOBS`` request (an ``auto`` request keeps its
    ``-n auto`` text — ``PYTEST_XDIST_AUTO_NUM_WORKERS`` caps it). Default
    ``None`` keeps every non-safe output byte-identical (INV-5).

    Returns the (possibly rewritten) command string. Emits nothing to stdout —
    the injected flag is itself the audit trail visible in the report's emitted
    command, so gate output stays clean for JSON parsing.
    """
    # Guard 1: is this a pytest command (directly, or through a wrapper)?
    # The invocation match is recorded so the compound guard can look for
    # sequencing operators AFTER the pytest invocation specifically, and so
    # the spec scan below can start AT the invocation.
    wrapper = None
    invocation = _RE_PYTEST_INVOCATION.search(cmd)
    if not invocation:
        if not allow_runner_wrappers:
            return cmd
        wrapper = _RE_PYTEST_RUNNER_WRAPPER.search(cmd)
        if wrapper is None:
            return cmd
        inv_end = wrapper.end()
    else:
        inv_end = invocation.end()
    # Compound guard (safe mode only): a sequencing operator AFTER the pytest
    # invocation means ANY end-of-string append — clamp or injection — would
    # land "-n …" on the DOWNSTREAM command (tee/echo), erroring there and
    # leaving the fan-out uncapped. Searched only under a cap so the non-safe
    # path stays byte-identical (INV-5; the non-safe compound-INJECTION defect
    # is pre-existing and deliberately left as-is for the residuals to own).
    seq_after = _RE_SHELL_SEQ_AFTER.search(cmd[inv_end:]) if cap is not None else None
    # Comment guard (S-F1/R2-F3), hoisted ABOVE the branch split: a "#" in
    # the command MAY open a /bin/sh comment (word-initial "#"; a mid-word
    # "#" is an ordinary token) — either way the append could land inside it,
    # so under a cap BOTH appends, clamp and injection, refuse fail-closed
    # (the env var still caps "-n auto" forms). The cap gate preserves INV-5
    # byte-identity: non-safe injection-into-comment stays the pre-existing
    # residual.
    if cap is not None and "#" in cmd:
        return cmd
    # Guard 2′: the author's own worker spec. Non-safe stays a passthrough
    # (byte-identical, INV-5); under a cap it becomes the CLAMP DECISION —
    # deliberately ahead of guard 3 so a jobs=off request cannot smuggle an
    # over-cap explicit -n through (INV-3).
    clamp: str | None = None
    if _RE_PYTEST_PARALLEL_PRESENT.search(cmd):
        if cap is None:
            return cmd
        if _p_no_xdist(cmd):
            return cmd                                  # INV-4: sequential intent
        # Segment-scoped spec: the invocation's OWN segment only (anchor
        # parity — the carve module's _apply_pytest_anchor_clamp). An -n
        # belonging to a tool BEFORE the pytest invocation is not pytest's
        # spec; clamping on it would inject -n into a pytest that declared
        # none (the transform-surface D7 violation class).
        spec = _explicit_worker_spec(cmd[(wrapper or invocation).start():])
        if spec is None or spec[0] in ("seq", "auto"):
            return cmd          # no classifiable spec / sequential / -n auto: env var's job
        if int(spec[1]) <= cap:
            return cmd                  # fits: the author's own value stands (OQ-7)
        if seq_after is not None:
            return cmd          # COMPOUND: skip the TEXT clamp; the env var still
                            # caps -n auto forms; explicit numeric -n in a compound
                            # is a disclosed residual (crit. finding 1).
        clamp = f" -n {cap}"                            # argparse last-wins (AC-1.4a)
    elif seq_after is not None:
        return cmd      # COMPOUND with no explicit spec: the INJECTION append
                        # would land on the downstream segment just the same.
    # Guard 3: jobs resolution. The CLAMP survives jobs=off (that is the
    # reorder's point); jobs is only ever consumed on the injection path.
    jobs: str | None = None
    if clamp is None:
        jobs = _pytest_parallel_jobs()
        if jobs is None:
            return cmd
        if cap is not None:
            # D2: distinguish "unset" from "=auto" — _pytest_parallel_jobs()
            # maps both to "auto", but under a cap unset means "inject the cap"
            # while an explicit auto request keeps its -n auto text.
            raw = (os.environ.get("REGENLOOP_PYTEST_JOBS") or "").strip()
            if raw == "":
                jobs = str(cap)                 # unset -> effective = cap (AC-1.1/3.1)
            elif jobs.isdecimal():
                jobs = str(min(int(jobs), cap))  # numeric request clamped (AC-3.2)
            # explicit auto/logical/count: text stays "-n auto"; env var caps it
    # Guard 4: already inside an xdist worker (prevent N×N oversubscription)?
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return cmd
    # Guard 5: is xdist importable by this gate's interpreter (through the
    # wrapper, when there is one)? The APPEND itself — clamp or injection —
    # lands only past this probe: an xdist-less repo must never see a new -n.
    if wrapper is not None:
        # Probe THROUGH the wrapper: it, not this process, picks the env.
        probe = [wrapper.group(1), "run", wrapper.group(2) or "python"]
    else:
        interp_match = _RE_PYTEST_INTERP.search(cmd)
        probe = [interp_match.group(1) if interp_match else "python3"]
    if not _pytest_xdist_importable_argv(probe):
        return cmd
    if clamp is not None:
        return cmd + clamp
    return f"{cmd} -n {jobs}"


def _apply_pytest_parallelism_argv(argv: list[str], *,
                                   allow_runner_wrappers: bool = False,
                                   cap: int | None = None) -> list[str]:
    """List-aware counterpart of ``_apply_pytest_parallelism`` for the argv path.

    After STAGE 1 (the cross-platform argv execution primitive), a check
    WITHOUT shell operators runs as ``list[str]`` via
    ``subprocess.run(shell=False)`` — never a string. This function applies
    the SAME five-guard gating as the string transform but operates on the
    token list directly: when every guard passes, it returns
    ``argv + ["-n", jobs]``; otherwise it returns *argv* unchanged.

    pytest detection on a list (the guard-1 counterpart of
    ``_RE_PYTEST_INVOCATION`` on a string):

    - ``argv[0] == "pytest"`` — the bare ``pytest`` command form.
    - ``argv[i] == "-m"`` and ``argv[i + 1] == "pytest"`` for some ``i`` —
      the ``<interpreter> -m pytest`` module form; the interpreter is then
      ``argv[0]``. The scan starts at ``i == 0`` so ``<interp> -m pytest``
      is recognized, and a mere ``-m pytest`` argument to a non-python
      program (e.g. ``["make", "-m", "pytest"]``) is NOT misdetected because
      ``argv[0]`` (``make``) would fail the interpreter probe rather than
      match the pytest-detection branch — wait, that IS a false positive on
      detection alone. But the practical risk is nil: ``make -m pytest`` is
      not a real invocation shape, and even if it were, the xdist probe would
      run ``make -c "import xdist"`` (fails) → no injection. Safe.

    Existing-parallelism detection (guard 2): ``_pytest_parallel_present_argv``
    scans tokens for ``-n`` (standalone, value in next token), ``-n<N>`` /
    ``-n=<spec>`` (attached), ``--numprocesses[=N]``, or ``-p no:xdist``.
    Semantically identical to ``_RE_PYTEST_PARALLEL_PRESENT`` on the string
    side.

    The interpreter for the xdist importability probe (guard 5) is ``argv[0]``
    when the ``-m pytest`` form matched (the interpreter that will actually
    run), and ``"python3"`` for the bare ``pytest`` form (matching the string
    variant's default — pytest's own shebang resolves to some python3).

    ``allow_runner_wrappers`` (safe mode only) additionally recognises
    ``["uv", "run", "pytest", …]``-style launcher argv — the shape this repo's
    own ``python-test`` gate uses, and the one guard 1 silently missed. It is
    checked FIRST so ``["uv", "run", "python", "-m", "pytest"]`` probes
    ``uv run python`` rather than falling into the ``-m`` branch and probing the
    wrapper binary itself. Default ``False`` keeps existing callers unchanged.

    ``cap`` (safe mode only) applies the SAME clamp semantics as the string
    transform (see ``_apply_pytest_parallelism``): the guard-2 passthrough
    becomes a clamp decision ahead of guard 3, an explicit numeric spec above
    the cap gets ``["-n", str(cap)]`` appended, sequential/auto forms are
    untouched, and without an explicit spec the cap drives the injection
    default. No compound guard argv-side: ``_needs_shell`` routes any command
    with sequencing operators to the string path, so an argv list never
    carries them. Default ``None`` keeps every non-safe output byte-identical
    (INV-5).
    """
    # Guard 1: is this a pytest command (directly, or through a wrapper)?
    # spec_start records WHERE the invocation begins so the guard-2 spec scan
    # can be segment-scoped (the argv twin of the string transform's
    # cmd[invocation.start():] scoping): an -n in the interpreter/wrapper
    # region BEFORE the pytest token is not pytest's spec.
    probe: list[str] | None = None
    spec_start = 0
    if allow_runner_wrappers:
        probe = _pytest_wrapper_probe_argv(argv)
        if probe is not None:
            spec_start = 2  # argv[0:2] is the wrapper's own "<runner> run"
    if probe is None:
        is_pytest = False
        interp = "python3"
        if argv:
            if argv[0] == "pytest":
                is_pytest = True
            else:
                for i in range(len(argv) - 1):
                    if argv[i] == "-m" and argv[i + 1] == "pytest":
                        is_pytest = True
                        interp = argv[0]
                        spec_start = i  # skip interpreter-region flags
                        break
        if not is_pytest:
            return argv
        probe = [interp]

    # Guard 2′: the author's own worker spec — passthrough non-safe
    # (byte-identical), clamp decision under a cap (before guard 3, INV-3).
    clamp: list[str] | None = None
    if _pytest_parallel_present_argv(argv):
        if cap is None:
            return argv
        if _p_no_xdist_argv(argv):
            return argv                                 # INV-4: sequential intent
        spec = _explicit_worker_spec_argv(argv[spec_start:])
        if spec is None or spec[0] in ("seq", "auto"):
            return argv         # no classifiable spec / sequential / -n auto: env var's job
        if int(spec[1]) <= cap:
            return argv                 # fits: the author's own value stands (OQ-7)
        clamp = ["-n", str(cap)]                        # argparse last-wins (AC-1.4a)

    # Guard 3: jobs resolution. The CLAMP survives jobs=off; without a clamp
    # the mode still gates the injection.
    jobs = _pytest_parallel_jobs()
    if clamp is None:
        if jobs is None:
            return argv
        if cap is not None:
            # D2 — same unset-vs-auto split as the string transform.
            raw = (os.environ.get("REGENLOOP_PYTEST_JOBS") or "").strip()
            if raw == "":
                jobs = str(cap)
            elif jobs.isdecimal():
                jobs = str(min(int(jobs), cap))

    # Guard 4: already inside an xdist worker (prevent N×N oversubscription)?
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return argv

    # Guard 5: is xdist importable by this gate's interpreter (through the
    # wrapper, when there is one)? The APPEND itself — clamp or injection —
    # lands only past this probe.
    if not _pytest_xdist_importable_argv(probe):
        return argv

    if clamp is not None:
        return argv + clamp
    return argv + ["-n", jobs]


# --------------------------------------------------------------------------- #
# --safe mode: vitest worker capping on the COMMAND-gate path
# --------------------------------------------------------------------------- #
def _apply_vitest_parallelism_str(cmd: str) -> str:
    """String-path bridge to the carve module's ``_apply_vitest_parallelism``.

    That resolver (five guards + the ``$REGENLOOP_VITEST_JOBS`` read) already
    exists and is well tested, but its only callers were inside the regression
    engine — so a plain command gate like ``ui-test`` was never capped. This is
    the missing command-gate call site; the decision logic is reused verbatim,
    never duplicated. Imported lazily through the same seam
    ``build_check_report`` uses, so command-gate-only repos still never load the
    regression engine at import time.

    A no-op by construction whenever ``$REGENLOOP_VITEST_JOBS`` is unset/off —
    exactly like ``_apply_pytest_parallelism``'s own unconditional call site.
    """
    return _carve_module()._apply_vitest_parallelism(cmd)


def _apply_vitest_parallelism_argv(argv: list[str]) -> list[str]:
    """Argv-path bridge — the one that matters for this repo's real ``ui-test``.

    ``bunx vitest run`` has no shell operators and ``bunx`` resolves on PATH
    wherever bun is installed, so ``_needs_shell`` routes it to the argv list,
    not the string. Rather than re-implement the string resolver's five guards,
    this reuses its DECISION: it runs the resolver against a ``shlex.join``'d
    view (no vitest-invocation token needs quoting, so the regexes see the same
    text) and, only if the resolver appended something, appends those identical
    literal tokens to the ORIGINAL list. The transformed string is never
    re-split, so the many gates this passes through unchanged carry zero
    quoting risk.
    """
    if not argv:
        return argv
    before = shlex.join(argv)
    after = _apply_vitest_parallelism_str(before)
    if after == before or not after.startswith(before):
        return argv
    return argv + after[len(before):].split()


# --------------------------------------------------------------------------- #
# --safe mode: heavy/light gate classification
# --------------------------------------------------------------------------- #
# A command is LIGHT only on an explicit whole-invocation match: pure linters,
# formatters, type-checkers and structural validators with no test-runner or
# browser-driving signature. Everything else — including anything unrecognized
# — is HEAVY, because for a safety feature the unknown case must fail toward
# protection, not toward unthrottled concurrency. Matching is deliberately
# whole-invocation, never a bare script name: ``ui_dist_check.py --verify``
# (pure hashing) and ``ui_dist_check.py --rebuild --junit …`` (a real build) are
# the same script with opposite cost.
_LIGHT_ALLOWLIST_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"(?:^|[;&|]\s*)(?:\S*/)?(?:uvx\s+)?ruff(?:@\S+)?\b"),
    re.compile(r"(?:^|[;&|]\s*)(?:\S*/)?(?:uvx\s+(?:--from\s+\S+\s+)?)?shellcheck\b"),
    re.compile(r"\btsc\b.*--noEmit\b"),
    re.compile(r"\bdesign_lint\.py\b"),
    re.compile(r"\bvalidate_plugin\.py\b"),
    re.compile(r"\bui_dist_check\.py\s+--verify\b"),
)


def _guard_env_override(gate_name: str) -> str | None:
    """Force a gate into a class via ``$REGENLOOP_GUARD_HEAVY_GATES`` /
    ``$REGENLOOP_GUARD_LIGHT_GATES`` (comma-separated gate names).

    Heavy wins when a name appears in both — the escape hatches exist so an
    operator can correct a misclassification without editing ``gates.toml``,
    and the safe direction for an ambiguous instruction is more throttling.
    """
    def _names(var: str) -> set[str]:
        return {n.strip() for n in (os.environ.get(var) or "").split(",") if n.strip()}

    if gate_name in _names("REGENLOOP_GUARD_HEAVY_GATES"):
        return "heavy"
    if gate_name in _names("REGENLOOP_GUARD_LIGHT_GATES"):
        return "light"
    return None


def _classify_gate_class(gate: dict, command: str) -> str:
    """Classify one COMMAND FIELD as ``"heavy"`` or ``"light"`` for the guard.

    Per command field, not per gate: the same gate's check and autofix may
    differ (``ui-dist-freshness``'s ``--verify`` check is light, its
    ``bun run build`` autofix is heavy). *command* is the RAW, pre-interpolation
    template, so classification never depends on which files changed.

    Order:
      1. ``kind == "regression"`` or a ``{junit}`` token -> heavy,
         UNCONDITIONALLY and before anything else, so neither an allowlist
         match nor the env escape hatch can light-classify a real build-and-diff
         regression gate;
      2. the ``REGENLOOP_GUARD_HEAVY_GATES``/``REGENLOOP_GUARD_LIGHT_GATES``
         escape hatch;
      3. the light allowlist;
      4. heavy by default.
    """
    if str(gate.get("kind") or "") == "regression" or "{junit}" in command:
        return "heavy"
    override = _guard_env_override(str(gate.get("name", "")))
    if override is not None:
        return override
    if any(pat.search(command) for pat in _LIGHT_ALLOWLIST_PATTERNS):
        return "light"
    return "heavy"


def _regression_pipeline_enabled() -> bool:
    """Resolve whether baseline/branch pipelining (B8) is opted in.

    DEFAULT OFF (consistent with B7's ``_vitest_max_workers``): with the env
    unset, returns False so behavior is byte-identical to the sequential path —
    B8 ships off and only activates when the operator opts in. Pipelining
    overlaps the cache-MISS baseline provision with the branch run, which
    doubles concurrent test runs for pytest/build regression gates; that can
    reintroduce the pegged-cores heat disease B7 exists to prevent, so it is
    never silently enabled. Accepted ``$REGENLOOP_REGRESSION_PIPELINE``:
      unset | ``off`` | ``none`` | ``0`` | ``false`` | ``no``  -> False
      ``on`` | ``1`` | ``true`` | ``yes``                       -> True
      any unrecognized value                                    -> False (safe default)
    """
    raw = (os.environ.get("REGENLOOP_REGRESSION_PIPELINE") or "").strip().lower()
    return raw in ("on", "1", "true", "yes")


def _default_check_timeout() -> float | None:
    """Resolve the default per-check timeout from the environment (SC-1, audit #2).

    ``--timeout`` historically defaulted to ``None`` (unbounded), so one hung
    check command froze the closing gate indefinitely. The default is now
    **bounded** — 900 s when the env is unset — so an unattended closing gate
    rides out a hung check instead of hanging forever. An operator can tune it
    via ``$REGENLOOP_GATE_TIMEOUT_S`` (set to a positive integer of seconds).

    ESCAPE HATCH (fail-safe): ``0`` / empty / malformed / non-finite all resolve
    to ``None`` (unbounded), NEVER ``0`` — ``subprocess.run(timeout=0)`` raises
    ``TimeoutExpired`` immediately and would brick every gate. The distinction:
    env **unset** → 900 s (bounded default); env **set to** ``0``/empty/garbage/
    non-finite → ``None`` (opt-out escape hatch). ``--timeout SECONDS`` passed explicitly
    always wins (it replaces this default at parse time; see
    ``_timeout_seconds_arg`` for the explicit-flag validation).

    SCOPE (F1 truth, pinned empirically by ``TestTimeoutFlagTruth``): the
    resolved timeout governs every path that EXECUTES a check or fix command —
    command-gate checks (each CNR re-probe attempt is separately bounded, so
    the worst-case TOTAL wall for one forever-hanging gate is
    ``(N + 1) * timeout + N * interval``), regression branch runs
    (``RunContext.timeout`` → ``_run_once_and_parse``), baseline dep-install +
    baseline suite run (``_provision_baseline_test_map`` passes
    ``run_ctx.timeout`` to both), and autofix runs. It does NOT govern
    internal plumbing: git calls (``GIT_TIMEOUT`` = 300 s), the pytest-xdist
    importability probe (20 s), and the CoW clone/probe
    (``_COW_CLONE_TIMEOUT_S`` = 600 s) keep their own fixed ceilings.
    """
    raw = os.environ.get("REGENLOOP_GATE_TIMEOUT_S")
    if raw is None:
        # LEGACY (1.1.0 on main): honor $REGENLOOP_GATE_TIMEOUT when the
        # canonical _S var is unset — valid positive numbers bind; anything
        # else warns and falls through to the default (released semantics,
        # deliberately NOT this knob's escape-hatch semantics: the legacy var
        # never meant "unbounded", so garbage must not silently become it).
        legacy = (os.environ.get(GATE_TIMEOUT_ENV) or "").strip()
        if legacy:
            try:
                legacy_seconds = float(legacy)
            except (TypeError, ValueError):
                legacy_seconds = None
            if legacy_seconds is not None and legacy_seconds > 0 and math.isfinite(legacy_seconds):
                return legacy_seconds
            if legacy_seconds is not None or legacy:
                print(
                    f"gate_runner: {GATE_TIMEOUT_ENV}={legacy!r} is not a usable "
                    f"number — using the default 900s.",
                    file=sys.stderr,
                )
        return 900.0  # unset -> bounded default (SC-1: deliver by default, not opt-in)
    raw = raw.strip()
    if not raw:
        return None  # empty -> escape hatch
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None  # malformed -> escape hatch
    if not math.isfinite(seconds):
        # F2: nan defeats every comparison (silently unbounded) and inf likewise
        # — non-finite is malformed-equivalent -> escape hatch.
        return None
    if seconds <= 0:
        return None  # 0 or negative -> escape hatch
    return seconds


def _timeout_seconds_arg(raw: str) -> float:
    """Validate the explicit ``--timeout SECONDS`` flag (F1 hardening).

    ``type=float`` alone accepted ``0`` / negatives / ``inf`` / ``nan``: a
    literal ``--timeout 0`` reached ``run_command`` as ``timeout=0.0``, where
    ``subprocess.run(timeout=0)`` raises ``TimeoutExpired`` instantly — bricking
    every gate as could_not_run and (since T3) burning the full transient
    re-probe budget first, because ``"timed out"`` is a re-probable reason.
    Reject non-positive / non-finite values at parse time; the unbounded escape
    hatch is env-only by design (``$REGENLOOP_GATE_TIMEOUT_S=0``).
    """
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"invalid number of seconds: {raw!r}")
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError(
            f"must be a positive finite number of seconds (got {raw!r}); for an "
            "unbounded run set $REGENLOOP_GATE_TIMEOUT_S=0 instead"
        )
    return seconds


def _fmt_seconds(seconds: float | None) -> str:
    """Render a timeout value for reason strings (F10a).

    The float defaults (900.0 / 300.0 / 600.0, or any explicit ``--timeout``
    value) f-string-format as ``900.0s`` where ``900s`` is meant. Rendering
    contract: ``timeout=None`` means unbounded, so ``None`` renders as
    ``"none"`` in reason strings; whole floats render as int-strings
    (``900.0 -> "900"``); anything fractional keeps its minimal decimal
    (``0.5 -> "0.5"``). Message PREFIXES (e.g. ``timed out after``) are NOT
    this helper's to touch — ``_cnr_is_transient`` keys on them.
    """
    if seconds is None:
        return "none"
    if float(seconds).is_integer():
        return str(int(seconds))
    return f"{seconds:g}"


def _default_flake_retries(gate: dict) -> int:
    """Resolve a regression gate's flake-retry count from ONE shared place (SC-2).

    Audit #5: the terminal full-tier gate had ZERO flake retries, so one flaky
    test misrouted the self-healing loop onto a phantom culprit. The default is
    now 1 (tier-agnostic), tunable via ``$REGENLOOP_FLAKE_RETRIES`` (default 1;
    parsed defensively; negative clamped to 0). A per-gate ``flake_retries``
    field stays authoritative — it overrides the env default.

    Called from BOTH ``run_branch_with_retries`` (the engine) and
    ``_regression_timing`` (the JSON mirror) so the report's ``retries_used``
    cannot diverge from the runs actually executed (the v1 single-site fix left
    the mirror reporting 1 run on a 2-run gate).
    """
    override = gate.get("flake_retries")
    if override is not None:
        return int(override)
    raw = (os.environ.get("REGENLOOP_FLAKE_RETRIES") or "1").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 1
    return max(0, value)


def _lazy_retry_disabled(gate: dict) -> bool:
    """Resolve the B6 lazy-scheduling opt-out (pre-push fix round).

    Lazy flake retry is ON by default (a clean first run with a baseline
    collapses — verdict-identical). This helper reports whether it has been
    explicitly disabled, restoring the eager ``retries + 1`` runs always:
      * gate field ``lazy_retry = false`` (validated a real bool — rule 10),
      * or ``$REGENLOOP_LAZY_RETRY`` in (``off`` | ``0`` | ``false`` | ``no``
        | ``none``) — the B6 kill switch, house-env style (unset/malformed
        stay lazy; there is no "force on" because the baseline-gated
        collapse is a soundness precondition, not a preference).
    """
    if gate.get("lazy_retry") is False:
        return True
    raw = (os.environ.get("REGENLOOP_LAZY_RETRY") or "").strip().lower()
    return raw in ("off", "0", "false", "no", "none")


def _cnr_is_transient(reason: str) -> bool:
    """Return True iff a ``could_not_run`` reason is TRANSIENT (worth re-probing).

    T3 (audit #1): eligibility is REASON-SCOPED, not status-scoped. Only a tight
    transient allowlist is re-probed; everything else is permanent and fails
    closed immediately. The allowlist is deliberately small and substring-based
    against the exact reason strings emitted by the check-execution sites:

      * ``"... timed out after <N>s"`` — the T1 timeout ceiling (command-gate and
        regression-branch run_command paths).
      * ``"tool error running check: ..."`` — ``OSError``/network failures at the
        same two sites.
      * ``"JUnit XML not found ..."`` — the regression-branch artifact-absent
        path (the branch command produced no JUnit on first run).

    Permanent (returns False — never re-probed): exit 127/126 (missing/misconfigured
    binary via ``_classify_tool_error``), ``ci_only``, ``no check command defined``,
    legacy-venv-refusal, ``{python}``-unresolved, malformed/empty JUnit, the
    per-run crash guard, AND every ``BaselineRunError`` reason (baseline
    provisioning is structurally excluded from re-probe — see
    ``_run_branch_with_cnr_reprobe``).
    """
    if not reason:
        return False
    if "timed out" in reason:
        return True
    if "tool error running check" in reason:
        return True
    if "JUnit XML not found" in reason:
        return True
    return False


def _cnr_reprobe_limit() -> int:
    """Max TRANSIENT could_not_run re-probes before failing closed (T3, audit #1).

    ``$REGENLOOP_CNR_REPROBE`` (default 2; 0 = no re-probe = pre-T3 behavior).
    Parsed defensively: malformed -> default. The total attempt count is this + 1.
    """
    raw = (os.environ.get("REGENLOOP_CNR_REPROBE") or "2").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 2
    return max(0, value)


def _cnr_reprobe_interval_s() -> float:
    """Seconds to wait between transient could_not_run re-probes (T3, audit #1).

    ``$REGENLOOP_CNR_REPROBE_INTERVAL_S`` (default 30). Parsed defensively:
    malformed/negative/non-finite -> default. The wait is skipped on the final
    attempt.
    """
    raw = (os.environ.get("REGENLOOP_CNR_REPROBE_INTERVAL_S") or "30").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 30.0
    # F2: inf must not reach time.sleep (raises an uncaught OverflowError from
    # inside the re-probe loop) and nan silently skips every wait — both fall
    # back to the default, same as malformed/negative.
    if not math.isfinite(value) or value < 0:
        return 30.0
    return value


# --------------------------------------------------------------------------- #
# Evaluating a single gate (check)
# --------------------------------------------------------------------------- #
def evaluate_gate(
    gate: dict,
    changed: list[str],
    never_touch: list[str],
    base: str,
    cwd: str,
    timeout: float | None,
    *,
    safe_mode: bool = False,
) -> dict:
    """Run one applicable gate's ``check`` and return its result dict.

    ``cwd`` is the repo root; the gate may run in a sub-directory if it has a
    ``chdir`` field (see ``resolve_gate_cwd``).

    ``safe_mode`` routes the check through ``regenloop_guard``'s machine-global
    admission queue (classified per command field by ``_classify_gate_class``)
    instead of spawning it immediately. It changes only WHEN the command runs,
    never the verdict mapping.
    """
    name = str(gate.get("name", "<unnamed>"))

    # Selection and fence filtering always use repo-relative paths.
    fch = fence_filtered(gate, changed, never_touch)

    # Resolve the gate's effective cwd and rebase {changed} to chdir-relative.
    try:
        gate_cwd, chdir_prefix = resolve_gate_cwd(gate, cwd)
    except ValueError as exc:
        return {
            "name": name, "ci_job": gate.get("ci_job"),
            "autofix_available": False,
            "status": "could_not_run", "reason": str(exc),
        }
    fch_interp = rebase_to_chdir(fch, chdir_prefix) if chdir_prefix else fch

    autofix = str(gate.get("autofix") or "").strip()
    result: dict = {
        "name": name,
        "ci_job": gate.get("ci_job"),
        # autofix_available reflects whether {changed} will expand to anything
        # in the gate's working directory.
        "autofix_available": bool(autofix) and bool(fch_interp),
    }

    # ci_only gates exist in CI but cannot run locally — always could_not_run,
    # never executed, never silently absent (spec §4.2, critic 2f).
    if gate.get("ci_only"):
        result.update(status="could_not_run", reason="ci-only", autofix_available=False)
        return result

    # Unmet preconditions -> could_not_run with the hint (never pass).
    for need in gate.get("needs", []):
        satisfied, why = probe_need(str(need), gate.get("probe_port"))
        if not satisfied:
            result.update(status="could_not_run", reason=why)
            hint = gate.get("needs_hint")
            if hint:
                result["hint"] = str(hint)
            return result

    check = str(gate.get("check") or "").strip()
    if not check:
        # An applicable gate with no runnable check is reported could_not_run,
        # not pass — refusing to false-green an undefined check.
        result.update(status="could_not_run", reason="no check command defined")
        return result

    # Legacy-refusal guard (spec §5): a plain command gate whose check happens to
    # contain the venv-create pattern is reported could_not_run and never run —
    # placed before path_filters/interpolate so no short-circuit can mask it.
    legacy = _check_legacy_venv_create(gate)
    if legacy is not None:
        result.update(status="could_not_run", reason=legacy)
        return result

    # Opt-in path-based test selection (only active when gate declares path_filters).
    # Matching uses repo-relative fch — the fence-filtered pool that {changed}
    # also draws from, before chdir rebasing — so path_filter globs are written
    # as repo-relative patterns (e.g. "src/auth/**").  select values are NOT
    # rebased to chdir; they are passed verbatim, shell-quoted by the runner.
    #
    # Handled BEFORE the {changed}-empty short-circuit so a gate mixing {changed}
    # + {selected} cannot be pre-empted into a false "pass" when path_filters has
    # no match but select_default is also absent (the skip must win).
    #
    # "skip" is verdict-neutral: treated the same as "pass" in build_check_report's
    # green computation.  The distinct status makes the gate's non-execution visible
    # in the report, which matters for auditability: a skipped gate ran nothing.
    # Coverage is still accounted for via the gate's `when` match — these files
    # ARE matched, so they do not create a phantom unmatched_changed_files gap.
    path_filters = gate.get("path_filters")
    selected_expansion: str | None = None  # None → gate has no path_filters
    selected_list: list[str] | None = None  # raw selects for the argv path
    if path_filters is not None:
        matched_selects = compute_selected(path_filters, fch)
        if matched_selects:
            selected_expansion = " ".join(shlex.quote(s) for s in matched_selects)
            selected_list = matched_selects
        else:
            default = str(gate.get("select_default") or "").strip()
            if default:
                selected_expansion = shlex.quote(default)
                selected_list = [default]
            else:
                # No entry matched and no fallback defined → skip cleanly.
                result.update(
                    status="skip",
                    reason=(
                        "path_filters: no entry matched the changed files; "
                        'add select_default = "<test-dir>" for a fallback'
                    ),
                )
                return result

    # If the check is scoped to {changed} but fch_interp is empty (all files
    # were fenced or all lie outside chdir), there is nothing to check -> pass.
    # Trade-off: fenced files that matched this gate's ``when`` are deliberately
    # not autofix-touched locally; they are covered via CI / code review (the
    # "fenced files clear locally, CI covers them" contract). Surfaced in
    # ``coverage.fenced_changed_files`` so the human knows they exist.
    # Reported as skipped_no_files rather than pass: the check never executed,
    # and calling that "pass" made "not checked" indistinguishable from "checked
    # and passed" in the report. The status is verdict-neutral, so the contract
    # above (fenced files clear locally, CI covers them) is unchanged. Covers
    # both routes to an empty set: every matching file fenced, and a forced
    # --gate whose `when` matched nothing.
    if "{changed}" in check and not fch_interp:
        result.update(
            status="skipped_no_files",
            summary="no applicable files after fence filter (not checked)",
        )
        return result

    # {changed} expands to chdir-relative paths; command runs in gate_cwd.
    # {selected} (opt-in) is expanded separately, after interpolate, so that
    # the existing interpolate() signature is preserved byte-for-byte.
    #
    # Execution routing: a check WITHOUT shell operators (redirects/pipes/
    # builtins — see ``_needs_shell``) is built into an argv list via
    # ``build_check_argv`` and run with ``subprocess.run(shell=False)`` — the
    # cross-platform default (no POSIX-only ``shlex.quote``, so Windows
    # ``cmd.exe`` no longer breaks on single-quoted args). A check WITH shell
    # operators keeps the legacy string + ``shell=True`` path so existing
    # shell-style gates (``printf '%s' {changed} > captured.txt``) behave
    # byte-for-byte as before.
    use_shell = _needs_shell(check)
    if use_shell:
        cmd = interpolate(check, base, fch_interp)
        if selected_expansion is not None:
            cmd = cmd.replace("{selected}", selected_expansion)
        if "{python}" in cmd:
            resolved = _resolve_gate_python(gate, cwd, baseline=False)
            if resolved.kind != "resolved":
                result.update(
                    status="could_not_run",
                    reason=(
                        f"could not resolve {{python}} ({resolved.kind}); run "
                        f"/regenloop-doctor (or /regenloop-init) to select an interpreter"
                    ),
                )
                return result
            cmd = _expand_python(cmd, resolved.path)
        run_target: str | list[str] = cmd
    else:
        # argv path — no quoting ever; each file/select/path is one literal word.
        python_path: "Path | None" = None
        if "{python}" in check:
            resolved = _resolve_gate_python(gate, cwd, baseline=False)
            if resolved.kind != "resolved":
                result.update(
                    status="could_not_run",
                    reason=(
                        f"could not resolve {{python}} ({resolved.kind}); run "
                        f"/regenloop-doctor (or /regenloop-init) to select an interpreter"
                    ),
                )
                return result
            python_path = resolved.path
        run_target = build_check_argv(
            check,
            base=base,
            files=fch_interp,
            selected=selected_list,
            junit_path=None,
            python_path=python_path,
        )

    # pytest-xdist auto-parallelism injection (the bridge between the argv
    # execution primitive from STAGE 1 and the xdist transform from STAGE 3).
    # The string path (shell=True branch: gates WITH shell operators) uses the
    # regex-based _apply_pytest_parallelism; the list path (shell=False branch:
    # the cross-platform default) uses _apply_pytest_parallelism_argv. Both
    # apply the SAME five-guard gating, so the injected "-n auto" reaches
    # run_command as the final argv word / shell token.
    #
    # The vitest bridges sit alongside them and are equally unconditional: both
    # transforms are no-ops unless their own env knob is set, and it is safe
    # mode's env defaulting (see ``main``) that turns the vitest one on.
    #
    # ``allow_runner_wrappers=safe_mode`` is the ONE piece of pytest injection
    # that is safe-mode-conditional: recognising ``uv run pytest …`` by default
    # would append ``-n auto`` where nothing was appended before, silently
    # overriding a repo's own ``addopts`` worker count. See
    # ``_RE_PYTEST_RUNNER_WRAPPER``. ``cap`` is the safe-mode worker ceiling
    # (``_safe_pytest_cap()``, INV-2: sized by regenloop_guard); None keeps
    # non-safe output byte-identical (INV-5).
    if isinstance(run_target, str):
        run_target = _apply_pytest_parallelism(
            run_target, allow_runner_wrappers=safe_mode,
            cap=(_safe_pytest_cap() if safe_mode else None),
        )
        run_target = _apply_vitest_parallelism_str(run_target)
        cmd_for_classify = run_target
    else:
        run_target = _apply_pytest_parallelism_argv(
            run_target, allow_runner_wrappers=safe_mode,
            cap=(_safe_pytest_cap() if safe_mode else None),
        )
        run_target = _apply_vitest_parallelism_argv(run_target)
        cmd_for_classify = " ".join(run_target)

    # {python} (opt-in): a plain command gate may reference {python} exactly
    # like a regression gate's branch run does. Command gates always run
    # against the real working tree (never an ephemeral worktree), so ancestor
    # search is safe here — mirrors the regression branch-run call
    # (baseline=False). Scope note: autofix does NOT support {python} (no
    # template currently needs it there); this is deliberately narrower than
    # the check path.
    # T3 (audit #1): bounded TRANSIENT could_not_run re-probe. Only transient
    # reasons (timeout / OSError / JUnit-absent — see ``_cnr_is_transient``) are
    # re-probed; permanent ones (exit 127/126, etc.) return immediately. The
    # exit-code contract is unchanged — after the budget the last could_not_run
    # stands (fail-closed). pass/fail are returned on the first decisive attempt.
    once = _run_command_gate_check(
        run_target, gate_cwd, timeout, cmd_for_classify,
        guard_klass=(_classify_gate_class(gate, check) if safe_mode else None),
        guard_label=f"check:{name}",
    )
    result["status"] = once["status"]
    if "reason" in once:
        result["reason"] = once["reason"]
    if "summary" in once:
        result["summary"] = once["summary"]
    if "items" in once:
        result["items"] = once["items"]
    # F3: re-probe attempt count from the timing=None paths (recorded TOP-LEVEL
    # on ``once`` by ``_run_command_gate_check``; the timing-ful paths carry it
    # inside ``timing``, copied below).
    if "reprobe_attempts" in once:
        result["reprobe_attempts"] = once["reprobe_attempts"]
    # B1 (Phase 1): per-gate timing observability. Pure additive — never read by
    # verdict/exit-code logic. Attached only when the check actually executed
    # (the timeout/OSError paths omit it, by design — ``timing`` is None there).
    if once.get("timing") is not None:
        result["timing"] = once["timing"]
    return result


def _run_command_check_once(run_target, gate_cwd, timeout, cmd_for_classify, *,
                            guard_klass: str | None = None,
                            guard_label: str = "") -> dict:
    """Run one command-gate check; map TimeoutExpired/OSError/exit-code to a dict.

    Returns a fresh result dict (``status`` + ``reason``|``summary``[/``items``]
    + ``timing``). ``timing`` is ``None`` on the timeout/OSError paths (the check
    did not complete) and a ``{"wall_s"}`` block otherwise — matching the
    pre-T3 attachment contract (the timing line ran only past the early returns).

    ``guard_klass`` is ``None`` unless safe mode is active; when set, the check
    waits for a machine-global turn via ``_run_via_guard`` before spawning.

    ``timing.wall_s`` caveat: the timer wraps the guard call, so under safe mode
    it is QUEUE-INCLUSIVE — admission wait plus execution, not execution alone.
    Nothing reads it for a verdict (it is observability only), but ``--safe``
    and non-``--safe`` wall times are therefore not directly comparable.
    """
    try:
        _wall_t0 = time.monotonic()
        if guard_klass is not None:
            rc, output = _run_via_guard(
                run_target, gate_cwd, timeout, klass=guard_klass, label=guard_label
            )
        else:
            rc, output = run_command(run_target, gate_cwd, timeout)
        check_wall_s = time.monotonic() - _wall_t0
    except subprocess.TimeoutExpired:
        return {
            "status": "could_not_run",
            "reason": f"check timed out after {_fmt_seconds(timeout)}s",
            "timing": None,
        }
    except _GuardRefusalError as exc:
        # MUST precede the OSError clause below (which _GuardRefusalError
        # subclasses): that clause's reason is on _cnr_is_transient's re-probe
        # allowlist, and re-probing a refusal just re-queues the gate at the
        # back of the FIFO. This prefix matches none of the allowlist
        # substrings, so a refusal fails closed immediately.
        return {
            "status": "could_not_run",
            "reason": f"regenloop_guard: {exc}",
            "timing": None,
        }
    except OSError as exc:
        return {
            "status": "could_not_run",
            "reason": f"tool error running check: {exc}",
            "timing": None,
        }

    # wall_s is queue-inclusive under safe mode (see this function's docstring).
    timing = {"wall_s": check_wall_s}
    if rc == 0:
        return {"status": "pass", "summary": "ok", "timing": timing}
    tool_error = _classify_tool_error(cmd_for_classify, rc)
    if tool_error is not None:
        return {"status": "could_not_run", "reason": tool_error, "timing": timing}
    return {
        "status": "fail",
        "summary": f"check exited {rc}",
        "items": _output_items(output),
        "timing": timing,
    }


def _run_command_gate_check(run_target, gate_cwd, timeout, cmd_for_classify, *,
                            guard_klass: str | None = None,
                            guard_label: str = "") -> dict:
    """Run a command-gate check with bounded TRANSIENT could_not_run re-probe (T3).

    Wraps ``_run_command_check_once``: a transient ``could_not_run`` (per
    ``_cnr_is_transient``) is re-probed up to ``$REGENLOOP_CNR_REPROBE`` times
    with ``$REGENLOOP_CNR_REPROBE_INTERVAL_S`` between attempts; a permanent one
    (or the re-probe budget exhausted) returns/raises the last result unchanged.
    ``pass``/``fail`` are returned on the first decisive attempt. Total attempts
    for a forever-transient check == limit + 1.
    """
    limit = _cnr_reprobe_limit()
    interval = _cnr_reprobe_interval_s()
    attempt = 0
    while True:
        attempt += 1
        once = _run_command_check_once(
            run_target, gate_cwd, timeout, cmd_for_classify,
            guard_klass=guard_klass, guard_label=guard_label,
        )
        if once["status"] != "could_not_run":
            break
        if limit <= 0 or not _cnr_is_transient(once.get("reason") or ""):
            break
        if attempt > limit:
            break
        time.sleep(interval)
    # N3: record how many attempts were made (observability only; not read by
    # verdict/exit-code logic). Inside ``timing`` when the check completed; on
    # the timeout/OSError paths (timing is None) TOP-LEVEL instead — F3: it was
    # previously dropped there, hiding that N+1 attempts were made.
    if once.get("timing") is not None:
        once["timing"]["reprobe_attempts"] = attempt
    else:
        once["reprobe_attempts"] = attempt
    return once


# --------------------------------------------------------------------------- #
# Autofix execution
# --------------------------------------------------------------------------- #
def autofix_gate(
    gate: dict,
    changed: list[str],
    never_touch: list[str],
    base: str,
    cwd: str,
    timeout: float | None,
    *,
    safe_mode: bool = False,
) -> dict:
    """Run one gate's ``autofix`` over the fence-filtered changed set.

    ``cwd`` is the repo root; the gate may run in a sub-directory if it has a
    ``chdir`` field (see ``resolve_gate_cwd``).

    ``safe_mode`` routes the autofix through the guard too: an autofix such as
    ``cd ui && bun run build`` is every bit as resource-hungry as a check, and
    any path bounded by ``run_command``'s timeout can equally orphan processes.
    """
    name = str(gate.get("name", "<unnamed>"))
    action: dict = {"name": name}

    if gate.get("ci_only"):
        action.update(status="skipped", reason="ci-only")
        return action

    autofix = str(gate.get("autofix") or "").strip()
    if not autofix:
        action.update(status="skipped", reason="no autofix command")
        return action

    # Selection and fence filtering always use repo-relative paths.
    fch = fence_filtered(gate, changed, never_touch)

    # Resolve the gate's effective cwd and rebase {changed} to chdir-relative.
    try:
        gate_cwd, chdir_prefix = resolve_gate_cwd(gate, cwd)
    except ValueError as exc:
        action.update(status="error", reason=str(exc))
        return action
    fch_interp = rebase_to_chdir(fch, chdir_prefix) if chdir_prefix else fch

    # An autofix scoped to {changed} with an empty fch_interp is a no-op.
    if "{changed}" in autofix and not fch_interp:
        action.update(status="skipped", reason="no applicable files after fence filter")
        return action

    cmd = interpolate(autofix, base, fch_interp)
    action["command"] = cmd
    guard_klass = _classify_gate_class(gate, autofix) if safe_mode else None
    try:
        if guard_klass is not None:
            rc, output = _run_via_guard(
                cmd, gate_cwd, timeout, klass=guard_klass, label=f"autofix:{name}"
            )
        else:
            rc, output = run_command(cmd, gate_cwd, timeout)
    except subprocess.TimeoutExpired:
        action.update(
            status="error", reason=f"autofix timed out after {_fmt_seconds(timeout)}s"
        )
        return action
    except _GuardRefusalError as exc:
        # Ordered before the OSError clause it subclasses. Autofix's own status
        # contract is applied/skipped/error — a guard refusal reads like any
        # other tool error here, and never introduces "could_not_run".
        action.update(status="error", reason=f"regenloop_guard: {exc}")
        return action
    except OSError as exc:
        action.update(status="error", reason=f"tool error running autofix: {exc}")
        return action

    action.update(status="applied", exit_code=rc, items=_output_items(output))
    return action


def _expand_junit(template: str, junit_path: str) -> str:
    """Expand the ``{junit}`` token to a ``shlex.quote``-d absolute path (§4A).

    ``{junit}`` is always absolute, so a gate's ``chdir`` does not affect it.
    """
    return template.replace("{junit}", shlex.quote(junit_path))


def _expand_python(template: str, path: Path) -> str:
    """Expand the ``{python}`` token to a ``shlex.quote``-d interpreter path.

    Mirrors ``_expand_junit``'s shape. ``path`` is the resolved absolute
    interpreter path from ``venv_resolve.resolve_python_interpreter`` (spec §4).
    """
    return template.replace("{python}", shlex.quote(str(path)))


def _resolve_gate_python(
    gate: dict, repo_root: str, *, baseline: bool
) -> "venv_resolve.DiscoveryResult":
    """Resolve the Python interpreter for a gate's ``{python}`` token (spec §1).

    ``repo_root`` MUST be the real ``run_ctx.repo_root`` — never the ephemeral
    baseline worktree path. Cache/``VIRTUAL_ENV`` lookups are keyed by the
    gate's repo-relative ``chdir``, which is stable across worktrees (spec §1's
    "critical constraint"); resolving from inside the ephemeral worktree would
    key/search the wrong tree and break AC18's cross-run interpreter identity.

    For the baseline call site (``baseline=True``), ancestor search is disabled:
    a fresh directory walk rooted in the ephemeral worktree
    (``repo_root/regenloop/local/.worktrees/regression-base-<sha>``)
    is structurally incapable of reaching the real project's venv (AC16), so
    source 3 must never run there — resolution falls back to
    ``VIRTUAL_ENV``/cache only.
    """
    return venv_resolve.resolve_python_interpreter(
        Path(repo_root), gate.get("chdir"), allow_ancestor_search=not baseline
    )


def _check_legacy_venv_create(gate: dict) -> str | None:
    """Return a ``could_not_run`` reason if the gate creates a new venv, else ``None``.

    Refuses (does not construct — AC10) any gate whose *own configured*
    ``check``/``baseline_install`` matches ``venv_resolve._RE_VENV_CREATE``
    (``python3 -m venv`` / ``virtualenv``). Per spec §5 the runner reports such a
    gate as ``could_not_run`` and names the operator-approved migration path,
    rather than silently executing a known venv-creating command (AC12).

    Scope note: ``autofix`` is deliberately NOT guarded — spec §5's decision text
    is scoped to ``check``/``baseline_install`` only (AC12's literal wording).
    """
    name = str(gate.get("name", "<unnamed>"))
    check = str(gate.get("check") or "")
    baseline_install = str(gate.get("baseline_install") or "")
    if venv_resolve._RE_VENV_CREATE.search(check) or venv_resolve._RE_VENV_CREATE.search(
        baseline_install
    ):
        return (
            f"gate '{name}'s check/baseline_install creates a new venv on every run"
            f" — refused; run /regenloop-doctor (or /regenloop-init) to review and approve a"
            f" migration to {{python}}"
        )
    return None


# B5 (Phase 1): default branch-suite budget (seconds) above which a fast-tier
# regression gate is flagged heavy. ``REGENLOOP_FAST_BUDGET_S`` overrides; see
# ``_fast_tier_budget`` (mirrors ``_gc_grace_seconds`` env-parse robustness).
REGENLOOP_FAST_BUDGET_DEFAULT_S = 60.0


def _fast_tier_budget() -> float:
    """The branch-suite budget (seconds) above which a fast-tier regression gate
    is flagged heavy (B5, Phase 1).

    ``REGENLOOP_FAST_BUDGET_S`` overrides (must parse as a non-negative number,
    else the 60s default). Mirrors ``_gc_grace_seconds``: a negative value or NaN
    (rejected via the ``val == val`` self-equality check) falls back to default.
    """
    raw = os.environ.get("REGENLOOP_FAST_BUDGET_S")
    if raw is None:
        return REGENLOOP_FAST_BUDGET_DEFAULT_S
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return REGENLOOP_FAST_BUDGET_DEFAULT_S
    if val < 0 or not (val == val):  # noqa: PLR0124 — reject NaN
        return REGENLOOP_FAST_BUDGET_DEFAULT_S
    return val


# --------------------------------------------------------------------------- #
# Config validation
# --------------------------------------------------------------------------- #
def _validate_regression_gate(gate: dict) -> None:
    """Validate one ``kind = "regression"`` gate (spec §4A rules 0-7).

    Raises ``ValueError`` with a descriptive message on the first violation.
    """
    name = gate.get("name", "<unnamed>")

    # 0. name must be filesystem-safe (used in cache filenames / JUnit paths).
    if not isinstance(name, str) or not _REGRESSION_NAME_RE.fullmatch(name):
        raise ValueError(f"gate {name!r}: name must match ^[A-Za-z0-9_-]+$")

    # 1. tier must be present and one of {"fast", "full"}.
    if gate.get("tier") not in ("fast", "full"):
        raise ValueError(
            f'gate {name!r}: regression gate requires tier = "fast" | "full"'
        )

    # 2. check must be non-empty and contain {junit}.
    check = str(gate.get("check") or "").strip()
    if not check or "{junit}" not in check:
        raise ValueError(
            f"gate {name!r}: regression gate check must contain {{junit}}"
        )

    # 3. autofix must not be set (regression gates have no autofix path).
    if str(gate.get("autofix") or "").strip():
        raise ValueError(f"gate {name!r}: regression gates do not support autofix")

    # 4. when is required (absent/empty when -> gate never runs).
    if not (gate.get("when") or []):
        raise ValueError(
            f"gate {name!r}: regression gates require an explicit when list "
            f"(absent when = gate never runs)"
        )

    # 5. chdir path-safety (same rules as command gates).
    chdir = str(gate.get("chdir") or "").strip().rstrip("/")
    if chdir and chdir != ".":
        _validate_chdir_str(chdir, str(name))

    # 6. flake_retries, if present, must be a non-negative integer. bool is
    #    rejected first (isinstance(True, int) is True in Python; True/False
    #    would silently be accepted as 1/0 without this explicit check).
    flake_retries = gate.get("flake_retries")
    if flake_retries is not None:
        if isinstance(flake_retries, bool) or not isinstance(flake_retries, int) or flake_retries < 0:
            raise ValueError(
                f"gate {name!r}: flake_retries must be a non-negative integer"
            )

    # 7. min_testcases_ratio, if present, must be a number in [0.0, 1.0]. bool is
    #    rejected first (isinstance(True, int) is True in Python).
    ratio = gate.get("min_testcases_ratio")
    if ratio is not None:
        if (
            isinstance(ratio, bool)
            or not isinstance(ratio, (int, float))
            or not (0.0 <= ratio <= 1.0)
        ):
            raise ValueError(
                f"gate {name!r}: min_testcases_ratio must be a number (int or "
                f"float) between 0.0 and 1.0"
            )

    # 8. baseline_install, if present, must be a non-empty string. A TOML array
    #    (or boolean) would stringify to a Python repr, running unintentionally.
    baseline_install = gate.get("baseline_install")
    if baseline_install is not None:
        if not isinstance(baseline_install, str) or not baseline_install.strip():
            raise ValueError(
                f"gate {name!r}: baseline_install must be a non-empty string "
                f"(got {type(baseline_install).__name__!r}); a TOML array would "
                f"stringify to the literal list representation and run unintentionally"
            )

    # 8b. baseline_expect_shrink, if present, must be a real bool. The B2
    #     baseline crash-guard checks ``gate.get(...) is True``, so a truthy
    #     non-bool (e.g. a TOML ``baseline_expect_shrink = "true"`` string) would
    #     silently never arm the hatch — the author thinks they've acknowledged
    #     a legitimate suite shrink but the guard would still trip every run.
    #     Accept ONLY ``isinstance(v, bool)``; reject everything else (a TOML
    #     int ``1`` would pass ``isinstance(v, int)`` but is not ``is True``).
    expect_shrink = gate.get("baseline_expect_shrink")
    if expect_shrink is not None and not isinstance(expect_shrink, bool):
        raise ValueError(
            f"gate {name!r}: baseline_expect_shrink must be a boolean "
            f"(got {type(expect_shrink).__name__!r}); the crash-guard checks "
            f"`is True`, so a truthy string/int would silently never arm the hatch"
        )

    # 8c. baseline_install_reuse, if present, names the dep dir to CoW-clone
    #     from HEAD into the baseline worktree (relative to chdir, e.g.
    #     "node_modules"). Must be a non-empty string — a TOML array/bool would
    #     deserialise to a Python list/bool and never match a real directory.
    #     Mirrors rule 8's baseline_install validation style. A gate declaring
    #     this field SHOULD also declare baseline_install (the fallback); not a
    #     hard error at validation time (documented, not enforced).
    reuse = gate.get("baseline_install_reuse")
    if reuse is not None:
        if not isinstance(reuse, str) or isinstance(reuse, bool) or not reuse.strip():
            raise ValueError(
                f"gate {name!r}: baseline_install_reuse must be a non-empty "
                f"string naming the dep dir to reuse (e.g. \"node_modules\"); "
                f"got {type(reuse).__name__!r}"
            )
        # Lexical safety (defense-in-depth, mirrors _validate_chdir_str at
        # lines 883-896): the dep-dir is joined onto HEAD's chdir to form
        # head_dep, whose parent is then searched for a lockfile (Stage B).
        # An absolute value or a ".." segment could escape the repo root and
        # search an arbitrary filesystem location for a lockfile. chdir gets
        # this check; baseline_install_reuse did not — close the gap.
        reuse_stripped = reuse.strip()
        if Path(reuse_stripped).is_absolute():
            raise ValueError(
                f"gate {name!r}: baseline_install_reuse {reuse_stripped!r} "
                f"must be relative to the chdir (not absolute)"
            )
        if ".." in Path(reuse_stripped).parts:
            raise ValueError(
                f"gate {name!r}: baseline_install_reuse {reuse_stripped!r} "
                f"must not contain '..'"
            )

    # 9. path_filters must not be set on a regression gate.  Regression gates
    #    always run the full suite against a cached JUnit baseline; coarse
    #    path-based selection would silently skip tests that belong in the
    #    baseline-diff comparison, breaking the regression-detection guarantee.
    if gate.get("path_filters") is not None:
        raise ValueError(
            f"gate {name!r}: path_filters is not supported on regression gates "
            f"(regression gates always run the full suite for baseline-diff semantics)"
        )

    # 10. lazy_retry, if present, must be a real boolean (rule-6/8b house
    #     style): the field is the per-gate arm of the B6 opt-out, so a truthy
    #     TOML string/int is config noise that reads like an explicit choice.
    lazy_retry = gate.get("lazy_retry")
    if lazy_retry is not None and not isinstance(lazy_retry, bool):
        raise ValueError(
            f"gate {name!r}: lazy_retry must be a boolean "
            f"(got {type(lazy_retry).__name__!r})"
        )

    # 11. autofix_unscoped must not be set at all (rule-9 path_filters style).
    #     Regression gates have no autofix path — rule 3 already rejects
    #     ``autofix`` itself — so the flag would be silently ignored config
    #     noise that reads like a meaningful opt-out.
    if gate.get("autofix_unscoped") is not None:
        raise ValueError(
            f"gate {name!r}: autofix_unscoped is not supported on regression "
            f"gates (no autofix path — rule 3 already rejects autofix itself)"
        )


def validate_config(config: dict) -> None:
    """Validate the loaded config; raise ``ValueError`` with a description on error.

    Hard rule: a non-empty ``autofix`` MUST contain ``{changed}``. An autofix
    that does not reference ``{changed}`` would run unconditionally over the
    entire working tree, bypassing the ``never_touch`` fence entirely. This is
    rejected at load time so the error is surfaced before any gate runs. A
    build-type autofix with no per-file form may opt out of the ``{changed}``
    containment EXPLICITLY and per gate (``autofix_unscoped = true``); the
    fence is not weakened for any gate that does not ask.
    """
    for gate in config.get("gate", []) or []:
        name = gate.get("name", "<unnamed>")

        # ``probe_port`` (spec §4F) is what ``probe_need`` actually probes for
        # the ``docker-db`` precondition — there is no hardcoded fallback (see
        # DOCKER_DB_HOST comment above), so any gate declaring
        # needs = ["docker-db"] MUST set it here, or the gate would silently
        # never be checked for DB reachability.
        probe_port = gate.get("probe_port")
        if probe_port is not None and (
            isinstance(probe_port, bool) or not isinstance(probe_port, int)
        ):
            raise ValueError(f"gate {name!r}: probe_port must be an integer")
        if "docker-db" in (gate.get("needs") or []) and probe_port is None:
            raise ValueError(
                f"gate {name!r}: needs = [\"docker-db\"] requires a 'probe_port' "
                f"field — add e.g. probe_port = 5432 directly to this gate in "
                f"gates.toml, matching the host port your docker-compose maps "
                f"for the DB service (no need to re-run regenloop-init; a plain text "
                f"edit of the existing gate block is enough)"
            )

        # Regression gates have their own rule set (spec §4A rules 0-7); they do
        # not share the command-gate autofix-{changed} rule.
        if str(gate.get("kind") or "") == "regression":
            _validate_regression_gate(gate)
            continue

        autofix = str(gate.get("autofix") or "").strip()
        # AR-1 (audit-recs-10-11): a build-type autofix (e.g. `cd ui && bun run
        # build`) has no per-file form, so the gate may opt out of the {changed}
        # containment fence EXPLICITLY and per-gate, here in config where the
        # choice is reviewable. The substring fence itself is not weakened for
        # any gate that does not ask.
        unscoped = gate.get("autofix_unscoped")
        # Strict bool (rule 8b style): a TOML string ("false") or int (1) is
        # truthy in Python and would silently ARM the {changed}-fence bypass
        # while reading like an explicit opt-out. Accept ONLY a real bool;
        # absent stays the no-flag path, False the explicit false.
        if unscoped is not None and not isinstance(unscoped, bool):
            raise ValueError(
                f"gate {name!r}: autofix_unscoped must be a boolean "
                f"(got {type(unscoped).__name__!r}); a truthy TOML string/int "
                f"would silently arm the {{changed}}-fence bypass the flag gates"
            )
        if unscoped and not autofix:
            raise ValueError(
                f"gate {name!r}: autofix_unscoped = true with an empty autofix is "
                f"config noise — drop the flag or set an autofix command"
            )
        # F11-T1: the flag only opts a {changed}-LESS autofix out of the
        # containment fence — the runtime never consults it when {changed} is
        # present, so on a {changed}-scoped autofix it is inert config noise
        # that reads like a meaningful opt-out.
        if unscoped and autofix and "{changed}" in autofix:
            raise ValueError(
                f"gate {name!r}: autofix_unscoped = true on an autofix that "
                f"already contains {{changed}} is config noise — the flag only "
                f"opts a {{changed}}-less autofix out of the containment fence; "
                f"drop the flag or the {{changed}} token"
            )
        if autofix and "{changed}" not in autofix and not unscoped:
            raise ValueError(
                f"gate {name!r}: autofix is non-empty but does not contain {{changed}}; "
                f"every autofix must be scoped to {{changed}} to respect the never_touch "
                f"fence, or set autofix_unscoped = true to opt a build-type autofix out "
                f"explicitly"
            )
        chdir = str(gate.get("chdir") or "").strip().rstrip("/")
        if chdir and chdir != ".":
            _validate_chdir_str(chdir, str(name))

        # Vacuous-check rejection (contract item 5): reject a command gate whose
        # entire check is a shell no-op ("true", ":", or a bare "echo ..." with
        # no pipe/&&/;). Placed AFTER the chdir check so an already-invalid
        # chdir is reported first (chdir's own tests use "true" as filler).
        check_cmd = str(gate.get("check") or "").strip()
        if check_cmd and _is_vacuous_check(check_cmd):
            raise ValueError(
                f"gate {name!r}: check {check_cmd!r} is a shell no-op "
                f"(true/:/bare echo) — every gate must run a real check"
            )

        # Validate path_filters structure if present (opt-in command-gate feature).
        # Rejection on regression gates is handled by _validate_regression_gate (rule 9).
        path_filters = gate.get("path_filters")
        if path_filters is not None:
            if not isinstance(path_filters, list):
                raise ValueError(
                    f"gate {name!r}: path_filters must be a list of tables, "
                    f"got {type(path_filters).__name__!r}"
                )
            for i, entry in enumerate(path_filters):
                if not isinstance(entry, dict):
                    raise ValueError(
                        f"gate {name!r}: path_filters[{i}] must be a table (dict)"
                    )
                # paths: non-empty list of non-empty strings.
                paths = entry.get("paths")
                if not isinstance(paths, list) or not paths:
                    raise ValueError(
                        f"gate {name!r}: path_filters[{i}].paths must be a "
                        f"non-empty list of strings"
                    )
                for j, p in enumerate(paths):
                    if not isinstance(p, str) or not p.strip():
                        raise ValueError(
                            f"gate {name!r}: path_filters[{i}].paths[{j}] must be "
                            f"a non-empty string"
                        )
                # select: non-empty string (not a list, not a bool).
                select = entry.get("select")
                if not isinstance(select, str) or not select.strip():
                    raise ValueError(
                        f"gate {name!r}: path_filters[{i}].select must be a "
                        f"non-empty string"
                    )
            # select_default, if present, must be a non-empty string.
            select_default = gate.get("select_default")
            if select_default is not None:
                if not isinstance(select_default, str) or not select_default.strip():
                    raise ValueError(
                        f"gate {name!r}: select_default must be a non-empty string "
                        f"(got {type(select_default).__name__!r})"
                    )
            # path_filters declared → {selected} MUST appear in the check command.
            # Without this, the runner silently runs the full suite (no injection
            # point), making path_filters a no-op that looks green but ignores scope.
            check_cmd = str(gate.get("check") or "").strip()
            if "{selected}" not in check_cmd:
                raise ValueError(
                    f"gate {name!r}: path_filters declared but {{selected}} not in check command; "
                    f"the runner cannot inject the selected tests"
                )


# --------------------------------------------------------------------------- #
# Report building
# --------------------------------------------------------------------------- #
def select_gates(config: dict, gate_filter: str | None) -> list[dict]:
    gates = config.get("gate", []) or []
    if gate_filter is not None:
        gates = [g for g in gates if str(g.get("name", "")) == gate_filter]
    return gates


def _execute_gates(
    runnable: list,
    *,
    jobs: int | None = None,
    sequential: bool = False,
) -> list[dict]:
    """Run the ordered list of zero-arg gate callables; return their result dicts.

    Gates are blocking ``subprocess.run`` calls (I/O-bound), so they release the
    CPython GIL during the wait and a ``ThreadPoolExecutor`` genuinely overlaps
    them in wall-clock time — the right primitive, not ``multiprocessing``.
    ``evaluate_gate`` / ``evaluate_regression_gate`` are pure ``(gate, ...) -> dict``
    with no shared mutable state (the only shared resource is ``run_command``'s
    per-call env + proc, and regression worktrees carry a unique ``run_id``), so
    concurrent execution is safe.

    Gate ORDER in the report is preserved either way: futures are submitted keyed
    by position and reassembled in submission (gates) order, so the JSON report's
    gate list is byte-for-byte identical to a sequential run — operators and
    tooling parse it positionally.

    ``--sequential`` (and any single-gate run) takes the plain list-comprehension
    path with zero thread-pool overhead: a 1-worker pool can only add cost.
    """
    if sequential or len(runnable) <= 1:
        return [fn() for fn in runnable]

    max_workers = jobs if (jobs and jobs > 0) else min(
        len(runnable), os.cpu_count() or 4
    )
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_by_pos = {pos: ex.submit(fn) for pos, fn in enumerate(runnable)}
        # Block on each in submission order; .result() re-raises any worker
        # exception. Gate evaluators catch their own (TimeoutExpired/OSError →
        # could_not_run), so this is purely defensive.
        return [future_by_pos[pos].result() for pos in range(len(runnable))]


def _heavy_fast_tier_findings(results: list[dict], budget: float) -> list[dict]:
    """Heavy fast-tier regression gates (B5, Phase 1; NON-BLOCKING diagnostic).

    A ``tier = "fast"`` regression gate that runs the FULL suite is the disease
    behind the originating complaint — it fires on every ``/green-gate``
    iteration AND every pre-push, pegging cores for 5–8 min. This scan names the
    remedy: every fast-tier regression gate whose measured ``branch_run_s``
    exceeds ``budget`` is recorded as a finding.

    Pure additive observability — the returned list is metadata only, never read
    by verdict / exit-code logic. Only gates that actually ran
    (``status in {"pass","fail"}``) are considered; ``could_not_run`` / ``skip``
    carry no measurement and produce no finding. ``branch_run_s`` is rounded to
    one decimal place for readable reports.
    """
    findings: list[dict] = []
    for r in results:
        if r.get("kind") != "regression" or r.get("tier") != "fast":
            continue
        if r.get("status") not in ("pass", "fail"):
            continue
        branch_run_s = (r.get("timing") or {}).get("branch_run_s")
        # bool is an int subclass; reject it explicitly so True/False (which
        # would arise only from a malformed timing block) never compare as 1/0.
        if isinstance(branch_run_s, bool) or not isinstance(branch_run_s, (int, float)):
            continue
        if branch_run_s > budget:
            findings.append({
                "name": r.get("name"),
                "branch_run_s": round(float(branch_run_s), 1),
                "budget_s": budget,
                "remedy": (
                    'demote to tier="full" (Part A: no longer fires on /green-gate '
                    "or pre-push) or adopt a scoped fast command gate (the split)"
                ),
            })
    return findings


def build_check_report(
    config: dict,
    base: str,
    changed: list[str],
    cwd: str,
    timeout: float | None,
    gate_filter: str | None = None,
    *,
    tier: str = "fast",
    no_baseline: bool = False,
    base_sha: str | None = None,
    allow_meta_changes: bool = False,
    forbid_fenced: bool = False,
    require_evidence: bool = False,
    config_path: str | None = None,
    jobs: int | None = None,
    sequential: bool = False,
    safe_mode: bool = False,
) -> dict:
    """Run every applicable gate's check and assemble the three-state report.

    ``tier``, ``no_baseline``, and ``base_sha`` are consumed only by
    ``kind = "regression"`` gates: a ``RunContext`` is constructed per regression
    gate (command gates never receive one, preserving their call signature). In
    ``--tier fast`` (default), ``tier = "full"`` regression gates are skipped;
    ``--tier full`` is a superset that runs them too. A skipped full-tier gate
    still contributes its ``when`` matches to coverage (it is covered by a gate
    definition, just not executed at this tier) so it does not create a phantom
    coverage gap.

    ``allow_meta_changes`` and ``forbid_fenced`` implement the two hardening
    fences (contract items 1-2): both can force the verdict to ``fail``
    independent of per-gate results — see ``meta_config_changed`` /
    ``coverage.fenced_changed_files`` in the returned report.

    Parallelism: by default the applicable gates run concurrently in a
    ``ThreadPoolExecutor`` (see ``_execute_gates``). ``--sequential`` disables it
    and ``--jobs N`` caps the pool size; both leave verdict and gate order
    unchanged. ``--apply-autofix`` is unaffected — autofix is always serial.

    ``safe_mode`` additionally makes every gate wait for a machine-global turn
    through ``regenloop_guard`` before spawning (command gates per check,
    regression gates once around the whole baseline+branch evaluation), so five
    parallel sessions on one laptop no longer all run heavy suites at once.
    """
    never_touch = config.get("never_touch", []) or []
    gates = select_gates(config, gate_filter)

    # First pass (main thread, deterministic): for every gate, record its
    # `when` matches into `matched` (coverage is tier-independent — a skipped
    # full-tier gate still counts as "covered by a gate definition") and decide
    # whether it will *execute*. Executing gates are appended to `runnable` IN
    # GATES ORDER, so the reassembly in _execute_gates reproduces the exact
    # sequential-report ordering operators rely on. The matched-set update is
    # derived from `changed`, not from gate output, so it stays here in the main
    # thread — no shared mutable state crosses a worker boundary.
    runnable: list = []
    matched: set[str] = set()
    # F2: gate names from the FULL loaded config — NEVER the select_gates-
    # filtered ``gates`` list — so a ``--gate``-scoped run must not sweep
    # other configured gates' stable branch JUnit files in the orphan sweep.
    # Built once here: identical for every regression gate's RunContext.
    configured_gate_names = {
        str(g.get("name") or "") for g in (config.get("gate", []) or [])
    }
    for gate in gates:
        when = gate.get("when", [])
        applicable = [f for f in changed if matches_any(f, when)]
        matched.update(applicable)   # coverage is tier-independent

        # When --gate explicitly names a gate, run it unconditionally: ``when``
        # and ``--tier`` are automatic-selection filters only. An explicit
        # --gate request overrides both (the branch run still uses the working
        # tree, so an uncommitted failing test is visible — the TDD red phase
        # is observable even when the test file is not yet committed).
        if not applicable and gate_filter is None:
            continue

        if str(gate.get("kind") or "") == "regression":
            # Tier selection: fast runs fast-tier gates only; full is a superset.
            # Bypassed when --gate explicitly names the gate.
            if gate_filter is None and tier == "fast" and gate.get("tier") == "full":
                continue
            # RunContext is built per regression gate in the main thread (its
            # fields are run-scoped, not mutated by the gate) and captured by
            # value. The default-arg binding (g=gate, rc=run_ctx) captures the
            # loop variable per-iteration, not its final value — the classic
            # late-binding-closure gotcha that would otherwise make every
            # worker run the LAST gate.
            # AR-3 seam: the regression engine lives in the sibling carve module,
            # imported LAZILY here so command-gate-only repos never pay for it.
            # Attribute access (never from-import) keeps the suite's direct
            # patches on THIS module (gr.run_command = fn) intercepting
            # regression-path calls. Do not "fix" into a from-import.
            _reg = _carve_module()
            run_ctx = _reg.RunContext(
                tier=tier,
                no_baseline=no_baseline,
                repo_root=cwd,
                base_sha=base_sha if base_sha is not None else base,
                timeout=timeout,
                configured_gate_names=configured_gate_names,
                # t5 (D3): the carve module learns safe mode through the ctx
                # (never new positional params) — evaluate_regression_gate
                # derives the per-anchor worker ceiling from it.
                safe_mode=safe_mode,
            )
            if safe_mode:
                runnable.append(
                    lambda g=gate, rc=run_ctx, nm=str(gate.get("name", "<unnamed>")):
                        _run_regression_gate_via_guard(g, rc, nm)
                )
            else:
                runnable.append(
                    lambda g=gate, rc=run_ctx: _reg.evaluate_regression_gate(g, rc)
                )
        else:
            runnable.append(
                lambda g=gate: evaluate_gate(
                    g, changed, never_touch, base, cwd, timeout, safe_mode=safe_mode
                )
            )

    # Parallel by default; --sequential or a single gate takes the inline path.
    # Gate order in `results` matches `runnable` (== gates order) either way.
    _run_t0 = time.monotonic()
    results = _execute_gates(runnable, jobs=jobs, sequential=sequential)
    total_wall_s = time.monotonic() - _run_t0

    # B1 (Phase 1): run-level timing rollup (pure additive). Gates run
    # concurrently by default, so total_wall_s is the real elapsed wall, not a
    # sum of per-gate wall_s. The cache/cow counts are computed generally from
    # each result's timing block so Phase 3 (B9/B10) needs no change here.
    # Command-gate timing blocks carry only wall_s (no baseline_cache_hit /
    # cow_unavailable_reason keys), so they are excluded from both counts; only
    # regression results (whose timing blocks key these fields) contribute.
    _reg_timing = [
        r["timing"] for r in results
        if isinstance(r.get("timing"), dict)
    ]
    baseline_cache_hits = sum(
        1 for t in _reg_timing if t.get("baseline_cache_hit") is True
    )
    baseline_cache_misses = sum(
        1 for t in _reg_timing if t.get("baseline_cache_hit") is False
    )
    # cow_hits / cow_misses (B10 review B3): re-keyed on install_reused (True
    # == a real CoW clone replaced the install). Post-B10, cow_unavailable_reason
    # is None for EVERY regression gate that didn't attempt CoW (no
    # baseline_install_reuse field, cache hit), so keying on it inflated the
    # count. cow_misses counts gates that attempted CoW but failed (non-None
    # cow_unavailable_reason). Observability-only — verdict/exit untouched.
    cow_hits = sum(
        1 for t in _reg_timing
        if "install_reused" in t and t["install_reused"] is True
    )
    cow_misses = sum(
        1 for t in _reg_timing
        if "cow_unavailable_reason" in t and t["cow_unavailable_reason"] is not None
    )
    run_timing = {
        "total_wall_s": total_wall_s,
        "baseline_cache_hits": baseline_cache_hits,
        "baseline_cache_misses": baseline_cache_misses,
        "cow_hits": cow_hits,
        "cow_misses": cow_misses,
    }

    # B5 (Phase 1): heavy fast-tier diagnostic (NON-BLOCKING). A fast-tier
    # regression gate that runs the full suite fires on every /green-gate
    # iteration AND every pre-push — the disease behind the originating
    # complaint. The scan names the remedy; verdict / exit code are untouched.
    heavy_fast_tier = _heavy_fast_tier_findings(results, _fast_tier_budget())

    # When --gate is set, coverage is scoped to the files that gate's `when`
    # could plausibly match (files outside the gate's scope are not in its
    # "universe" for coverage purposes, so they never force it non-green).
    if gate_filter is not None:
        all_whens: list[str] = []
        for gate in gates:
            all_whens.extend(gate.get("when", []))
        coverage_universe = [f for f in changed if matches_any(f, all_whens)]
    else:
        coverage_universe = changed

    unmatched = [f for f in coverage_universe if f not in matched]

    # Surface fenced files that changed — they are deliberately not auto-touched
    # locally and are covered via CI / code review. Exposed here so a human can
    # see "sensitive files changed; verify via CI". Does NOT affect the verdict.
    fenced = [f for f in changed if matches_any_nocase(f, never_touch)]

    # "skip" is verdict-neutral: a gate with path_filters that matched no changed
    # files (and has no select_default) is intentionally not run.  It contributes
    # no test evidence but also creates no failure, so it does not block green.
    # "could_not_run" and "fail" still block green as before.
    all_pass = all(r["status"] in ("pass", "skip", "skipped_no_files") for r in results)
    green = all_pass and not unmatched

    # Meta-config tamper fence (item 1, highest priority): a diff that edits
    # the gates config it is being checked against forces a fail regardless of
    # gate results, unless explicitly allowed.
    meta_changed = meta_config_changed(changed)
    # Fence fail-closed mode (item 2): opt-in — advisory by default.
    fence_violations = list(fenced) if (forbid_fenced and fenced) else []

    verdict = "green" if green else "fail"
    if meta_changed and not allow_meta_changes:
        verdict = "fail"
    if fence_violations:
        verdict = "fail"

    # Report freshness (item 8): lets downstream tooling verify a report
    # matches the commit it claims to describe.
    head_sha_proc = _git(["rev-parse", "HEAD"], cwd)
    head_sha = head_sha_proc.stdout.strip() if head_sha_proc.returncode == 0 else None

    # Which gates.toml actually governed this run, and whether it diverges from
    # the committed one the tamper fence inspects.
    config_sha, config_dirty = config_identity(config_path, cwd)

    # Evidence accounting: a report must record what it actually examined, so a
    # run that checked nothing is distinguishable from a run that checked
    # everything and passed. `all(...)` over an empty result list is True, so an
    # empty diff yields green — legitimate pre-MR behaviour, but previously
    # indistinguishable from real coverage. Verdict semantics are unchanged
    # here; see --require-evidence for callers that must treat zero evidence as
    # fatal.
    gates_executed = sum(1 for r in results if r["status"] in _EXECUTED_STATUSES)
    gates_skipped = len(results) - gates_executed

    # Zero-evidence guard: opt-in, because a clean pre-MR tree legitimately
    # matches no gates and green is the right answer there. Callers that enforce
    # (the pre-push hook, per-task gates) pass --require-evidence so a run that
    # verified nothing cannot satisfy them. Escalation only — it never turns a
    # genuine fail into a pass.
    if require_evidence and gates_executed == 0 and verdict == "green":
        verdict = "no_evidence"

    report: dict = {
        "base": base,
        "verdict": verdict,
        "gates": results,
        "coverage": {
            "unmatched_changed_files": unmatched,
            "fenced_changed_files": fenced,
        },
        "head_sha": head_sha,
        "config_sha": config_sha,
        "config_dirty": config_dirty,
        "evidence": {
            "gates_executed": gates_executed,
            "gates_skipped": gates_skipped,
            "changed_file_count": len(changed),
            "scoped": gate_filter is not None,
        },
        # B1 (Phase 1): run-level timing rollup (pure additive metadata; never
        # read by verdict/exit-code logic).
        "timing": run_timing,
    }
    if meta_changed:
        report["meta_config_changed"] = meta_changed
        # Lets print_check_summary branch the banner: distinct wording when the
        # forced fail was actually suppressed via --allow-meta-changes, vs. when
        # it is still in force (item 2 — the banner previously printed the
        # identical "pass --allow-meta-changes to suppress" text either way).
        report["meta_config_change_allowed"] = allow_meta_changes
    if fence_violations:
        report["fence_violations"] = fence_violations
    if heavy_fast_tier:
        report["heavy_fast_tier"] = heavy_fast_tier
    return report


def build_autofix_report(
    config: dict,
    base: str,
    changed: list[str],
    cwd: str,
    timeout: float | None,
    gate_filter: str | None = None,
    *,
    safe_mode: bool = False,
) -> dict:
    """Apply autofixes for the selected applicable gates.

    Always SEQUENTIAL by design — autofix commands mutate the working tree in
    place, so running them concurrently would race on shared files (two fixers
    rewriting the same path, or one fixer's output becoming the next fixer's
    input). ``--jobs`` / ``--sequential`` therefore have NO effect on autofix:
    it ignores the parallel scheduler entirely. Only the *check* loop
    (``build_check_report``) parallelizes.
    """
    never_touch = config.get("never_touch", []) or []
    gates = select_gates(config, gate_filter)

    actions: list[dict] = []
    for gate in gates:
        when = gate.get("when", [])
        applicable = any(matches_any(f, when) for f in changed)
        if not applicable:
            if gate_filter is not None:
                actions.append(
                    {"name": str(gate.get("name", "<unnamed>")),
                     "status": "skipped", "reason": "gate not applicable to changed set"}
                )
            continue
        actions.append(autofix_gate(gate, changed, never_touch, base, cwd, timeout,
                                    safe_mode=safe_mode))

    return {"base": base, "mode": "autofix", "gate": gate_filter, "autofixes": actions}


# --------------------------------------------------------------------------- #
# TDD RED/GREEN evidence verifier (contract item 9) — standalone mode
# --------------------------------------------------------------------------- #
def _load_report_json(path: str) -> dict | None:
    """Load a gate_runner JSON report; ``None`` on any read/parse failure."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _sha_resolves(sha: str, cwd: str) -> bool:
    """True iff ``sha`` names a commit object that exists in the repo at ``cwd``."""
    return _git(["cat-file", "-e", f"{sha}^{{commit}}"], cwd).returncode == 0


def _is_ancestor(ancestor: str, descendant: str, cwd: str) -> bool:
    """True iff ``ancestor`` is an ancestor of (or equal to) ``descendant``."""
    return _git(["merge-base", "--is-ancestor", ancestor, descendant], cwd).returncode == 0


def _gate_text(gate: dict) -> str:
    """Concatenate a gate's ``items`` entries and ``summary`` for substring search."""
    items = gate.get("items") or []
    return " ".join(str(i) for i in items) + " " + str(gate.get("summary") or "")


def verify_tdd(
    red_path: str,
    green_path: str,
    expect_test: str | None = None,
    cwd: str | None = None,
    require_lineage: bool = False,
) -> tuple[int, str]:
    """Verify RED/GREEN TDD evidence from two gate_runner JSON reports.

    RED requirement (satisfied by EITHER path):
      - a regression-kind gate with ``status == "fail"`` and a non-empty
        ``new_and_failing`` (a new test was written and is failing), OR
      - when ``expect_test`` is given: any gate with ``status == "fail"`` whose
        ``items`` entries or ``summary`` contain the ``expect_test`` substring
        (the new test's id). This makes receipts satisfiable in command-gate-only
        repos, which have no regression gate at all. Regression evidence, when
        present, is preferred/sufficient — the two paths compose.

    GREEN requirement:
      - ``verdict == "green"`` AND every regression-kind gate has an empty
        ``regressions`` list. When the RED requirement was satisfied via
        ``expect_test``, the same-named gate(s) must additionally show
        ``status == "pass"`` in GREEN.

    Lineage: when BOTH reports carry a ``head_sha`` that is present and resolves
    in the repo at ``cwd``, GREEN's head must descend from OR equal RED's head
    (``git merge-base --is-ancestor``).

    When a head_sha is present but does not resolve — git absent from PATH, or
    reports produced in another checkout — the lenient fallback applies: the
    reports' ``base`` fields must match, and the PASS message notes lineage was
    unverified.

    When a head_sha is ABSENT altogether, ``require_lineage`` (the CLI default)
    fails instead. Every real gate_runner report stamps the key, so its absence
    means the report was not produced by a gate run — and the lenient path only
    checks ``base``, an arbitrary string, which two hand-authored JSON files
    satisfy. This is the mechanism behind "no test, no done", and the implementer
    producing the receipts can write files, so a bare omission must not pass.
    ``--allow-unverified-lineage`` opts out.

    Exit 1 with a one-line reason for any other well-formed pair of reports.
    Exit 2 if either file is missing, unreadable, or not a JSON object.
    Returns ``(exit_code, message)``; the caller prints ``message`` once,
    either way.
    """
    if cwd is None:
        cwd = str(Path.cwd())

    red = _load_report_json(red_path)
    if red is None:
        return 2, f"verify-tdd: could not read/parse RED report at {red_path}"
    green = _load_report_json(green_path)
    if green is None:
        return 2, f"verify-tdd: could not read/parse GREEN report at {green_path}"

    red_gates = red.get("gates", []) or []
    red_ok_regression = any(
        g.get("kind") == "regression"
        and g.get("status") == "fail"
        and g.get("new_and_failing")
        for g in red_gates
    )
    expect_gate_names: set[str] = set()
    if expect_test:
        for g in red_gates:
            if g.get("status") == "fail" and expect_test in _gate_text(g):
                expect_gate_names.add(g.get("name"))
    red_ok_expect = bool(expect_gate_names)

    if not (red_ok_regression or red_ok_expect):
        if expect_test:
            return 1, (
                "verify-tdd: FAIL — RED report has no failing gate whose items/summary "
                f"contain the --expect-test substring {expect_test!r}, and no regression "
                "gate with status=fail and a non-empty new_and_failing"
            )
        return 1, (
            "verify-tdd: FAIL — RED report has no regression gate with "
            "status=fail and a non-empty new_and_failing (in a command-gate-only "
            "repo, pass --expect-test <new-test-id> to match a failing command gate)"
        )

    if green.get("verdict") != "green":
        return 1, "verify-tdd: FAIL — GREEN report verdict is not 'green'"

    green_gates = green.get("gates", []) or []
    green_clean = all(
        not g.get("regressions") for g in green_gates if g.get("kind") == "regression"
    )
    if not green_clean:
        return 1, (
            "verify-tdd: FAIL — GREEN report has a non-empty regressions list "
            "on a regression gate"
        )

    # When the RED requirement was satisfied via --expect-test, the same gate(s)
    # that were failing in RED must now be passing in GREEN.
    if red_ok_expect:
        green_by_name = {g.get("name"): g for g in green_gates}
        for name in sorted(expect_gate_names):
            gg = green_by_name.get(name)
            if gg is None or gg.get("status") != "pass":
                got = "absent" if gg is None else gg.get("status")
                return 1, (
                    f"verify-tdd: FAIL — GREEN report gate {name!r} (which showed the "
                    f"--expect-test failure in RED) is not passing (status: {got}); "
                    "the gate that was red must be green"
                )

    # Evidence quality of the GREEN report. Neither check is about lineage: a
    # report scoped to one gate (--gate) never examined the rest of the diff, and
    # a report that executed no gate examined nothing at all. Either way it
    # cannot stand as the GREEN half of a TDD receipt.
    green_evidence = green.get("evidence") or {}
    if green_evidence.get("scoped"):
        return 1, (
            "verify-tdd: FAIL — GREEN report is scoped to a single gate (--gate); "
            "a scoped report cannot stand for the whole diff"
        )
    if green_evidence.get("gates_executed") == 0:
        return 1, (
            "verify-tdd: FAIL — GREEN report executed no gate, so it carries no "
            "evidence that anything passed"
        )

    # Lineage assurance (Defect 1).
    red_head = red.get("head_sha")
    green_head = green.get("head_sha")
    heads_resolvable = bool(
        red_head and green_head
        and _sha_resolves(red_head, cwd) and _sha_resolves(green_head, cwd)
    )
    if heads_resolvable:
        if red_head != green_head and not _is_ancestor(red_head, green_head, cwd):
            return 1, (
                f"verify-tdd: FAIL — GREEN head {green_head[:12]} does not descend from "
                f"RED head {red_head[:12]} (if you rebased/amended between RED and GREEN, "
                "re-run the RED gate)"
            )
        lineage_note = f"lineage verified (GREEN {green_head[:12]} descends from RED {red_head[:12]})"
    elif require_lineage and not (red_head and green_head):
        # Strict mode (the CLI default). The lenient branch below accepts a pair
        # whose only tie to reality is a matching ``base`` string — and ``base``
        # can be any string, so two hand-authored JSON files satisfy it. That is
        # the whole mechanism behind "no test, no done", and the implementer that
        # produces these receipts has Write access. Absent or unresolvable
        # head_shas therefore fail here rather than passing with a footnote.
        return 1, (
            "verify-tdd: FAIL — a report carries no head_sha, so it cannot be tied "
            "to any commit. Every real gate_runner report stamps one; a report "
            "without it was not produced by a gate run. Re-run the gates, or pass "
            "--allow-unverified-lineage to accept receipts you cannot verify"
        )
    else:
        if red.get("base") != green.get("base"):
            return 1, "verify-tdd: FAIL — RED and GREEN reports have different 'base' fields"
        lineage_note = "lineage unverified (head_shas absent or not resolvable here)"

    return 0, f"verify-tdd: PASS — RED showed a failing new test; GREEN is clean; {lineage_note}"


# --------------------------------------------------------------------------- #
# Ledger wiring (contract item 7) — best-effort, never affects the verdict
# --------------------------------------------------------------------------- #
def append_ledger_entry(
    report: dict, ledger_path: str, source: str,
    *, run_id: str | None = None, tier: str | None = None,
) -> None:
    """Append a ledger entry for a check-mode ``report``; failures are non-fatal.

    ``ledger.append_entry`` (owned by another module — used as-is, not edited)
    reads the report from a JSON file on disk, so the report is written to a
    private temp file first (removed afterwards) rather than requiring
    ``--json`` to already have been passed. Any failure — a bad ledger path,
    ``ledger.append_entry``'s own ``SystemExit(1)``, a stray exception — is
    caught and printed as a stderr warning; it must never change the verdict
    already computed and emitted for this run.
    """
    import tempfile

    tmp_path: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix="gate-runner-ledger-", suffix=".json")
        os.close(fd)
        tmp_path = Path(tmp_name)
        tmp_path.write_text(json.dumps(report))
        ledger.append_entry(
            tmp_path, Path(ledger_path), source=source, run_id=run_id, tier=tier,
        )
    except SystemExit as exc:
        print(f"gate_runner: warning: ledger append failed (exit {exc.code})", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - must never affect the already-decided verdict
        print(f"gate_runner: warning: ledger append failed: {exc}", file=sys.stderr)
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def print_check_summary(report: dict) -> None:
    print(f"base:    {report['base']}")
    print(f"verdict: {report['verdict']}")
    meta_changed = report.get("meta_config_changed")
    if meta_changed:
        print("  " + "!" * 66)
        print(
            "  ! GATE CONFIG CHANGED IN THIS DIFF — the gates config itself was "
            "edited:"
        )
        for f in meta_changed:
            print(f"  !   {f}")
        if report.get("meta_config_change_allowed"):
            print(
                "  ! The forced fail was suppressed via --allow-meta-changes — "
                "human review is still expected."
            )
        else:
            print(
                "  ! This requires human review (pass --allow-meta-changes to "
                "suppress the forced fail)."
            )
        print("  " + "!" * 66)
    fence_violations = report.get("fence_violations")
    if fence_violations:
        print(f"  --forbid-fenced blocked green: {len(fence_violations)} fenced file(s) changed:")
        for f in fence_violations:
            print(f"      {f}")
    for g in report["gates"]:
        detail = g.get("reason") or g.get("summary") or ""
        line = f"  [{g['status']:>13}] {g['name']}"
        if detail:
            line += f" — {detail}"
        print(line)
        if g.get("hint"):
            print(f"                  hint: {g['hint']}")
    coverage = report.get("coverage", {})
    unmatched = coverage.get("unmatched_changed_files", [])
    if unmatched:
        print(f"  coverage gap ({len(unmatched)} unmatched changed file(s)):")
        for f in unmatched:
            print(f"      {f}")
    fenced = coverage.get("fenced_changed_files", [])
    if fenced:
        print(f"  fenced files changed ({len(fenced)} — not auto-touched; verify via CI/review):")
        for f in fenced:
            print(f"      {f}")


def print_autofix_summary(report: dict) -> None:
    scope = report["gate"] or "all gates"
    print(f"base:   {report['base']}")
    print(f"autofix scope: {scope}")
    for a in report["autofixes"]:
        detail = a.get("reason") or a.get("command") or ""
        print(f"  [{a['status']:>8}] {a['name']}" + (f" — {detail}" if detail else ""))


def emit(report: dict, json_path: str | None, summary_fn) -> None:
    summary_fn(report)
    blob = json.dumps(report, indent=2)
    print(blob)
    if json_path:
        Path(json_path).parent.mkdir(parents=True, exist_ok=True)
        Path(json_path).write_text(blob + "\n")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _is_loop_kit_served_dir(path: Path) -> bool:
    """True only when *path* carries BOTH gates.toml and knowledge/INDEX.md.

    A bare-name match on ``loop-kit/gates.toml`` alone would misfire on an
    unrelated directory that happens to share the plugin's old name and merely
    contains some gates.toml — this 2-artifact minimum (standalone here;
    deliberately not importing regenloop_init to keep this script standalone)
    is **stricter than** the content-signature guarding used elsewhere in the
    plugin (regenloop_init.py's ``_is_regenloop_served_dir``, which accepts
    ANY ONE of 5 signature artifacts) before routing the operator into the
    migrate-brand hint. The two helpers are deliberately not shared logic —
    don't assume they'll always agree.
    """
    return (path / "gates.toml").exists() and (path / "knowledge" / "INDEX.md").exists()


def _resolve_gates_path(explicit: Path | None, repo_root: Path) -> Path:
    """Resolve the gates.toml path with back-compat fallback (spec §7).

    Branch resolution order:
      (a) explicit flag given → return as-is
      (b) regenloop/gates.toml exists → return silently
      (c) .claude/gates.toml exists → return with deprecation warning on stderr
      (d) neither → raise FileNotFoundError naming the expected location
    """
    if explicit is not None:
        return explicit
    candidate = repo_root / "regenloop" / "gates.toml"
    if candidate.exists():
        return candidate
    candidate = repo_root / ".claude" / "gates.toml"
    if candidate.exists():
        print(
            "[regenloop] DEPRECATED: gates.toml found at .claude/gates.toml — "
            "run `regenloop_init.py migrate <repo_root>` to move it to regenloop/gates.toml",
            file=sys.stderr,
        )
        return candidate
    if _is_loop_kit_served_dir(repo_root / "loop-kit"):
        # Pre-rename served dir: the repo was onboarded under the plugin's old
        # name and hasn't been brand-migrated yet — point at the one command
        # that fixes everything, not just this path.
        raise FileNotFoundError(
            "gates.toml not found at regenloop/gates.toml, but loop-kit/gates.toml "
            "exists — this repo was onboarded under the plugin's old name. "
            "Run /regenloop-migrate (regenloop_init.py migrate-brand <repo_root>) "
            "to migrate the served directory, then retry."
        )
    raise FileNotFoundError(
        "gates.toml not found — expected at regenloop/gates.toml (or pass --config)"
    )


def load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config not found: {path}")
    with p.open("rb") as fh:
        return tomllib.load(fh)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gate_runner.py",
        description=(
            "Deterministic, zero-dependency gate runner for regenloop. Runs the "
            "checks defined in gates.toml against files changed vs a base, and "
            "reports each gate as pass | fail | could_not_run with a coverage "
            "block. Exit 0 only when the verdict is green."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--base",
        required=False,
        default=None,
        metavar="REF",
        help="Base git ref/sha to diff HEAD against. If it names a branch, the "
             "effective base is `git merge-base HEAD <branch>`. Required unless "
             "--verify-tdd is used.",
    )
    parser.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help="Path to the gates.toml config (default: regenloop/gates.toml (fallback: .claude/gates.toml, deprecated)).",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        metavar="PATH",
        help="Also write the JSON report to this path.",
    )
    parser.add_argument(
        "--apply-autofix",
        action="store_true",
        help="Run gates' autofix commands over {changed} instead of checking. "
             "Re-run without this flag to re-check. A gate may opt a build-type "
             "autofix out of the {changed} containment explicitly "
             "(autofix_unscoped = true); every other autofix stays "
             "{changed}-scoped.",
    )
    parser.add_argument(
        "--gate",
        metavar="NAME",
        help="Restrict to a single gate by name (selection, checks, autofix, "
             "and coverage are all scoped to it).",
    )
    parser.add_argument(
        "--timeout",
        type=_timeout_seconds_arg,
        default=_default_check_timeout(),
        metavar="SECONDS",
        help="Per-command timeout; on expiry a gate is could_not_run. Default is "
             "900s (bounded) when $REGENLOOP_GATE_TIMEOUT_S is unset; set it to "
             "0 or empty for unbounded. An explicit --timeout SECONDS wins and "
             "must be positive and finite.",
    )
    parser.add_argument(
        "--tier",
        choices=("fast", "full"),
        default="fast",
        help="Which regression tier to run. 'fast' (default) runs command gates "
             "plus fast-tier regression gates; 'full' is a superset that also "
             "runs full-tier regression gates. Command gates always run.",
    )
    parser.add_argument(
        "--no-baseline",
        action="store_true",
        help="Skip baseline worktree creation and cache lookups for regression "
             "gates; every branch failure is reported as a regression "
             "(baseline_skipped: true).",
    )
    parser.add_argument(
        "--allow-meta-changes",
        action="store_true",
        help="Suppress the forced fail when the changed set includes the gates "
             "config itself (regenloop/gates.toml or .claude/gates.toml). The "
             "meta_config_changed report field is still populated.",
    )
    parser.add_argument(
        "--forbid-fenced",
        action="store_true",
        help="Force a fail when coverage.fenced_changed_files is non-empty "
             "(adds top-level fence_violations). Without this flag, fenced "
             "changes remain advisory only.",
    )
    parser.add_argument(
        "--allow-dirty-autofix",
        action="store_true",
        help="Allow --apply-autofix to rewrite files that have uncommitted "
             "changes. Refused by default: reverting an autofix discards the "
             "operator's own edits in those files too.",
    )
    parser.add_argument(
        "--require-evidence",
        action="store_true",
        help="Fail (verdict 'no_evidence', exit 1) when no gate actually "
             "executed. Use wherever a vacuous green is dangerous — the "
             "pre-push hook, per-task gates. Off by default because a clean "
             "pre-MR tree legitimately matches no gates.",
    )
    parser.add_argument(
        "--allow-default-branch",
        action="store_true",
        help="Allow --apply-autofix (or, with --forbid-default-branch, plain "
             "check mode) to run while the current branch is main/master.",
    )
    parser.add_argument(
        "--forbid-default-branch",
        action="store_true",
        help="Apply the same default-branch refusal that --apply-autofix "
             "always gets to plain check mode too (opt-in).",
    )
    parser.add_argument(
        "--ledger",
        default=None,
        metavar="PATH",
        help="Append a ledger entry for this check-mode run via ledger.py "
             "(no effect in --apply-autofix mode). A failure to append is a "
             "stderr warning only — it never changes the verdict.",
    )
    parser.add_argument(
        "--ledger-run-id",
        default=None,
        metavar="ID",
        help="Correlate this report's ledger entry with the other tier's entry "
             "from the same logical run, so catch rate counts the run once.",
    )
    parser.add_argument(
        "--ledger-source",
        default="gate-runner",
        metavar="STR",
        help="Source identifier recorded in the ledger entry (default: "
             "'gate-runner').",
    )
    parser.add_argument(
        "--verify-tdd",
        nargs=2,
        metavar=("RED_JSON", "GREEN_JSON"),
        default=None,
        help="Standalone mode: verify RED/GREEN TDD evidence from two "
             "gate_runner JSON reports (mutually exclusive with --base). "
             "Exit 0 iff RED shows a failing new test and GREEN is clean "
             "against the same base, with RED's commit an ancestor of GREEN's. "
             "Exit 3 if this repo's gate configuration cannot produce such "
             "receipts at all (distinct from exit 1, fake receipts).",
    )
    parser.add_argument(
        "--allow-unverified-lineage",
        action="store_true",
        help="Accept RED/GREEN receipts whose head_shas are absent or do not "
             "resolve here (e.g. reports produced in another checkout). Off by "
             "default: without lineage, two hand-written JSON files satisfy "
             "--verify-tdd.",
    )
    parser.add_argument(
        "--expect-test",
        metavar="SUBSTR",
        default=None,
        help="For --verify-tdd in command-gate-only repos (no regression gate): "
             "a RED report also satisfies the red requirement if some gate has "
             "status=fail whose items/summary contain this substring (the new "
             "test's id); GREEN must then show that same gate passing.",
    )
    parser.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=None,
        metavar="N",
        help="Number of gates to run concurrently in check mode (default: "
             "min(applicable_gates, cpu_count or 4)). Gates are blocking "
             "subprocesses, so a thread pool overlaps their wall-clock time "
             "without changing results. Ignored by --sequential and by "
             "--apply-autofix (autofix is always serial).",
    )
    parser.add_argument(
        "--sequential",
        action="store_true",
        help="Run gates one-at-a-time instead of the default thread-pool "
             "execution. The JSON report (verdict + gate order + statuses) is "
             "identical either way — this is a wall-clock / determinism knob. "
             "No effect on --apply-autofix (autofix is always serial).",
    )
    parser.add_argument(
        "--pytest-parallel",
        choices=("auto", "off"),
        default=None,
        help="Auto-parallelize any pytest check with pytest-xdist by appending "
             "``-n auto`` (one worker per CPU core). 'auto' is the default WHEN "
             "pytest-xdist is importable by the gate's interpreter; injection is "
             "skipped silently when xdist is absent, so a missing dep never turns "
             "a pass into a fail. 'off' disables it. For an explicit worker cap, "
             "use $REGENLOOP_PYTEST_JOBS=N instead (e.g. 4) — the env var takes "
             "precedence over this flag. No effect on non-pytest gates or autofix.",
    )
    parser.add_argument(
        "--safe",
        action="store_true",
        help="Machine-global resource admission control: every gate check, "
             "autofix and regression baseline/branch run waits for a turn "
             "through regenloop_guard.py before spawning, and pytest/vitest "
             "worker counts plus the gate thread-pool width fall back to "
             "conservative defaults. For running several sessions on one "
             "machine overnight without an OOM. $REGENLOOP_SAFE=1 enables it "
             "for a whole shell; explicit operator values always win over the "
             "safe-mode defaults.",
    )
    parser.add_argument(
        "--no-safe",
        action="store_true",
        help="Force safe mode OFF even when $REGENLOOP_SAFE=1 is exported. "
             "This is the ONLY way to turn it off once the env var is set — "
             "$REGENLOOP_SAFE=0 never overrides an explicit --safe.",
    )
    return parser


def _resolve_safe_mode(explicit_safe: bool, explicit_no_safe: bool) -> bool:
    """Fail-safe OR-to-enable: either the flag or the env var turns safe mode on.

    Only an explicit ``--no-safe`` turns it off, so an operator can adopt safe
    mode for a whole CLI home by exporting ``$REGENLOOP_SAFE=1`` and still opt
    one invocation out.
    """
    if explicit_no_safe:
        return False
    if explicit_safe:
        return True
    return (os.environ.get("REGENLOOP_SAFE") or "").strip() == "1"


def _safe_gate_jobs() -> int:
    """Safe mode's gate thread-pool width. ``$REGENLOOP_SAFE_GATE_JOBS``, default 2."""
    try:
        value = int((os.environ.get("REGENLOOP_SAFE_GATE_JOBS") or "2").strip())
    except ValueError:
        return 2
    return value if value >= 1 else 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # --pytest-parallel feeds the same resolver evaluate_gate reads, via the
    # env var (setdefault: an exported $REGENLOOP_PYTEST_JOBS wins over the
    # flag, so power-users can pin a worker count without editing the call).
    if args.pytest_parallel is not None:
        os.environ.setdefault("REGENLOOP_PYTEST_JOBS", args.pytest_parallel)

    # --verify-tdd is a standalone mode: no repo, no config, no --base needed
    # (and mutually exclusive with it — contract item 9).
    if args.verify_tdd is not None:
        if args.base is not None:
            print("gate_runner: --verify-tdd is mutually exclusive with --base", file=sys.stderr)
            return 2
        red_path, green_path = args.verify_tdd
        code, message = verify_tdd(
            red_path, green_path, expect_test=args.expect_test,
            require_lineage=not args.allow_unverified_lineage,
        )
        print(message)
        return code

    if args.base is None:
        print("gate_runner: --base is required (unless using --verify-tdd)", file=sys.stderr)
        return 2

    safe_mode = _resolve_safe_mode(args.safe, args.no_safe)
    if safe_mode:
        # Install the process-wide teardown handlers ONCE, here, on the real
        # main thread: signal.signal() raises off it, and gates run on
        # ThreadPoolExecutor workers. This single handler is what drains the
        # guard's registry (every worker thread's acquire/release feeds the
        # same one) on SIGTERM/SIGINT/SIGHUP and at interpreter exit.
        try:
            _guard_module().install_teardown_handlers()
        except _GuardRefusalError as exc:
            print(f"gate_runner: --safe unavailable: {exc}", file=sys.stderr)
            return 2

    # Safe-mode pytest worker ceiling — spec §2 precedence chain, resolved
    # ONCE here. The PYTEST_XDIST_AUTO_NUM_WORKERS default set below reaches
    # every check child through _check_subprocess_env() (the one chokepoint
    # covering command gates, the guard bridge, AND the regression
    # baseline/branch spawns) — the only net for an `-n auto` hidden in a
    # repo's addopts, which no text transform can see. The chain:
    #   request = $REGENLOOP_PYTEST_JOBS if set, else the legacy
    #             $REGENLOOP_SAFE_PYTEST_JOBS (now CLAMPED: honored ahead of
    #             the resource default, never past it), else the cap itself;
    #   unset -> effective = cap (the injected default IS the cap);
    #   numeric N -> effective = min(N, cap), N > cap REWRITTEN down;
    #   auto|logical|count -> `-n auto` text stands, the env var caps it;
    #   off/malformed -> no injection (sequential semantics unchanged), but
    #   the env var and the explicit-N clamp still apply.
    # A blank-but-present $REGENLOOP_PYTEST_JOBS counts as UNSET here — the
    # same blank-as-unset reading the transform's D2 branch applies.
    # Ordered AFTER the --pytest-parallel block above so an explicit operator
    # value still selects the request; under --safe it is honored only up to
    # the resource ceiling (a hard clamp, INV-3 — never past the math).
    if safe_mode:
        cap = _safe_pytest_cap()
        request = (os.environ.get("REGENLOOP_PYTEST_JOBS") or "").strip()
        defaulting = request == ""   # JOBS unset: legacy var / cap is the default
        if defaulting:
            request = (os.environ.get("REGENLOOP_SAFE_PYTEST_JOBS") or "").strip()
        low = request.lower()
        numeric = low.isdecimal() and int(low) >= 1
        if request == "":
            effective = cap                     # unset -> cap (AC-3.1)
        elif low in ("off", "none", "0", "false"):
            effective = cap                     # INV-4: sequential intent
        elif low in ("auto", "logical", "count"):
            effective = cap                     # env var caps `-n auto`
        elif numeric:
            effective = min(int(low), cap)      # AC-3.2 / AC-3.3
        else:
            effective = cap                     # malformed: never a bad flag
        if defaulting:
            # Materialize the resolved DEFAULT into $REGENLOOP_PYTEST_JOBS —
            # the injection machinery's single input. The legacy var fed this
            # env var verbatim before caps existed, so a numeric default
            # materializes CLAMPED and every other value verbatim (identical
            # resolver output to an operator-exported value: auto still
            # injects `-n auto`, off/malformed still inject nothing).
            os.environ["REGENLOOP_PYTEST_JOBS"] = (
                str(effective) if request == "" or numeric else request)
        elif numeric and int(low) > cap:
            os.environ["REGENLOOP_PYTEST_JOBS"] = str(effective)  # raise-clamp
        operator_raw = (os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS") or "").strip()
        if operator_raw.isdecimal() and int(operator_raw) >= 1:
            effective = min(int(operator_raw), effective)  # AC-3.4: lower wins
        os.environ["PYTEST_XDIST_AUTO_NUM_WORKERS"] = str(effective)
    # Vitest half of the safe-mode de-escalation (v1 residual OQ-4: the
    # resource-derived ceiling is pytest-only): flat default 2, legacy
    # $REGENLOOP_SAFE_VITEST_JOBS ahead of it, an explicit value untouched.
    if safe_mode and "REGENLOOP_VITEST_JOBS" not in os.environ:
        os.environ["REGENLOOP_VITEST_JOBS"] = (
            os.environ.get("REGENLOOP_SAFE_VITEST_JOBS") or "2"
        )
    # --jobs, when passed, still wins outright; --sequential still forces 1.
    effective_jobs = args.jobs
    if safe_mode and args.jobs is None:
        effective_jobs = _safe_gate_jobs()

    cwd = str(Path.cwd())
    try:
        root = repo_root(cwd)
        gates_path = _resolve_gates_path(Path(args.config) if args.config else None, Path(root))
        config = load_config(str(gates_path))
        validate_config(config)
        base = resolve_base(args.base, root)
        # Normalize the resolved base to a full 40-char SHA for the regression
        # baseline cache key (spec §4D). resolve_base already returns the
        # merge-base SHA when --base names a branch; rev-parse makes a verbatim
        # ref (e.g. HEAD~1) concrete too. Falls back to base if rev-parse fails.
        base_sha = _git(["rev-parse", base], root).stdout.strip() or base
        changed = changed_files(base, root)
    except (GitError, FileNotFoundError) as exc:
        print(f"gate_runner: {exc}", file=sys.stderr)
        return 2
    except tomllib.TOMLDecodeError as exc:
        print(f"gate_runner: invalid TOML in {gates_path}: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"gate_runner: config error: {exc}", file=sys.stderr)
        return 2

    # Runner-adequacy warning (item 6): non-blocking, printed once config is loaded.
    config_warnings = runner_adequacy_warnings(config)
    for warning in config_warnings:
        print(f"gate_runner: warning: {warning}", file=sys.stderr)

    # Hard error (not a warning) if --gate names a gate that doesn't exist —
    # a typo must surface clearly rather than silently producing an empty,
    # vacuously-green report (contract item 4).
    if args.gate is not None:
        known = {str(g.get("name", "")) for g in config.get("gate", []) or []}
        if args.gate not in known:
            print(
                f"gate_runner: --gate {args.gate!r} does not match any gate "
                f"in {gates_path} (known: {sorted(known)})",
                file=sys.stderr,
            )
            return 2

    # Default-branch safety (item 3): --apply-autofix always refuses on
    # main/master unless overridden; plain check mode only refuses when
    # --forbid-default-branch opts in. Checked BEFORE any autofix runs.
    needs_branch_guard = args.apply_autofix or args.forbid_default_branch
    if needs_branch_guard and not args.allow_default_branch:
        branch = current_branch(root)
        if branch in _DEFAULT_BRANCHES:
            mode = "--apply-autofix" if args.apply_autofix else "check mode (--forbid-default-branch)"
            print(
                f"gate_runner: refusing {mode} on default branch {branch!r}; "
                f"pass --allow-default-branch to override",
                file=sys.stderr,
            )
            return 2

    if args.apply_autofix:
        # Uncommitted-work guard: autofix rewrites files in place, and the
        # documented recovery when an autofix makes things worse is
        # `git checkout -- <changed paths>` — which discards the operator's own
        # uncommitted edits in those same files along with the autofixer's.
        # green-gate runs in the operator's working directory, so refuse rather
        # than put unrecoverable work at risk. The list is reported either way,
        # so a caller never has to guess whether reverting is safe.
        dirty = uncommitted_files(root)
        if dirty is None:
            preexisting_dirty: list[str] = []
            dirty_targets: list[str] = []
        else:
            changed_set = set(changed)
            dirty_targets = sorted(p for p in dirty if p in changed_set)
            preexisting_dirty = dirty_targets
        if dirty_targets and not args.allow_dirty_autofix:
            print(
                "gate_runner: refusing --apply-autofix: these files have "
                "uncommitted changes that an autofix would overwrite (and that "
                "reverting the autofix would then discard):\n  "
                + "\n  ".join(dirty_targets)
                + "\n  commit or stash them first, or pass --allow-dirty-autofix "
                  "to accept the risk",
                file=sys.stderr,
            )
            return 2

        report = build_autofix_report(config, base, changed, root, args.timeout,
                                      args.gate, safe_mode=safe_mode)
        report["preexisting_dirty"] = preexisting_dirty
        if config_warnings:
            report["config_warnings"] = config_warnings
        emit(report, args.json_path, print_autofix_summary)
        # Non-green is decided by the re-check, not here; only a tool-level
        # autofix error is a failure of this invocation.
        errored = any(a.get("status") == "error" for a in report["autofixes"])
        return 1 if errored else 0

    report = build_check_report(
        config, base, changed, root, args.timeout, args.gate,
        tier=args.tier, no_baseline=args.no_baseline, base_sha=base_sha,
        allow_meta_changes=args.allow_meta_changes, forbid_fenced=args.forbid_fenced,
        require_evidence=args.require_evidence, config_path=str(gates_path),
        jobs=effective_jobs, sequential=args.sequential, safe_mode=safe_mode,
    )
    if config_warnings:
        report["config_warnings"] = config_warnings
    emit(report, args.json_path, print_check_summary)

    # Ledger wiring (item 7): check mode only, best-effort, never affects verdict.
    if args.ledger:
        # tier is passed automatically: one logical run appends once per tier,
        # and a shared --ledger-run-id is what lets the ledger count it once.
        append_ledger_entry(
            report, args.ledger, args.ledger_source,
            run_id=args.ledger_run_id, tier=args.tier,
        )

    return 0 if report["verdict"] == "green" else 1


if __name__ == "__main__":
    sys.exit(main())
