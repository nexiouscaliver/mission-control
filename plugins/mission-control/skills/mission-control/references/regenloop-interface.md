# regenloop interface — stable contract, volatile pointers, controller rules

**PLUGIN WINS.** If this file and the installed regenloop plugin disagree, the plugin is the source
of truth: follow it, record the drift in the program note as a maintainer follow-up, and re-derive
this file's citations (§10) before the next program. Derived from regenloop **1.3.1**
(verified 2026-09-17). Section markers: **[STABLE]** = zero drift 1.2.0→1.3.1, transcribed in full,
spot-check at plan; **[VOLATILE]** = ~8% churn per minor concentrated in this surface — one-line
facts ONLY, **re-derive at every plan (§10)**; **[CONTROLLER]** = our rules, absent from regenloop's
docs, stated in full. Citations resolve against the installed plugin root (RL) =
`~/.zcode/cli/plugins/cache/regenloop/regenloop/<ver>/`: `[run L157]` =
RL `skills/regenloop-run/SKILL.md:157`; `[ship]` `[GR]` `[doc]` `[rep]` = the regenloop-ship /
gate-runner / regenloop-doctor / regenloop-report SKILL.md files; `[o-state]` `[gt]` = RL `docs/`;
`[state.py]` `[budget.py]` `[guard.py]` = RL `scripts/`.

The plugin ships its own stamped knowledge package — `<RL>/regenloop/knowledge/INDEX.md` (29
cards) — if this file, a skill, or a card disagree, the plugin (skill + card) wins. The cards are
stamped (`anchor`/`anchor_hash`/`last_verified`) but summary-level: they carry no records-path
table, branch/worktree layout, budget arithmetic, or controller rules, so the [STABLE] contract and
[CONTROLLER] rules below stay transcribed here — and this file's load-bearing use is verify-side,
past where the cards' why/invariant summaries reach.

## 1. Invocation flags — [VOLATILE] re-derive at every plan (§10)

| Flag | One-line fact | Cite |
|---|---|---|
| `/regenloop-run <goal>` | positional; restated goal + SC list drive triage; auto-invokes and reuses an active goal's slug for plausible continuations | run L3, L102, L764 |
| `--deep` / `--short` | force complex/standard lane; a sanity-checklist YES forces deep **over even `--short`** | run L116, L143 |
| `--ship` | pre-authorizes §9a: push `loop/<slug>` + `/regenloop-ship` on all-gates-green; push+review only — never merges/approves, never ships red or `could_not_run`; suppresses + records the ship question | run L145-155, L626-631, L751 |
| `--no-ship` | decline the handoff without asking; recorded | run L145-151 |
| `--safe` / `--no-safe` | ON if `--safe` OR `REGENLOOP_SAFE=1`; only explicit `--no-safe` disables (`=0` never overrides); arms machine-global guard admission + Bash hook via worktree marker | run L157-175; gt L34 |
| `--slug <s>` | overrides slug derivation; must match `^[a-z0-9][a-z0-9-]*$`, ≤40 chars | run L698; state.py L57 |

The run itself always passes `--forbid-fenced --require-evidence --ledger …/ledger.jsonl
--ledger-source regenloop-run` (zero-gate green → `no_evidence`) [run L81-88, L204-208].
`--allow-meta-changes` is the only escape from the gates.toml tamper fence — requires named
operator approval, never add unilaterally [run L92; GR L66].

Controller launch form (ours; the flag semantics above are the plugin's): `/regenloop-run <mode>
<safe> <ship> --slug <slug> <goal in one sentence>` — `<mode>`=`--deep` default|`--short`,
`<safe>`=`--safe` unless the operator declined, `<ship>`=`--ship|--no-ship` per the host/ship
policy (§9). Re-derivation source for this table: `knowledge/cards/regenloop-run.md` (index:
`knowledge/INDEX.md`).

## 2. Goal lifecycle — [STABLE]

- **Creation:** slug derived (lowercase, `-`, `[a-z0-9-]`, ≤40 chars; `loop-<UTCts>` fallback);
  `--slug` overrides. `regenloop_state.py start "$slug" --root "$ORCH_ROOT"` creates
  `goals/<slug>/{queue.md,memory.md}` — idempotent, never overwrites [run L696-724; o-state L81].
- **Collision guard (before `start`, always):** `start` silently reuses an existing dir;
  `goals/<slug>/` present and not in `_archive/` → resume or `--slug <new>`, never `start`.
  **Provenance gate (reusing any slug):** first line of `queue.md` = `# Queue: <slug>`, first line
  of `memory.md` = `# Memory: <slug>`, slug matches `SLUG_RE` [run L700-720].
- **Base capture:** `BASE_SHA=$(git rev-parse HEAD)` — the operator's **current branch**, not
  origin — plus `LAUNCH_BRANCH`, persisted in the same Bash call via `state.py set`; `ORCH_ROOT`
  derives from the git common dir (never `--show-toplevel`); `set`/`get` refuse relative `--root`
  [run L450-471, L426-438; o-state L86-89].
- **State files per goal:** `queue.md` (9-column task table, SC-n acceptance citations),
  `memory.md` (terse lines, 16 KB soft cap), `budget.json` (counters + caps — observed shape
  `{slug, whole_run, gates}`; no `resets` key — resets are run-SKILL's mechanism, governed
  per §6: per-gate retry cap 3 via run's `--cap-gate 3` [run L215];
  reset-abuse guard via run's `--max-resets-per-gate 1` [run L215]), `record.json`
  (`base_sha`, `launch_branch`, `ship_tier`, `ship_round_base`) [o-state L9-26, L30-42, L52-57].
- **Finish:** only after `validate-queue` exit 0 AND `ledger.py verify --expect-min-total 1`;
  `state.py finish <slug> --outcome "<text>"` moves `goals/<slug>/` → `_archive/<slug>/` and
  appends one INDEX row; idempotent; INDEX self-heals [run L546-552, L769; o-state L83, L112-114].
  INDEX rows are `| date | slug | outcome |` (ISO `%Y-%m-%dT%H:%M:%SZ`) [o-state L44].
- **`example-goal`** is init scaffolding — never list, resume, or validate it as real [run L708].
- **Active-goal hook** reports only a **count** of active goals into prompt context, never slugs
  [run L712]. The goal stays active through the whole ship window; `decision: shipping-started
  <branch>` in memory.md makes a mid-ship death resumable [run L641-648].

## 3. Machine-written records (verify's evidence surface) — [STABLE]

All under the **main checkout's** gitignored `regenloop/local/` — per-machine, invisible to CI, no
cross-machine continuity [o-state L144-156].

| Record | Path (under `<repo>/regenloop/local/`) | Identity stamp | Cite |
|---|---|---|---|
| Goal state | `orchestrator/goals/<slug>/{queue.md,memory.md,budget.json,record.json}` | slug dir + provenance headers | o-state L9-26; run L714-716 |
| Fast gate report | `green-gate/goals/<slug>/last-report.json` | own `head_sha` + `base` — must match the run | run L202-208, L440-448 |
| Full gate report | `regression/goals/<slug>/last-full-report.json` | same | run L263-269 |
| TDD receipts | `tdd/<task-id>-{red,green}.json`; check via `gate_runner.py --verify-tdd` | `head_sha` lineage (green descends from red) | run L304-325; GR L82-101 |
| Ledger | `ledger.jsonl` (JSON-lines, per-run `source`); read via `ledger.py report [--since N]` | per-run entries | run L87, L549; rep L20-27 |
| Archive | `orchestrator/_archive/<slug>/…` + `INDEX.md` rows | append-only | o-state L19-25, L44 |
| Ship artifacts | MR threads/comments (`mr_notes.py`; the MR is the record); `green-gate/mr-$IID-round-<N>.json`, `mr-$IID-final.json` | MR IID + round | ship L208-210, L683, L784 |
| Guard events | `$REGENLOOP_GUARD_ROOT/events.log` (default `~/.regenloop/guard/`), lease tickets | ticket ids | guard.py L158, L840; gt L38 |

## 4. Branch / worktree layout — [STABLE]

- Run branch `loop/<slug>`, forked from the operator's **current HEAD**, not origin [run L483, L465].
- One run worktree `$MAIN_ROOT/.claude/worktrees/loop-<slug>` (must sit under `.claude/worktrees/`);
  one per run, whole run; sequential writers are NOT isolated [run L480-506, L420-424].
- `MAIN_ROOT` = parent of the git common dir; `ORCH_ROOT = $MAIN_ROOT/regenloop/local/orchestrator`;
  state is always absolute-rooted [run L426-438].
- Ship review worktree `$MAIN_ROOT/.worktrees/ship-review-$IID` (detached) [ship L412, L1254-1265];
  the §9a handoff runs BEFORE §8 cleanup — it needs the live worktree; each skill sweeps only its
  own worktrees [run L619-663, L671-672].
- `EnterWorktree` persists the session cwd; `ExitWorktree(keep)` at finish; git ops via `git -C`
  [run L510-515]. Heartbeat at every cycle boundary, 6 h GC grace [run L517-525].

## 5. Ship — scope, rules, verdicts — [STABLE]

- **Scope:** green non-default branch → push, Draft MR (`glab mr create`), 2 concurrent read-only
  reviewers, triage, fix, re-gate, verify, CI wait, promote or stay Draft, merge check.
  **GitLab-only** (`glab auth` + GitLab remote) [ship L3-16, L346-347, L439-453; run L633-634].
- **Hard rules:** never merge, never approve (hook-enforced `hooks/mr_merge_guard.py`); never edit
  `gates.toml`/`never_touch`; never promote past red/`could_not_run`/unfinished CI; never fix below
  triage `VALID`; one commit per fix round; unresolved findings ride as notes on a Ready MR
  [ship L20-50]. `MAX_ROUNDS = 3`, constant, shared with target-absorption re-merges; round number
  derived from MR `round-<N>` markers, not memory [ship L184, L878-881, L957-959].
- **Preconditions (§3):** non-default branch, clean `git status`, commits ahead of fork point,
  local fast-tier gate green, `meta_config_changed` empty [ship L214-287]. **Handoff:** reads
  `record.json` keys; `get` exit 0 + empty value or unreadable = broken handoff → stop loudly;
  exit 3 = standalone run [ship L53-161].
- **Verdicts:** triage `VALID`/`INVALID`/`UNRESOLVED` (+confidence; UNRESOLVED escalates, then
  demotes to note) [ship L493-510]; CI `ok`/`none` (exit 0) / `bad` / `blocked` / `pending` /
  `timeout` (exit 3) [ship L1073-1087]; merge-check per-finding `APPLIED|SILENTLY_APPLIED|
PARTIALLY_APPLIED|NOT_APPLIED|NEEDS_HUMAN`, overall `SAFE_TO_MERGE|MERGE_WITH_CAUTION|
DO_NOT_MERGE` (→ back to Draft) [ship L1200, L1235, L1243-1244].
- **Promotion:** gates green AND CI ok/none → `--ready`; else Draft with reason; newly added tests
  must be mutation-proven [ship L1094-1105, L728-730]. **Target movement:** merge-never-rebase;
  `AHEAD > 0` → absorb and restart verification [ship L883-983].

## 6. Convergence and gates — [STABLE core; CNR re-probe numbers VOLATILE]

- `could_not_run` = unsatisfied precondition → STOP, **no budget consumption**, no fix loop,
  escalate; `fail` → fix/re-commit/re-gate loop [run L212, L281-288; GR L48]. Transient-CNR engine
  self-retry (`REGENLOOP_CNR_REPROBE`=2 × 30 s) — one-liner, re-derive per card
  `knowledge/cards/regenloop-run.md` [run L285].
- Budgets: per-gate cap 3, run cap 12 (`CAP_RUN = max(12, 6 + Σ per-task caps)` when architect
  scored it), 1 reset per gate, reset only on green, `whole_run` never reset [run L364-397;
  budget.py L50-52]. Exit 0 = continue; 2 = stop → operator escalation; 1 = script error → cleanup
  + `finish --outcome "partial: budget-script error"` [run L372-379]. Stop reasons:
  `gate_cap_exceeded`, `run_cap_exceeded`, `reset_abuse_suspected` (never re-reset, never raise
  the cap) [run L383, L397].
- **Budgets are cooperative** (prose-mandated ticks); only gate exit codes are hard [run L406-408].
  → Controller corollary: a forged prompt adds only a wall-clock bound, never a competing retry rule.
- Done-condition: all closing gates green AND SCs met AND reviews pass AND (deep) e2e verdict or
  recorded `surface-excluded: e2e — <reason>` [run L356, L279].

## 7. Clarify and human gates — [STABLE; approval-timeout default VOLATILE]

- Ship question asked at Clarify only (unless flags given / already recorded); recommended default
  **No — stop at the push command**; the deep plan-approval restates it, never re-asks
  [run L106-114, L247-256]. Unattended Clarify does not block: recommended defaults are adopted as
  recorded `assumption:` entries; answered questions are never re-asked [run L114].
- Short mode's human gate is suppressed only by a critic's literal `Verdict: proceed` [run L177-181].
- The deep plan-approval gate is the ONLY blocking human gate; pre-approved when the operator
  answered **unattended** at Clarify (earned suppression) [run L230-234]. Headless approval is
  bounded (default 1800 s; exit 11 NO_APPROVER = treated as denial) — re-derive per card
  `knowledge/cards/regenloop-run.md` [run L236-245].
- Doctor preflight: `regenloop_doctor.py .` exit 1 → STOP before mode selection/worktree/dispatch
  [run L57-65; doc L106-109].

## 8. Resource truth — [VOLATILE] re-derive at every plan (§10)

- `--safe` arms a machine-global guard shared by EVERY regenloop session on the host regardless of
  config home; heavy gates serialize machine-wide across all of them [run L157-175; gt L20-31].
- Requests are honored ahead of the resource-derived ceiling but clamped never past it; pytest
  chain `REGENLOOP_PYTEST_JOBS` → `REGENLOOP_SAFE_PYTEST_JOBS` → cap [gt L83-113].
- Full env-var table lives in `docs/gate-tuning.md` (grew 84→353 lines across two minors — treat
  any memory of it as stale): `REGENLOOP_GUARD_ROOT` (`~/.regenloop/guard`), `REGENLOOP_GATE_
  TIMEOUT_S` (900 s/check), `REGENLOOP_APPROVAL_TIMEOUT`, `SHIP_BASE`/`SHIP_TARGET`/`SHIP_TIER`/
  `SHIP_ROUND_BASE`, ~16 more knobs [gt L13-49, L83-97; ship L110-130]. Enumerate via `[gt]` and
  the scripts card `knowledge/cards/scripts.md` — grep, never recall.
- `REGENLOOP_GUARD_ACTIVE` is set by the guard only — never by hand [gt L47].
- Query, never recall: `regenloop_guard.py plan --json` (§10). 2026-09-17 on this machine: cap 1
  (loaded) → parallel lanes serialize on heavy gates today.

## 9. Verify-side knowledge — procedure is SKILL §4, commands are `references/verify-runbook.md` — [CONTROLLER; paths STABLE]

Machine-written records check the session's prose. Per slug, in this order, BEFORE any hand re-run:

1. `orchestrator/_archive/INDEX.md` — is there a row for the slug? A goal still in `goals/` after
   a "done" claim = cleanup never ran.
2. `record.json` (`base_sha`, `launch_branch`, `ship_tier`) — via the archived dir or
   `regenloop_state.py get` (exit 0 = exists, 3 = absent, 1 = unreadable) [o-state L86-89].
3. **Base drift tiers (ours; equality with the forge-time sha is NEVER the verify criterion):**
   apply the anatomy item-4 tiers (`references/prompt-anatomy.md`) — never silently self-correct
   a base.
4. Slug-scoped gate reports — `regression/goals/<slug>/last-full-report.json`,
   `green-gate/goals/<slug>/last-report.json`; confirm the report's own `head_sha`/`base` match
   this run's record (per-slug scoping exists precisely because a fixed path was a race).
5. `queue.md` terminal row statuses → `ledger.jsonl` (a run that wrote no ledger row is itself a
   defect) → `budget.json` tallies vs caps (§6).
6. Only then the external checks; a hand suite re-run is reserved for merged trees (no machine
   report covers the merge result) and for mismatches with the reports.

**Controller-side lane rules (ours, in full):**
- **Mode is a recorded per-lane decision:** `--deep` default; `--short` only for trivially-scoped
  packages (few files, no cross-module risk); never an unexamined constant.
- **`--slug` join discipline:** `--slug <slug>` appears verbatim in every invocation line and in
  the program note's prompt-log row — the join key between note, goal dir, branch, reports, and
  archive row. Never "suggest" a slug in prose.
- **Per-host ship policy:** decide at forge from `git remote -v`. `--ship` only where ship can run
  (GitLab remote + `glab auth`); where it cannot, ship degrades to surfacing a bare push command —
  an unreviewed branch, no PR. GitHub-hosted repos get `--no-ship` + an explicit human/`gh` PR path
  written into the prompt. Never write host-specific prose without checking the remote.
- **An active foreign goal is a continuation-misroute risk, not a stall:** different slugs run
  concurrently; auto-invocation reuses a plausible slug — pin `--slug` and check the provenance
  gate (§2) after kickoff. A routing check, never a kickoff STOP.

## 10. Re-derivation procedure + machine checks — [CONTROLLER]

Run at every program's plan, before the first forge in a session, and at every regenloop minor
bump; record `envelope: regenloop <ver> (verified <date>, <RL path>)` + `skill: mission-control
<ver>` in the program note:

```bash
RL_BASE="$HOME/.zcode/cli/plugins/cache/regenloop/regenloop"
RL_VER=$(ls "$RL_BASE" | sort -V | tail -1); RL="$RL_BASE/$RL_VER"
grep -m1 '"version"' "$RL/.claude-plugin/plugin.json"          # version, never assume
# Re-read, in order: skills/regenloop-run/SKILL.md (§0 flags/env, §2 Clarify+triage, §8 base
# capture+worktree+cleanup/finish, §9 ship handoff, §10 slug+lifecycle); skills/regenloop-ship/
# SKILL.md (rules, MAX_ROUNDS, verdicts); docs/orchestrator-state.md; docs/gate-tuning.md;
# knowledge/INDEX.md + cards regenloop-run/regenloop-ship (plugin surface).
grep -n "REGENLOOP_" "$RL/docs/gate-tuning.md" | head          # env names — grep, don't recall
python3 "$RL/scripts/regenloop_guard.py" plan --json           # machine worker ceiling → caps
python3 "$RL/scripts/regenloop_doctor.py" <lane-repo> --json   # exit 1 = fix env before forging
python3 "$RL/scripts/regenloop_state.py" status --root <repo>/regenloop/local/orchestrator
```

Mismatch policy: if the installed version ≠ 1.3.1 above, re-read the four files plus
`knowledge/INDEX.md` and the cards `regenloop-run`/`regenloop-ship` (cards count as plugin
surface), re-confirm every [VOLATILE] row (§1, §6 CNR, §7 timeout, §8), spot-check three [STABLE]
citations, and follow the plugin wherever this file disagrees. A Derived-from version older than
the installed plugin is a hard stop for forging, not advisory. All six commands verified clean
against 1.3.1 on 2026-09-17.

## 11. Negative scope — controller's HEAD vs forged prompt — [CONTROLLER]

This file never ships in a forged prompt; the forge-side allow-list has one home: anatomy §0.
PLUGIN WINS governs disagreement.
