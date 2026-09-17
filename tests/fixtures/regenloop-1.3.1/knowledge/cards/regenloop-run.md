---
id: regenloop-run
anchor: skills/regenloop-run
anchor_hash: 717e7537e7fddd60f6b6df89d7de54600ad3b885
last_verified: 2026-08-29
---
## Why this module exists

Thin autonomy + mode envelope around `architect`
([[architect-orchestrator-ref]] owner split) — reimplements nothing of
loop/lanes/review/dispatch. Mode selection (short/deep), termination via
a real [[gate-runner]] run, one worktree, never-push.

## Key invariants

- **§0 bootstrap**: resolver chain with a `$CLAUDE_PLUGIN_ROOT` fallback
  before the plugins search; `$LS` alone `--plugin-only` (a stale in-repo
  state script must not shadow it); the kickoff test runs the fence
  verbatim under runner conditions.
- **§8 one-worktree rule**: ONE worktree per run; no per-writer
  `isolation: worktree` on the sequential spine (exception:
  parallel waves via `wave_merge.py`).
- **Gate discipline**: commit before gating (uncommitted vacuously passes;
  `--require-evidence` both sites); closing gate = one run (fast/full per
  mode), reported per-goal under `regenloop/local/{green-gate,
  regression}/goals/<slug>/`.
- **Short-mode critic gate — earned**: skips human approval only on a clean
  planner-critic `Verdict: proceed`; anything else escalates to the deep
  plan-approval gate.
- **Never pushes** — except `--ship`/a `yes` Clarify answer: hand off to
  [[regenloop-ship]] BEFORE §8 cleanup; base/launch/ship_tier persist via
  `regenloop_state.py`.
- **Bisect (deep only)**: `newly_broken` non-empty → dispatch
  [[regenloop-bisect]] first.
- **TDD receipt**: `--verify-tdd RED GREEN` reads saved gate reports, not
  narrative.
- **could_not_run**: transient (timeout/OSError/JUnit-absent) re-probes
  ≤`$REGENLOOP_CNR_REPROBE` (2×, 30s); permanent (127/126, `ci_only`,
  no check cmd) halts → §8 cleanup. Never a cycle, never Done.
- **Deep-mode Done needs an e2e verdict**: non-`could_not_run` dispatch or
  a recorded `surface-excluded: e2e — <reason>`; none needed with no e2e
  surface.
- **`--safe` (§2)**: routes every `gate_runner.py` call in the run through
  the machine-global guard (`regenloop_guard.py`), so heavy gates across ALL
  sessions on the host serialise. ON if either `--safe` or `REGENLOOP_SAFE=1`;
  only an explicit `--no-safe` disables. §0 preflights the guard root
  (advisory, never a gate); §8 sweeps it at finish.
- Lifecycle: slug → collision guard → provenance check → `start`; every
  stop runs §8 cleanup (commit → remove worktree → validate-queue →
  verify ledger → finish).
