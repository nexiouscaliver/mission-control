# gate_runner tuning — environment variables & gate fields

Operator-tunable knobs for `scripts/gate_runner.py` (the regenloop objective gate).
All are **opt-in / default-off** unless noted — the engine is verdict-safe by default;
setting any of these changes *behavior* (run count, concurrency shape, install source)
but never the verdict on the default path. For the full design spec see
`claudedocs/2026-08-10-regenloop-test-gate-optimization-changes.md`.

## Environment variables

| Variable | Default | Effect |
|---|---|---|
| `REGENLOOP_VITEST_JOBS` | unset (passthrough) | **B7:** append `--maxWorkers <spec>` to a regression gate's vitest `check` at BOTH the branch + baseline anchors (base/branch parity). Accepts `N` (e.g. `4`) or `NN%` (e.g. `50%`); `off`/`none`/`0`/`auto`/unset = passthrough (vitest has no `--maxWorkers=auto` keyword). **Thermal relief, not speed** — ~1.3–2× slower on CPU-bound suites, fewer pegged cores. Only DIRECT vitest invocations match (`vitest`, `bunx vitest`, `npx vitest`, `pnpm exec vitest`, `node_modules/.bin/vitest`, …); opaque wrappers (`bun run test`, `npm test`) are untouched. Respects an author-set `--maxWorkers`/`--minWorkers`/`--pool`/…; refuses to inject on a compound command (`vitest run \| tee log`) where the flag would misland. |
| `REGENLOOP_REGRESSION_PIPELINE` | unset (off) | **B8:** overlap a regression gate's baseline + branch runs on cache-MISS via an inner `ThreadPoolExecutor(cap=2)` (sequential on cache-HIT). Correctness-neutral (run_ctx fields are disjoint: baseline writes the install/timing fields; the branch only reads `repo_root`/`timeout`). Skips vitest-bearing gates unless `REGENLOOP_VITEST_JOBS` is capped (would otherwise 2× oversubscribe the heat source). |
| `REGENLOOP_BASELINE_INSTALL_REUSE` | unset (enabled per-gate) | **B10:** global kill-switch for CoW dep reuse. Set `false` to force EVERY regression gate to run its real `baseline_install` (no CoW clone of HEAD's deps), even gates that declare `baseline_install_reuse`. Lockfile detection is chdir-first then dep-dir-parent. |
| `REGENLOOP_FAST_BUDGET_S` | `60` | **B5:** a `tier = "fast"` regression gate whose measured `branch_run_s` exceeds this emits a non-blocking heavy-fast-tier diagnostic naming the demote/split remedy. Verdict unchanged. |
| `REGENLOOP_BASELINE_EXPECT_SHRINK` | unset | **B2:** env escape valve to permit a baseline `test_map` that shrinks vs the nearest ancestor (the crash-guard normally refuses to cache a shrunken baseline). Prefer the per-gate `baseline_expect_shrink` field. |
| `REGENLOOP_LAZY_RETRY` | — | **B6 (DEFERRED — spec'd, not shipped).** Would collapse flake retries to a single run when a baseline exists. |

## `--safe` mode & the machine-global guard (`regenloop_guard.py`)

`--safe` (or `$REGENLOOP_SAFE=1`) turns on machine-global resource admission
control for `gate_runner.py` and `/regenloop-run`: every gate check, autofix,
and regression baseline/branch evaluation waits its turn through
`scripts/regenloop_guard.py` before spawning, and pytest/vitest/gate-pool
concurrency default down instead of `-n auto`/one-thread-per-core. It exists
because parallel `/regenloop-run` sessions — one per Claude CLI config home
(`~/.claude`, `~/.claude-glm`, …) — each independently size their gate pool
and pytest workers off the whole machine's core count with no idea any other
session exists; run enough of those overnight and the machine OOMs.

| Variable | Default | Effect |
|---|---|---|
| `REGENLOOP_SAFE` | unset (off) | Turns on `--safe` for every `gate_runner.py`/`/regenloop-run` invocation in the shell that has it exported. Fail-safe OR-to-enable: safe mode is ON if **either** `--safe` is passed **or** this is `1`. Only an explicit `--no-safe` turns it back off — `REGENLOOP_SAFE=0` (or unset) never overrides an explicit `--safe`. |
| `REGENLOOP_SAFE_PYTEST_JOBS` | resource-derived cap | Under `--safe`, the pytest worker ceiling (`$REGENLOOP_PYTEST_JOBS`) defaults to the guard's resource-derived cap (or 2 if the guard module itself can't be loaded — `_SAFE_PYTEST_CAP_FALLBACK`) when both this and `REGENLOOP_PYTEST_JOBS` are unset. Still honored ahead of the resource default, still never past the cap (an exported `4` with cap 3 → `3`). |
| `REGENLOOP_SAFE_VITEST_JOBS` | `2` | Same, for `$REGENLOOP_VITEST_JOBS` under `--safe`. |
| `REGENLOOP_SAFE_GATE_JOBS` | `2` | Under `--safe`, the gate-runner's own thread-pool width defaults to this instead of one-thread-per-gate, when `--jobs`/`--sequential` wasn't passed explicitly. |
| `REGENLOOP_GUARD_ROOT` | `~/.regenloop/guard` | Where the guard's lease/ticket bookkeeping and `events.log` live. Deliberately **outside** every repo, worktree, and `~/.claude*` config home, so every session on the host — regardless of which CLI config home launched it — shares one view of who's running what. |
| `REGENLOOP_GUARD_MEM_FLOOR_MB` | `2048` | Soft memory floor. Below this much available RAM, admission of a new lease backs off (subject to the progress-guarantee valve — see the module docstring in `regenloop_guard.py` — so a fully idle machine can never refuse forever). |
| `REGENLOOP_GUARD_SWAP_FLOOR_MB` | `256` | Soft swap floor, read on macOS only. Corroborates the memory floor: macOS's memory compressor can keep `vm_stat`'s "available" reading looking healthy while the machine is actually thrashing into swap, so a low-swap reading is the corroborating signal the floor logic requires there. On Linux (no swap reading) the memory floor gates alone. |
| `REGENLOOP_GUARD_MAX_WAIT_S` | `21600` (6h) | How long a queued gate waits for a turn before the guard refuses it (the gate reports `could_not_run`, guard exit code 2). See the regression-gate note below for when to raise this. |
| `REGENLOOP_GUARD_KILL_GRACE_S` | `10` | SIGTERM→SIGKILL grace period for a guard-torn-down process group. Also read **independently** by `gate_runner.py`'s own unconditional process-group teardown (below) — one operator-facing variable name, two separate readers that never share an import, pinned together by `tests/test_pgroup_teardown_parity.py` so they can't silently drift apart. |
| `REGENLOOP_HOOK_BG_TIMEOUT_S` | `7200` (2h) | Ceiling on a **backgrounded** hook-wrapped command. Not about the caller — a backgrounded run has no harness deadline — but about the lease: without it a watch-mode runner (`vitest --watch &`) holds the single machine-wide heavy lease for its whole lifetime and starves every other `--safe` session on the host. |
| `REGENLOOP_GUARD_MEM_FLOOR_MB` | 1/12 of RAM | Free-memory floor below which no heavy job is admitted. **Derived from total RAM by default** (24 GB → ~2048 MB, the value the published measurements were taken at; 8 GB → ~683 MB), because an absolute floor does not travel: on a host that idles below it, the first heavy acquisition is refused even with nothing else running and must wait out the full `REGENLOOP_GUARD_FLOOR_STALL_S`, and that toll is paid again on every subsequent acquisition rather than once. Setting this variable overrides the derivation absolutely. |
| `REGENLOOP_GUARD_WORKER_MB` | `2048` | Per-worker memory weight of the resource-derived pytest worker ceiling (subsection below). Deliberately the SAME value as the guard's heavy-class weight (`DEFAULT_HEAVY_WEIGHT_MB` — measured pytest process-tree peak 1490 MB, rounded up): one memory model, overestimating a single worker is the safe direction. Lower it if your workers are genuinely light — this is the only raise path; there is no absolute-max knob (see the subsection's honest framing). |
| `REGENLOOP_HOOK_MAX_WAIT_S` | `300` | How long a **hook-wrapped ad-hoc** Bash test command waits for a turn before the guard refuses it (exit 2). Deliberately far below `REGENLOOP_GUARD_MAX_WAIT_S`: the Bash tool caps a foreground call at 600 s, so a queue wait that outlives it produces an unexplained kill instead of a refusal the agent can read and act on. Background (`run_in_background`) commands have no such deadline and keep the guard's full default ceiling. |
| `REGENLOOP_GUARD_ACTIVE` | set by the guard | Exported into every command the guard supervises, and read back as a re-entrancy fence: the Bash hook uses it to avoid double-wrapping, and **every** guard admission site — the `regenloop_guard.py run` CLI, and both of `gate_runner.py`'s in-process ones — checks it before queueing, so work already inside a guarded command runs under its parent's lease instead of waiting for one it can never be granted. **Never set it by hand.** Doing so both disarms the hook and makes every explicit `regenloop_guard.py run` skip admission entirely and run with no lease — silently, in the under-throttling direction. It is inherited by every descendant of a guarded command, so beware of exporting it from a shell profile or a `.env` you source before tests. |
| `REGENLOOP_GUARD_HEAVY_GATES` | unset | Comma-separated gate **names** forced into the `heavy` class regardless of the allowlist below. Heavy wins if a name appears in both this and `LIGHT_GATES` — the safe direction for a self-contradictory override. |
| `REGENLOOP_GUARD_LIGHT_GATES` | unset | Comma-separated gate names forced into the `light` class. Cannot override a `kind = "regression"` gate or any command containing a `{junit}` token — those are always heavy, unconditionally, before either escape hatch is consulted. |

Other guard-internal knobs (poll interval/jitter, head-stall and floor-stall
windows, the progress-guarantee valve, per-class default memory weights,
`REGENLOOP_GUARD_MAX_LIGHT`) exist for advanced tuning and are documented in
`regenloop_guard.py`'s module docstring and env-resolver functions rather than
duplicated here — the table above covers what an operator actually needs to
turn the knob for.

### Resource-derived worker ceiling (`--safe`, pytest)

Under `--safe`, the pytest worker ceiling is derived from the machine, not
fixed. `regenloop_guard.py`'s `plan_worker_cap()` — the ONE sizing function;
`gate_runner`, the regression anchors, and the Bash hook all read it, nothing
re-derives it — computes:

```
cap = max(1, min(cpu_count − 1, floor((available_mb − mem_floor) / REGENLOOP_GUARD_WORKER_MB)))
```

with two degrade rules: a readable macOS swap reading below
`REGENLOOP_GUARD_SWAP_FLOOR_MB` clamps the cap to 1 (swap-corroborated
thrashing — the 2026-09-06 incident class; Linux has no swap reading and stays
memory-only), and an unreadable memory read degrades conservatively to
`min(cpu_count − 1, 2)`. The reserve is the same mem floor the admission
control uses (derived from total RAM, overridable via
`REGENLOOP_GUARD_MEM_FLOOR_MB`) — no separate knob. The result is memoized for
5 s, keyed on the three knob values. Query it on any host with
`python3 scripts/regenloop_guard.py plan --json` (always exit 0, reads machine
state only). The incident arithmetic, for scale: ~7.4 GB available of 24 GB →
(7400 − 2048) / 2048 = 2, cpu_cap 13 → **cap 2**, not 14.

`gate_runner.py` applies the cap three ways under `--safe`:

1. **Env-var net (`PYTEST_XDIST_AUTO_NUM_WORKERS`).** `main()` resolves the
   typed precedence chain once and writes the env var, which reaches every
   check child through the one `_check_subprocess_env()` chokepoint — command
   gates, the guard bridge, and the regression baseline/branch spawns. It is
   the ONLY net for `-n auto`, whether in command text or in a repo's
   `addopts` (both invisible to text transforms), and it does NOT cap an
   explicit numeric `-n`. The chain: request = `$REGENLOOP_PYTEST_JOBS` if
   set, else the legacy `$REGENLOOP_SAFE_PYTEST_JOBS`, else the cap —
   **unset → cap** (the injected default IS the cap); **numeric N ≥ 1 →
   min(N, cap)**, with N > cap rewritten down in the env (the raise-clamp);
   **`auto`/`logical`/`count` → the `-n auto` text stands, the env var caps
   it**; **`off`/malformed → no injection** (sequential semantics unchanged),
   but the env var and the explicit-N clamp still apply. An operator-exported
   `PYTEST_XDIST_AUTO_NUM_WORKERS` above the effective value is lowered; a
   lower value wins freely.
2. **Text clamp.** The gate transforms and BOTH regression anchors (B7
   dual-anchor parity) append ` -n {cap}` after an explicit numeric
   `-n N > cap` — argparse last-wins. `N ≤ cap` stands (the clamp target is
   the cap, not the effective value); `-n auto` text is untouched (the env
   var's job); `-n 0`/`off` and `-p no:xdist` are never touched (sequential
   intent is sacred). COMPOUND commands — a sequencing operator after the
   pytest invocation, `pytest … -n 14 | tee log` — are not text-clamped: an
   end-of-string append would land on the downstream segment; under `--safe`
   the injection append is suppressed there too. `-n auto` in compounds is
   still capped by the env var; an explicit numeric `-n` in a compound is a
   disclosed residual. A `#` anywhere in the command refuses the text clamp
   on every shell-parsed surface (string-path gates, both regression anchors,
   the Bash hook) — the append could land inside a `/bin/sh` comment while
   the shell runs the uncapped fan-out; `-n auto` forms are still capped by
   the env var, and a `#`-bearing command with no shell operators runs
   argv-side, where no comment exists and the clamp still fires.
3. **Regression pipelining.** With `REGENLOOP_REGRESSION_PIPELINE=1`, the
   overlapping baseline+branch pools each get
   `pool = max(1, min(effective, cap) // 2)` — derived from the effective
   global the env var carries, so the per-pool value never raises above it —
   applied at both anchors as the clamp target AND as a per-spawn
   `PYTEST_XDIST_AUTO_NUM_WORKERS` override. The cap = 1 edge yields 2 total
   workers (disclosed below).

The Bash hook (`hooks/guard_bash_tests.py`) caps ad-hoc pytest runs the same
two ways: the rewrite adds `regenloop_guard.py run --cap-workers` (the guard
sets the env var in the child env), and a single-statement explicit numeric
`-n` above the cap gets the ` -n {cap}` append before wrapping. Fail-open: on
ANY sizing failure the capping is skipped but admission still applies.

**No absolute-max knob.** No env var can set workers above the resource math;
the only raise path is re-parameterizing the math — `REGENLOOP_GUARD_WORKER_MB`
down (if your workers are genuinely light), the mem floor down. Any env var an
agent can set, an agent can set; the guard bounds the math, not the actor.

Known limits of the ceiling (full record and incident background:
`docs/safe-mode-worker-fanout-escape.md`):

- The cap is a point-in-time read — per-gate text clamps re-resolve it (memo
  TTL 5 s); the process-global env var is written once and does not.
- A `conftest.py` `pytest_xdist_auto_num_workers(config)` hook outranks the
  env var — a repo can defeat the cap from inside its own tree.
- `addopts` with an explicit numeric `-n N` (not `auto`): uncapped by the env
  var, invisible to transforms — workaround `REGENLOOP_PYTEST_JOBS`.
- Vitest: an explicit `--maxWorkers N` above the ceiling is not clamped in v1.
- Hook compound lines with an explicit numeric `-n` are not text-clamped
  (`auto` forms in them still are, via the env var).
- Pipelining: cap = 1 → 2 total workers; and a gate whose baseline turns out
  cached still halves the lone branch pool (safe direction).
- `REGENLOOP_GUARD_LIGHT_GATES` can allow 3 light + 1 heavy pools — worst case
  4 × cap workers (operator opt-in).
- Load average is not read; unreadable memory → conservative `min(cpu−1, 2)`.
- cap = 1 injects `-n 1` (a 1-worker xdist pool), not sequential; use `-n 0`
  spelling for sequential intent. A native-sequential
  `PYTEST_XDIST_AUTO_NUM_WORKERS=0` is likewise raised to the cap by the ≥1
  validity rule.
- `bisect_regression.py` predicates run pytest outside both nets — export
  `PYTEST_XDIST_AUTO_NUM_WORKERS`.
- Hook: a subshell shape `(pytest -n 14 )` is not recognized as a test
  command at all — the paren-head line fails test detection, so it is
  neither wrapped nor clamped (pre-existing detection boundary: no guard
  routing, no cap).
- The NON-safe injection append on compound commands still lands `-n` on the
  downstream segment (`pytest -q | tee log` → `tee` errors) — pre-existing,
  kept for byte-identity of the non-safe path; under `--safe` the append is
  suppressed instead.

### Heavy vs. light gate classification

Classification is per **command field**, not per gate — e.g.
`ui-dist-freshness`'s `--verify` check is light, its `bun run build` autofix
is heavy. In order:

1. `kind = "regression"` or a `{junit}` token anywhere in the command →
   **heavy**, unconditionally, before anything else — neither the env escape
   hatch nor the allowlist below can light-classify a real build-and-diff
   regression gate.
2. `REGENLOOP_GUARD_HEAVY_GATES` / `REGENLOOP_GUARD_LIGHT_GATES` name
   override (heavy wins on a name in both).
3. An explicit light allowlist, matched whole-invocation (never a bare script
   name): `ruff`/`uvx ruff@…`, `shellcheck`/`uvx shellcheck`, `tsc … --noEmit`,
   `design_lint.py`, `validate_plugin.py`, `ui_dist_check.py --verify`.
4. Everything else — including anything unrecognized — is **heavy** by
   default. For a safety feature the unknown case must fail toward
   protection, not toward unthrottled concurrency.

### Policies: `serial` vs. `budget`

`regenloop_guard.py run` supports two admission policies; `gate_runner.py`'s
own call sites currently always pass `serial`:

- **`serial`** (default): at most one `heavy` lease and up to
  `REGENLOOP_GUARD_MAX_LIGHT` (default 3) `light` leases held at once,
  machine-wide.
- **`budget`**: admits by a computed memory budget
  (`min(total_ram - RESERVED_MB, available - MEM_FLOOR_MB)`) instead of a
  fixed lease count. Available via `python3 scripts/regenloop_guard.py run
  --policy budget -- <command>` for an operator who wants to drive the guard
  directly; not wired into `gate_runner.py`'s own gate/autofix/regression call
  sites.

### Regression gates and `REGENLOOP_GUARD_MAX_WAIT_S`

A `kind = "regression"` gate holds **one** heavy lease across its whole
evaluation — baseline install, baseline run, and branch run — not per
subprocess, because the regression engine (`gate_runner_regression.py`) lives
in a sibling module the guard integration wraps rather than instruments
per-spawn. A repo with **several** regression gates queued behind each other
therefore has each queued gate waiting on a lease that spans that whole
multi-step evaluation rather than a single bounded command. If that queueing
routinely approaches the `REGENLOOP_GUARD_MAX_WAIT_S` default (6h), raise it —
that's a sizing problem, not a guard malfunction.

### Ad-hoc Bash test commands (`hooks/guard_bash_tests.py`)

The routing described above lives inside `gate_runner.py`, so it covers gates
and nothing else. An agent that runs `uv run pytest tests/` directly in Bash —
an implementer verifying its own change, a debugger reproducing a failure —
bypasses admission control entirely and starts an unbounded xdist pool beside
whatever else the machine is already running. With five overnight sessions that
is the original OOM, reintroduced through a side door.

`hooks/guard_bash_tests.py` is a `PreToolUse` hook on `Bash` that closes it. In
safe mode it **rewrites** the command rather than refusing it:

```
uv run pytest tests/ -q
  ->  python3 .../regenloop_guard.py run --class heavy --quiet \
          --max-wait-s 300 -- /bin/sh -c 'uv run pytest tests/ -q'
```

Rewriting beats refusing here. A refusal costs a turn and leaves a blocked agent
free to work around it — a different runner, a narrower selection, or skipping
verification. A rewrite needs no cooperation from the model, which is the only
property that survives an unattended night. `--quiet` makes the wrapping
invisible: the wrapped command's own output goes to stdout and the guard's JSON
envelope is suppressed, so the caller reads pytest's results, not a status blob.

**How the hook learns that `--safe` is on.** It cannot see the flag: `--safe` is
text in a prompt, not a process argument, and shell state does not survive
between Bash tool calls, so an `export` in one call is invisible to the next.
Two durable channels, either of which arms it:

1. **`REGENLOOP_SAFE=1` in the environment** — inherited by the Claude process
   and therefore by every hook it spawns. Export it once in each CLI config
   home's shell profile and every session from that home is armed, with no
   per-invocation flag. This is the recommended overnight setup and covers the
   case completely.
2. **A scope marker** under `$REGENLOOP_GUARD_ROOT/safe-scopes/`, named
   `sha256(absolute path)[:16]`. `/regenloop-run --safe` writes one for its run
   worktree right after creating it and clears it at §8 cleanup
   (`regenloop_guard.py safe-scope set|clear|check --path DIR`). The hook has
   `cwd` and walks up from it hashing each ancestor. Both sides derive the same
   key from the same public fact — the path — which is what makes this work
   without a session-id handshake the skill has no way to perform.

**Never wrapped** (a false positive here queues an innocent command behind a
4-minute suite, which is worse than a miss): non-test commands; cheap
invocations (`pytest --version`, `--collect-only`); anything already containing
`regenloop_guard.py` or `gate_runner.py`; and anything running with
`REGENLOOP_GUARD_ACTIVE=1`. Those last three are **re-entrancy fences, not
politeness** — wrapping something that itself takes a heavy lease is a
self-deadlock under the serial policy, bounded only by the 6-hour max-wait, not
a slowdown.

### Known limits — document, don't paper over

- **Playwright / `e2e-cockpit` internal concurrency is not capped.** It is
  classified heavy (no allowlist match) and therefore serialised one-at-a-time
  machine-wide by the guard, but the peak resource size of a *single*
  `e2e-cockpit` instance is unmeasured. Named follow-up, not yet addressed.
- The guard is **machine-local only**. It assumes a single, local,
  non-networked `$HOME`; `flock` semantics are not reliable over networked
  filesystems, so it gives no cross-machine protection — don't point
  `REGENLOOP_GUARD_ROOT` at a network share expecting it to coordinate two
  hosts.
- **The Bash hook is a guardrail, not a sandbox.** It tokenizes with `shlex`,
  so it sees through ordinary quoting, but it does not defeat a wrapper script,
  a command held in a variable, or any deliberate obfuscation. The threat model
  is a well-meaning agent running a test suite, not an attacker.
- **A hook-wrapped command can still exceed the Bash tool's ceiling.** The queue
  wait plus the run must fit inside 600 s for a foreground call. A suite that
  takes 8 minutes unwrapped can be killed at 10 after a 3-minute wait, where
  unwrapped it would have finished. Run long suites through the gate engine, or
  with `run_in_background`, which is exempt from the shortened wait.
- macOS has no cgroups, so there is no kernel-enforced memory cap here — the
  memory floor is admission control plus live-headroom backpressure, not a
  hard limit. Safe mode trades wall-clock time for stability by design; it
  does not make an individual gate use less memory.

## Gate fields (in `regenloop/gates.toml`, `kind = "regression"` gates)

| Field | Effect |
|---|---|
| `baseline_install_reuse = "node_modules"` | **B10:** opt this gate into CoW dep reuse. The value is the dep-dir NAME, relative to the gate's `chdir`. Reuse fires only when ALL hold: HEAD's dep dir exists (NEVER auto-created — regenloop never auto-installs), the lockfile BYTES match the baseline commit's (`git cat-file blob`, binary-safe for `bun.lockb`), a structural dep-health check passes, CoW is available, and the clone succeeds. On ANY doubt → the real `baseline_install` runs (never false-green). The clone is atomic (a failed clone leaves no partial tree). Disclosed residual: structural health ≠ native-binary loadability (a CoW-cloned native binding built against HEAD's state could misbehave at the baseline) — mitigated by the opt-out + B11's mutation self-check. Lockfile auto-detection searches the gate's **`chdir`** first, then the **dep-dir's parent** (e.g. `ui/node_modules` → searches `ui/`); the first `_LOCKFILE_CANDIDATES` match wins (chdir-first → zero regression for chdir-based gates). If `baseline_install_reuse` is declared but NO lockfile is found at either location, the timing block carries `reuse_no_lockfile: true` + a `reuse_hint` string naming the searched locations (repo-relative) — the self-debug path for a misconfigured gate (verdict-neutral; never flips exit code). |
| `baseline_expect_shrink = true` | **B2:** permit this gate's baseline to shrink vs the nearest ancestor (escape the crash-guard) when the shrink is legitimate. |

## Cross-platform notes
- **CoW reuse (B9)** uses `fclonefileat` (macOS/APFS, ctypes) / `cp --reflink=always` (Linux btrfs/xfs); sentinel fallback (`CoW unsupported on …` / `cross-filesystem`) on Windows/ext4/tmpfs/cross-FS — always falls back to the real install, never false-greens.
- **Cross-process provision lock (B12, `fcntl.flock`)** is POSIX-only; Windows runs without it (best-effort, with a one-time warning when a fresh install is about to run unlocked).
- **The `--safe` guard (`regenloop_guard.py`)** reads memory/swap via `vm_stat`/`sysctl` on macOS and `/proc/meminfo` on Linux — no `psutil` dependency. It is POSIX-only end to end: on Windows (no `fcntl`) it runs ungated with a one-time warning (`degraded_fcntl`) rather than blocking a gate run, the same fail-open posture as B12 above.

## Observability (B1)
Every regression-gate `timing` block carries: `wall_s`, `baseline_provision_s`, `branch_run_s`, `baseline_cache_hit`, `retries_used`, `lockfile_match`, `cow_unavailable_reason`, `install_reused`, `cow_mutation_detected`, `reuse_no_lockfile`, `reuse_hint` — pure additive metadata, never read by verdict/exit-code logic. The pre-push hook also prints its wall-time.

## Recommended operator environment (speed + disk)

For operators who prioritise throughput (and accept the extra heat), put these in
your shell profile (`~/.zshrc` / `~/.bashrc`) so every local green-gate /
regression / pre-push run picks them up:

```sh
export REGENLOOP_REGRESSION_PIPELINE=1     # B8: overlap baseline+branch on cache-miss
export REGENLOOP_WORKTREE_GC_GRACE_S=600   # B3b: reap dead baseline worktrees sooner (default 3600)
```

Leave `REGENLOOP_VITEST_JOBS` **unset** — capping vitest is thermal relief
(~1.3–2× *slower* per run); uncapped, vitest gates run full-speed sequential (not
pipelined, but each run is fastest). pytest parallelism is already `auto` by
default. The regenloop CI is unaffected (it runs sequential pytest + validate
only — no regression gates).

### Disk: CoW dep reuse — the big preventive win, where it applies

`baseline_install_reuse` (B10) doesn't just save the install time — CoW
(`fclonefileat` / `reflink`) **shares disk blocks** instead of copying them, so
a baseline worktree's dep tree costs ~0 extra bytes vs a full copy. In served
repos with large backend dep trees that's the difference between MB and GB of
`regenloop/local/.worktrees/` churn per regression run.

**Applicability rule (verified):** the engine searches for the lockfile at the
gate's **`chdir`** first (Stage A), then the **dep-dir's parent** (Stage B —
e.g. `ui/node_modules` → searches `ui/`), so the lockfile must live in one of
those two places. The simplest shape is still `chdir` pointing at the project
dir that holds the lockfile (chdir-first means zero new search for
chdir-based gates):

```toml
[[gate]]
name = "frontend-build"
kind = "regression"
tier = "full"
chdir = "ui"                              # lockfile + node_modules live here
baseline_install = "bun install"
baseline_install_reuse = "node_modules"   # dep-dir name, relative to chdir
check = "bun run build … --junit {junit}"
```

A gate whose `check` runs from the repo root while its deps live in a subdir
now fits CoW reuse too — e.g. regenloop's own `ui-build-reproduce` runs
`{python} scripts/ui_dist_check.py` from root with deps in `ui/`; the engine
searches the **chdir first, then the dep-dir's parent** for a lockfile, so
`ui/bun.lock` is found and `lockfile_match` is `true` (Stage B dep-dir-parent
fallback). Disk footprint also stays flat across runs thanks to B3a
(worktrees under gitignored `regenloop/local/.worktrees/`) and B3b
(orphan GC at provision).
