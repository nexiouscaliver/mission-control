---
name: regenloop-doctor
description: Use before a regenloop run, or when a gate fails with a missing or mis-versioned tool, to check the environment — reads the repo's gates.toml and verifies every tool each gate needs is installed and correctly versioned, reporting OK / MISSING / WRONG-VERSION per requirement with a one-line fix hint. Flags the backend baseline-venv interpreter gap. Exit 0 if all requirements met, 1 if any gate would be blocked.
---

# Loop Doctor

Reads the repo's `gates.toml` and verifies that every tool a gate needs is installed
and correctly versioned.  Reports per-requirement: **OK / MISSING / WRONG-VERSION** with
a one-line fix hint.  Read-only; no side effects.

## Preconditions

- You are operating **from the target repo's root**, not the regenloop plugin directory.
- A `gates.toml` exists at `<repo_root>/regenloop/gates.toml`. If absent, the doctor
  reports an error and exits 1 — and when the cause is a pre-rename `loop-kit/gates.toml`
  (the repo was onboarded under the plugin's old name), the doctor's error names the fix:
  run `/regenloop-migrate` first, then re-run the doctor.
- **Locate `regenloop_doctor.py`** using the plugin-path pattern:
  ```bash
  LD="${CLAUDE_PLUGIN_ROOT:-}/scripts/regenloop_doctor.py"
  [ -f "$LD" ] || LD=$(find "$HOME/.claude/plugins" -name regenloop_doctor.py -path '*regenloop*' 2>/dev/null | head -1)
  [ -f "$LD" ] || { echo "regenloop_doctor.py not found — is regenloop installed?"; exit 1; }
  ```
  Use `"$LD"` in every command below.

## The procedure

```bash
python3 "$LD" [repo-root]
```

The doctor always checks (needed by gate_runner.py itself):
1. **git** — required for diff computation.
2. **python3 >=3.12** — required to run `gate_runner.py` (uses `tomllib`, etc.).

The doctor then derives additional requirements from the gates themselves:
3. **bun / bunx** — if any gate's `check` or `baseline_install` references `bun` or `bunx`.
4. **node** — if any gate references `node`.
5. **docker** + **docker daemon** — if any gate declares `needs = ["docker-db"]`.
6. **TCP port probe** — if any gate specifies `probe_port`, that port is TCP-probed.
7. **Python interpreter / venv** — if any gate's `check`/`baseline_install` references
   `.venv/bin/python3` or the `{python}` token, the doctor resolves the interpreter for
   that gate's `chdir` cross-platform, checked in this priority order:
   1. **`VIRTUAL_ENV` environment variable** — POSIX `<VIRTUAL_ENV>/bin/python3` or
      Windows `<VIRTUAL_ENV>/Scripts/python.exe`, whichever exists. Wins over
      everything else.
   2. **Per-machine cache** (`regenloop/local/venv.json`) — a path the operator
      previously confirmed via the `regenloop-init`/`regenloop-doctor`-driven interpreter
      resolution flow, keyed by the gate's `chdir`.
   3. **Bounded ancestor-directory search** — `chdir`, then each parent directory,
      up to **4 ancestor levels** (`_MAX_ANCESTOR_LEVELS` in `scripts/venv_resolve.py`),
      checking both POSIX (`.venv/bin/python3`, `.venv/bin/python`, `venv/bin/python3`,
      `venv/bin/python`) and Windows (`.venv/Scripts/python.exe`,
      `venv/Scripts/python.exe`) layouts unconditionally at every level — venvs "in
      project parent folders" are found this way, not just at the gate's own `chdir`.

   Result reporting:
   - Exactly one candidate found (or a valid `VIRTUAL_ENV`) → `[OK]` with the
     resolved interpreter's version.
   - Two or more candidates found (none cached, no `VIRTUAL_ENV`) → `[MISSING]`,
     fix text lists every candidate path and points at `/regenloop-doctor` (or
     `/regenloop-init`) to confirm which one to use.
   - Zero candidates found → `[MISSING]`, fix text points at `/regenloop-doctor` (or
     `/regenloop-init`) to point at the interpreter/venv to use.
   - **The fix text never suggests creating a venv** — no `python3 -m venv` or
     `virtualenv` invocation appears anywhere in this check's output, ever. RegenLoop
     only finds-or-asks; it never creates.
   - **`VIRTUAL_ENV`-vs-cache conflict**: if `VIRTUAL_ENV` is set to a path that
     differs from an existing cache entry for the same `chdir`, the doctor still
     reports `[OK]` with `VIRTUAL_ENV`'s path (an activated venv is a valid, explicit
     signal), but prints an extra `note:` line naming both paths and flagging that the
     cached choice was a prior, explicit operator confirmation — so the operator
     notices before assuming the cache is still authoritative.
8. **baseline-venv interpreter gap** — if any gate's `baseline_install` still matches
   the legacy venv-creation pattern (`python3 -m venv ...` / `virtualenv ...`), the
   doctor reports this as a **blocking `[MISSING]`** finding (not an advisory `[OK]`
   note) labeled `baseline-venv interpreter (<chdir>)`, because `gate_runner.py` now
   *refuses* to execute that pattern (the gate reports `could_not_run` until migrated —
   this is not merely informational). Fix text points at the `/regenloop-doctor` (or
   `/regenloop-init`) migration flow: review the diff produced by
   `interpreter --migrate-diff` and, after explicit approval, apply it with
   `interpreter --apply-migration`, which rewrites the gate's `baseline_install`/`check`
   to the `{python}`-based equivalents. `regenloop_doctor.py` itself never writes
   `gates.toml` — only detects and points at the migration flow.

**Known limitation (accepted, not solved):** because `{python}` reuses one
operator-confirmed interpreter for both the baseline (historical-commit) run
and the branch run, a baseline run replays old source code against *today's*
installed dependency set. If `requirements.txt`/a lockfile changed between
the merge-base commit and HEAD, the baseline's dependencies no longer match
what that historical commit actually shipped with — this can silently
produce a wrong baseline (spurious regressions or spuriously-clean results),
with no warning raised. `regenloop-doctor` does not detect this drift; it is an
accepted tradeoff of the "never auto-install" rule, not something this check
covers.

## Status codes

| Status | Meaning |
|---|---|
| `[OK]` | Tool present and correctly versioned. |
| `[MISSING]` | Tool not found on PATH (or `.venv` not present). |
| `[WRONG-VERSION]` | Tool found but version constraint not satisfied (e.g. python3 < 3.12, Docker daemon down). |

## Exit codes

- **0** — all gate requirements satisfied.
- **1** — one or more gate requirements not met.

## Guardrails

- Never run against the regenloop plugin directory itself.
- No side effects — read-only inspection only.
- Never installs tools, creates files, or modifies the repo.
- A tool not needed by any gate is not checked (no false alerts).
