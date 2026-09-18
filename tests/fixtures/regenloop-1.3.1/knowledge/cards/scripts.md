---
id: scripts
anchor: scripts
anchor_hash: c5e0beb6d413c1da1471654980ed780417204f78
last_verified: 2026-08-31
---
## Why this module exists

Mechanism layer skills call into — one zero-dep script per concern,
resolved via `resolve_plugin_path.sh` (in-repo → install → registry →
version-sorted, [[gate-runner]]). Logic lives here, not prose — verdicts
stay deterministic without an LLM.

## Key invariants

- Conventions: stdlib-only, ≥3.12; per-script exit codes; git via
  subprocess; `gates.toml` edited only by `regenloop_init.py
  --apply-migration` (approval-gated).
- Roster: `gate_runner.py`+`gate_runner_regression.py` the
  kind="regression" carve ([[gate-runner]]). `ledger.py` append-only JSONL.
  `bisect_regression.py` git bisect, ephemeral worktree.
  `regenloop_doctor.py` tools/ports from `gates.toml`; absolute hints;
  e2e-deps. `venv_resolve.py` venv discovery. `regenloop_state.py`
  per-goal lifecycle; durable `set`/`get` (absent=3, empty=0).
  `regenloop_budget.py` cycle-convergence guard. `regenloop_init.py`
  knowledge initializer; re-init refreshes vendored hook files
  against `regenloop/local/vendored.sha256` (match→refresh,
  mismatch→WARN+skip, no-record→backup+refresh; migrate records exact
  copies only). `context_diet.py` CLAUDE.md→eager/lazy. `coverage_gap.py`
  coverage report. `design_lint.py`/`design_palette.py`
  [[frontend-design]]. `wave_merge.py` parallel-writer merges.
  `regenloop_worktree_gc.py` orphan-worktree GC.
  `validate_plugin.py` manifest check. `accelerate_tests.py`
  [[accelerate-tests]]. `regenloop_approve.py` blocks until answered
  (1800s). `regenloop_ensure_deps.py` `uv` venv bootstrap; timeouts →
  rc 124; self-hints absolute. `regenloop_ui.py` Cockpit comp root; FU1
  session-token GC ([[cockpit]]). `mr_notes.py` sole GitLab write path
  ([[regenloop-ship]]): `glab api` posts, redaction, bounded calls,
  resume markers. `ui_dist_check.py` `ui/dist` freshness
  (--verify/--rebuild). `regenloop_guard.py` machine-global `--safe`-mode
  admission control: lease-based FIFO queue rooted at
  `${REGENLOOP_GUARD_ROOT:-~/.regenloop/guard}`, outside every repo/worktree/
  `~/.claude*` home so parallel sessions share one view; heavy/light gate
  classification + serial/budget policies; `run`/`status`/`sweep`/`release`/
  `safe-scope` subcommands. `run --quiet` puts the wrapped command's own output
  on stdout and drops the JSON envelope; `safe-scope set|clear|check --path`
  arms/disarms the ad-hoc-Bash hook for a directory tree ([[hooks]]). Exports
  `REGENLOOP_GUARD_ACTIVE=1` into every supervised child, and — via the CLI's
  `run(nested_passthrough=True)` — READS it back: already inside a guarded
  command it supervises directly under the parent's lease instead of queueing
  for one it can never get. That is what makes the serial-policy self-deadlock
  structurally impossible rather than dependent on the hook recognising a
  routed command from its text ([[hooks]]). **Every** admission site must
  ask `nested_under_guard()` first — the guard CLI, `gate_runner`'s
  `_guard_module().run(...)`, and `_run_regression_gate_via_guard`, which calls
  `acquire()` DIRECTLY and so was untouched by two rounds of arming a `run`
  kwarg. Both misses deadlocked for real with `REGENLOOP_SAFE=1` exported: a
  pre-push gate held the lease while running the suite, the suite's nested-gate
  test re-entered the engine, and the inner acquire queued behind its own parent
  — 56 min/3 waiters via the plain gate path, then 40 min/2 waiters via the
  regression path (6h ceiling; both ended in a hand kill). Per-site fixes are
  what failed twice, so the invariant is now enforced by an AST test over
  `gate_runner.py` rather than by remembering
  (`test_every_gate_runner_admission_site_checks_for_nesting`). The predicate is
  deliberately NOT inside `acquire()`: the suite runs under this guard in
  `--safe`, so `os.environ` carries the marker process-wide and every
  direct-`acquire` admission test would pass vacuously. **Never export
  `REGENLOOP_GUARD_ACTIVE` by hand** — it disarms the hook AND makes every
  explicit `run` skip admission; it is in `ENGINE_TUNING_ENV_VARS` for that
  reason ([[tests]]).
- **Identity before a cross-process signal is the START TIME, never the argv.**
  `exec` replaces the program image but not the process, so start time survives
  it; the recorded command line only corroborates (`_argv_corroborates`, logged
  on the event). Requiring argv to match was a shipped orphan bug: every
  supervised command is `/bin/sh -c '<cmd>'`, sh execs a simple command, so the
  live argv stopped matching within milliseconds and both cleanup paths freed
  the lease without signalling. Superseded AC10.
- `--total-budget-s` bounds queue wait + run TOGETHER, resolved after
  `acquire()` when the real `wait_s` is known — a static ceiling-minus-max-wait
  is wrong whenever the queue was empty. `--quiet` STREAMS (inherits stdio) so
  an outer kill cannot swallow the command's output. The memory floor is
  1/12 of RAM (`MEM_FLOOR_FRACTION`), not an absolute figure measured on one
  host; 24GB still resolves to ~2048MB.
- FU7: backend error codes ↔ ui `ERROR_CODE_TEXT` both ways
  (`test_cockpit_ui_contract.py`). FU6: shared `commitWait.ts` waits
  (`COMMIT_WAIT` 5000ms).
