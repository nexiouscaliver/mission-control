---
name: gate-runner
description: Run a repo's real gates (lint, type-check, tests, custom checks) against the files changed vs a base, and get back an objective pass/fail/could_not_run report. Use as the OBJECTIVE gate before declaring code done — in the architect's review step and inside the green-gate loop. It runs real commands; it does not "review" with judgment.
---

# Gate Runner

The objective gate of regenloop. It is **deterministic code, not an LLM** — that is the whole point. A linter's exit code has no stake in passing; an agent asked to "review" does. Use this whenever you need a trustworthy yes/no on whether changed code clears a repo's real checks.

## What it does

Reads `regenloop/gates.toml`, computes the files changed vs a base, selects the gates whose `when` globs match those files, runs each gate's `check` command, and returns a structured report. It never edits code unless you pass `--apply-autofix`.

## How to invoke

Run it **from the target repo's working directory** (so `git diff` sees the changes). Resolve the script path once — it lives in the regenloop plugin under `scripts/gate_runner.py`:

```bash
R="$(git rev-parse --show-toplevel 2>/dev/null)/regenloop/hooks/resolve_plugin_path.sh"
[ -f "$R" ] || R="$(find "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins" -name resolve_plugin_path.sh -path '*regenloop*' 2>/dev/null | head -1)"
[ -f "$R" ] || { echo "resolve_plugin_path.sh not found — is regenloop installed?"; exit 1; }
GR="$(sh "$R" gate_runner.py)" || { echo "gate_runner.py not found — is regenloop installed?"; exit 1; }

# Check the working branch against its base (exit 0 = green, 1 = not green, 2 = could not produce a report)
python3 "$GR" --base <target-branch-or-sha> --json report.json

# Apply deterministic autofixes (scoped to changed∖never_touch), then re-run WITHOUT the flag to re-check
python3 "$GR" --base <target> --apply-autofix
python3 "$GR" --base <target>          # re-check

# Restrict to one gate (useful when iterating on a single failure)
python3 "$GR" --base <target> --gate frontend-typecheck

# Hard-fail on any fenced-file change, and record a ledger entry for this run
python3 "$GR" --base <target> --forbid-fenced --ledger regenloop/local/ledger.jsonl --ledger-source green-gate
```

Requirements: Python ≥ 3.12 (uses stdlib `tomllib`). No third-party packages, no venv — it is zero-dependency by design so it runs anywhere.

## How to read the report

Each gate has one of **four** states — never collapse them:

| State | Meaning | What to do |
| :-- | :-- | :-- |
| `pass` | the check succeeded | nothing |
| `fail` | the check ran and failed | if `autofix_available`, try `--apply-autofix`; otherwise classify + escalate |
| `could_not_run` | a precondition was missing (e.g. `docker-db` down) or the tool errored, or the gate is `ci_only` | **escalate — this is NOT a pass.** Show the `hint`, and suggest `/regenloop-doctor` — it checks git, python3 (version), bun/node when a gate's `check`/`chdir` references them, the docker binary and daemon when a gate declares `needs = ["docker-db"]`, that gate's probe port, and venv/interpreter resolution for python-`chdir` gates, naming the missing precondition with a fix. It does **not** verify that a resolved interpreter can actually import the test runner (e.g. `pytest`) it's about to be asked to run — a `could_not_run` from that gap still needs to be resolved by hand |
| `skip` | the gate's `path_filters` matched none of the changed files and it has no `select_default` fallback | verdict-neutral (treated like `pass` for `verdict`), but **surface it** — a skipped gate ran zero tests, so don't report it as if it exercised anything |

Top-level `verdict` is `green` **only if** every applicable gate is `pass` **and** `coverage.unmatched_changed_files` is empty (a changed file matched by no gate is a coverage gap, not a silent pass).

`coverage.fenced_changed_files` lists changed files matching `never_touch` (auth/billing/oauth/migrations…). These are **deliberately not auto-touched** — surface them so a human verifies them via review/CI. Pass `--forbid-fenced` to make this failure-grade rather than advisory: a non-empty `coverage.fenced_changed_files` then forces `verdict: "fail"` with field `fence_violations` populated, even if every gate itself passed.

The report also carries `head_sha` — the resolved SHA of the checked-out HEAD at run time — so a saved report can be tied back to the exact commit it was produced against (useful for the ledger and for `--verify-tdd`, below).

`config_warnings` appears when the loaded `gates.toml` has no gate whose `check` command contains a recognized test/lint/type-check runner token — a signal the config may be a stub or miscopied, surfaced without failing the run.

## Flags reference

| Flag | Behavior |
|---|---|
| `--tier fast\|full` | Which regression tier to run. `fast` (default) runs command gates plus fast-tier regression gates; `full` is a superset that also runs full-tier regression gates. Command gates always run regardless of tier. |
| `--no-baseline` | Skip baseline worktree creation and cache lookups for regression gates; every branch failure is then reported as a regression (`baseline_skipped: true`). |
| `--forbid-fenced` | Promote a non-empty `coverage.fenced_changed_files` from advisory to failure-grade: `verdict: "fail"`, field `fence_violations` populated. |
| `--allow-meta-changes` | Required to let a run go green when the diff touches `regenloop/gates.toml` or `.claude/gates.toml` (see Meta-config tamper fence, below). Absent this flag, such a diff is always `fail`. |
| `--apply-autofix` | Runs autofix commands instead of checking. **Refuses on `main`/`master`** (exit 2) unless `--allow-default-branch` is also passed — autofix mutates the working tree, and doing that unattended on the default branch is the one autofix action this tool won't do by default. |
| `--allow-default-branch` | Overrides the `--apply-autofix` default-branch refusal above. Named explicitly so it can't be set by accident. |
| `--forbid-default-branch` | Opt-in for **check** mode (no autofix): fail outright if the current branch is `main`/`master`, instead of running checks against the default branch. Off by default for check mode — only `--apply-autofix` refuses by default. |
| `--gate NAME` | Restrict to one gate. A `NAME` that matches no gate in `gates.toml` is now a **hard error — exit 2** (previously a warning only): a typo in `--gate` must not silently produce an empty, vacuously-passing report. |
| `--ledger PATH --ledger-source STR` | Appends one JSON-lines entry to `PATH` recording this run's verdict, tagged with `STR` (e.g. `green-gate`, `regenloop-run`, `regression`). Both flags are required together. This is what `/regenloop-report` and the ledger `verify` subcommand read. |
| `--verify-tdd RED.json GREEN.json` | Standalone verification mode — see below. Mutually exclusive with a normal check/autofix run. |
| `--expect-test SUBSTR` | Only with `--verify-tdd`: lets a command-gate-only repo (no regression gate) satisfy the red requirement. See below. |
| `--timeout SECONDS` | Per-command ceiling for every check/fix execution path — full scope in the paragraph directly below. |

**`--timeout SECONDS` scope.** The flag — and `$REGENLOOP_GATE_TIMEOUT_S`, which supplies the **default only** (unset → 900 s; an explicit flag always wins) — bounds every path that executes a check or fix command: command-gate checks (each transient re-probe attempt is separately bounded, so the worst-case TOTAL wall for one forever-hanging gate is `(N+1) × timeout + N × interval`, with N = `$REGENLOOP_CNR_REPROBE` and interval = `$REGENLOOP_CNR_REPROBE_INTERVAL_S`), regression branch runs, baseline dep-install + baseline suite run, and autofix runs. It does **not** govern the runner's internal plumbing: git calls (`GIT_TIMEOUT` = 300 s), the pytest-xdist importability probe (20 s), and the CoW clone/probe (600 s) each keep their own fixed ceilings.

## Meta-config tamper fence (default ON)

A diff that touches `regenloop/gates.toml` or `.claude/gates.toml` can **never** be green by default — the report comes back `verdict: "fail"` with field `meta_config_changed`, independent of every other gate's result and independent of `--forbid-fenced`. `never_touch` protects files from the *autofixer*; this protects the gate config itself from being silently loosened by the branch it's supposed to be judging. The only way past it is `--allow-meta-changes` — treat setting that flag as an event that needs a named, human-approved reason, not a routine unblock.

## Standalone TDD verification (`--verify-tdd`)

```bash
python3 "$GR" --verify-tdd regenloop/local/tdd/task-1-red.json regenloop/local/tdd/task-1-green.json
```

Reads two previously-saved gate reports (produced by two ordinary `--json` runs) and checks the red→green claim mechanically instead of trusting an implementer's narrative. Exit 0 **iff** all three hold:

1. **Red requirement** — satisfied by *either* path:
   - a regression-kind gate in `RED.json` with `status: "fail"` and a non-empty `new_and_failing`, **or**
   - (with `--expect-test SUBSTR`) any gate in `RED.json` with `status: "fail"` whose `items` entries or `summary` contain `SUBSTR` — the new test's id. This is the only way to satisfy the red requirement in a **command-gate-only repo** (this plugin's own repo, and the shipped `gates.toml` template), which has no regression gate and therefore no `new_and_failing` field at all. Without the flag such repos' genuine receipts are rejected. Regression evidence, when present, is preferred and sufficient on its own — the two paths compose.
2. **Green requirement** — `GREEN.json` shows `verdict: "green"` with an empty `regressions` on every regression gate. When the red requirement was met via `--expect-test`, the same-named gate that was failing in `RED.json` must additionally show `status: "pass"` in `GREEN.json`.
3. **Lineage** — by default (`require_lineage=True`, i.e. without `--allow-unverified-lineage`), **both** reports must carry a `head_sha` at all; if either is absent, verify-tdd FAILS outright naming which report is missing it. When both are present but resolve in the current repo (`git cat-file -e`), `GREEN.json`'s head must descend from or equal `RED.json`'s head (`git merge-base --is-ancestor`; equal SHAs pass — the legitimate uncommitted-work flow); if it doesn't, the failure names both SHAs and hints to re-run the RED gate if you rebased/amended in between. Only when both `head_sha`s are present but at least one does not resolve here (git absent from PATH, or the commit isn't reachable — e.g. a report from another machine) does it fall back to the older, weaker check — the two reports' `base` fields must match — and the PASS message notes `lineage unverified (head_shas present but not resolvable here)`. Pass `--allow-unverified-lineage` to also accept reports with a genuinely absent `head_sha` (pre-v0.4.0 receipts, or hand-authored ones) via that same lenient fallback.

Any other outcome is a non-zero exit (`1` = TDD claim did not hold; `2` = a report file is missing/unreadable) — the task is not done. This is the mechanism the `regenloop-run` skill's §5 receipt check and the `tdd` skill both call before accepting red→green evidence. In a command-gate-only repo, that receipt check MUST pass `--expect-test <new-test-id>`.

```bash
# Command-gate-only repo (no regression gate): name the new test's id.
python3 "$GR" --verify-tdd RED.json GREEN.json --expect-test test_widget_rejects_empty_input
```

## Guarantees (rely on these)

- Autofix only ever runs over `changed ∖ never_touch` files; the fence is matched **case-insensitively** so a mixed-case sensitive path can't slip through.
- Gates-config shape rules — autofix `{changed}` containment (with its explicit `autofix_unscoped` opt-out), vacuous-check rejection, regression-gate field rules such as `lazy_retry` — are enforced at config load by `validate_config` / `_validate_regression_gate` in `scripts/gate_runner.py` (exit 2 on the first violation). This file deliberately does not restate them: the validators are the single authoritative site, so this prose cannot drift from the code.
- **Check subprocesses run isolated from git's hook environment.** Every `check` runs with the git-injected repo-location variables (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_PREFIX`, `GIT_COMMON_DIR`, and the object-dir vars) stripped from its environment. When the gate-runner is invoked from a git hook (pre-push/pre-commit), git exports those pointing at the repo the hook fired in; without stripping, a check that runs git in another directory with only `cwd` set — e.g. a test suite building fixture repos — would commit to the real repo instead, moving the branch being pushed off its HEAD. This makes a gate's own checks unable to corrupt the outer repo through an inherited hook context. (Keep test suites hermetic anyway — see the `tdd` skill — so they're safe under any runner, not only this one.)
- **Check mode:** exit code is usable directly as a gate: `0` green, `1` not green, `2` couldn't produce a report (this also covers a config load rejection, an unmatched `--gate`, and an `--apply-autofix`/`--forbid-default-branch` default-branch refusal).
- **`--apply-autofix` mode:** its exit code is **not a verdict signal**. `0` means every autofix command ran without a tool-level error; `1` means at least one autofix command itself errored (e.g. the linter binary crashed) — it says nothing about whether the code is now green. The verdict only ever comes from the **re-run in check mode** that must follow every autofix pass.

## When NOT to use it

This is the *checker*, never the *maker*. Don't ask it to fix things beyond the deterministic `autofix` commands, and don't treat a subagent's prose "looks good" as a substitute for its verdict.
