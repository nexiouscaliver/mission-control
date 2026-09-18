# Orchestrator state — layout, lifecycle, and model routing

Per-goal working state for the `architect` orchestrator: where it lives, how to manage it with `regenloop_state.py`, what migrating from the legacy path does, and the model-routing rules the orchestrator applies on every dispatch.

---

## 1. State layout

```
regenloop/local/orchestrator/        ← gitignored; per-developer/per-machine
  project.md                        ← cross-goal facts; never archived
  goals/
    <active-slug>/
      queue.md                      ← this goal's task rows only
      memory.md                     ← this goal's working facts only
      budget.json                   ← this goal's run/cycle budget counters
      record.json                   ← durable key/value carrier across a skill boundary
  _archive/
    <done-slug>/                    ← goal directory moved here verbatim on finish
      queue.md
      memory.md
      budget.json
      record.json
    legacy-<ts>/                    ← legacy state tree moved here verbatim on migrate
    INDEX.md                        ← one row per archived goal or migration event
```

The directory does not exist on a fresh clone — `regenloop_state.py start` creates it on first use.

### What each file holds

**`project.md`** — curated, cross-goal facts: architecture invariants, conventions, pinned versions. Pruned, not appended: when a fact changes, replace the line; never leave stale entries. Soft cap **8 KB**.

**`goals/<slug>/queue.md`** — the task table for the active goal. Columns: `ID | Task | Lane | Model | Status | Depends on | Acceptance | Result | Notes`. Updated in place; status moves from `pending → active → done/dropped`. The **`Model`** column holds the tier assigned at Decompose — the frontmatter default for the row's agent (Sonnet/Haiku), or `opus` on a reviewed complex-implementer escalation. It records the decision for the critic's plan-gate review; it does **not** by itself mean "pin this dispatch" (see §5).

**Acceptance entries cite a success condition.** Every Acceptance cell MUST cite the success condition it satisfies using the convention `SC-n: <testable statement>` (e.g. `SC-2: rate limit returns 429 after 100 req/min`), where `SC-n` refers to the numbered success-condition list the architect's Intake Goal Sanity Check writes to `memory.md`. `validate-queue` (below) enforces both the citation and that the statement is concrete — `"works"` or `"looks good"` fails validation.

**`goals/<slug>/memory.md`** — per-goal scratchpad: decisions, gotchas, open questions. One terse line per fact — no essays. Soft cap **16 KB**.

**`goals/<slug>/budget.json`** — this goal's run/cycle budget counters, written by `scripts/regenloop_budget.py` (`_budget_path`). Archived with the rest of the goal directory when `finish` moves it to `_archive/<slug>/` — `finish` moves the whole directory verbatim, so `budget.json` requires no separate handling.

**`goals/<slug>/record.json`** — durable key/value carrier for values that must cross a skill boundary (`base_sha`, `launch_branch`, `ship_tier`), written by `set` and read by `get`. Shell state does not survive between Bash tool calls, so `/regenloop-run` persists these at the moment it captures them and `/regenloop-ship` reads them back by command. Archived with the rest of the goal directory at `finish`, which is what makes a post-`finish` ship the standalone case.

**`_archive/INDEX.md`** — append-only ledger written by `finish` and `migrate`. The script writes rows in this column order: `| date | slug | outcome |`. (`finish` uses ISO format `%Y-%m-%dT%H:%M:%SZ`; `migrate` uses compact `%Y%m%dT%H%M%SZ` — they legitimately differ.)

### Terse-entry rule

State entries are **one line each** — no multi-paragraph explanations. A superseded fact is replaced, not appended. Append-only essays are an explicit anti-pattern; they inflate context on every load.

### Size caps and warnings

| File | Soft cap | `status` warns at |
|------|----------|-------------------|
| `project.md` | 8 KB | ≥ 80% (≈ 6.4 KB) |
| `goals/<slug>/memory.md` | 16 KB | ≥ 80% (≈ 12.8 KB) |
| `goals/<slug>/queue.md` | none | — (size reported, not capped) |

When a warning fires, prune before adding new content.

### Load only the active goal

On each session the orchestrator loads `project.md` and the **active goal's** `queue.md` + `memory.md` only. Archived goals stay in `_archive/` and are never loaded into context. This is the primary mechanism keeping the context window bounded across a long project.

---

## 2. `regenloop_state.py` commands

Resolve the script path the same way other regenloop skills do:

```bash
LS="${CLAUDE_PLUGIN_ROOT:-}/scripts/regenloop_state.py"
[ -f "$LS" ] || LS=$(find "$HOME/.claude/plugins" -name regenloop_state.py -path '*regenloop*' 2>/dev/null | head -1)
```

Run from (or point at) the **served repo root**, not the plugin directory. The `--root` flag sets the orchestrator root directory; it defaults to `regenloop/local/orchestrator` relative to the working directory.

### Subcommand reference

| Subcommand | Flags | What it does |
|---|---|---|
| `start <slug>` | `--root PATH` | Creates `goals/<slug>/queue.md` as a valid empty pipe table (the `QUEUE_HEADER` row + GFM separator, zero data rows — so a fresh goal validates clean and parses as a zero-row table) and `memory.md` with a minimal header. Idempotent: existing files are never overwritten. Also creates `project.md`, `_archive/`, and `_archive/INDEX.md` if absent. |
| `validate-queue <slug>` | `--root PATH [--prev-snapshot PATH]` | Validates `queue.md` against the schema rules below; prints a JSON violations list to stdout. Exit `0` = clean, `1` = violations found, `2` = file unreadable/unparseable. With `--prev-snapshot`, additionally flags any row whose `Status` moved backward since the snapshot (e.g. `done` → `active`) as a regression. |
| `finish <slug> --outcome "TEXT"` | `--root PATH` | Moves `goals/<slug>/` → `_archive/<slug>/` via `shutil.move` (both `queue.md` and `memory.md` move together). Appends one row to `_archive/INDEX.md`. Never touches `project.md`. Now also warns (does not block) if any row is still non-terminal — field `non_terminal_rows` lists rows whose `Status` is neither `done` nor `dropped` at finish time. |
| `status` | `--root PATH` | Prints sizes for `project.md` and all active goal files. Emits `[WARN: approaching cap]` on `project.md` or any goal's `memory.md` past 80%. |
| `migrate --source PATH` | `--root PATH` | Moves the entire legacy source tree → `_archive/legacy-<ts>/`. Default source: `.claude/orchestrator`. See §3. |
| `set <slug> <key> <value>` | `--root PATH` (**absolute**) | Writes a durable key/value into `goals/<slug>/record.json`. Idempotent, last-write-wins. Exit `0`; `1` on an invalid slug/key, a non-absolute `--root`, or an unwritable root. |
| `get <slug> <key>` | `--root PATH` (**absolute**) | Prints the stored value on stdout. Exit `0` when the key exists (**including an empty value, which prints an empty line**), `3` when the record or key is absent, `1` on an invalid slug/key, a non-absolute `--root`, or an unreadable record. The 0-vs-3 split is what lets `/regenloop-ship` tell a standalone run (no record → fall back) from a broken handoff (record present but empty → stop). |

**`set`/`get` refuse a relative `--root`, unlike the other subcommands.** Their callers are `/regenloop-run`'s capture block and `/regenloop-ship`'s §2, both of which run with an ambient cwd that may be a run worktree. A relative root there writes `./goals/<slug>/record.json` into the worktree, reports success, and disappears at `git worktree remove` — while the reader, resolving the real root, sees "absent" and silently falls back to the default branch's merge-base. Both derive an absolute root from the git **common** dir (`git rev-parse --path-format=absolute --git-common-dir`), so a non-absolute root is always a bug and is refused rather than half-honoured.

### `validate-queue` — the queue's enforcement mechanism

`queue.md` is prose, not a database — without a validator, a stray column, a typo'd `Status`, or a vague Acceptance entry silently corrupts the durable cross-session source of truth. `validate-queue` is that check, and both the `architect` and `regenloop-run` skills MUST run it at Integrate and before declaring a goal done (see their own docs for exactly when).

**Schema rules it enforces:**
- Exactly 9 columns, in order: `ID | Task | Lane | Model | Status | Depends on | Acceptance | Result | Notes`.
- `Status` ∈ `pending`, `active`, `done`, `dropped`.
- `Lane` ∈ `trivial`, `standard`, `complex`.
- `ID` values are unique within the file.
- Every `Depends on` reference points at an `ID` that exists in the same file.
- `Acceptance` is non-empty and non-vague for any row whose `Status` is `active` or `done` — it must cite an `SC-n:` success condition (see §1) with a concrete, testable statement; generic text like `"works"` fails.
- `Result` is non-empty for any row whose `Status` is `done`.
- **Difficulty marker (optional, Rule 10):** when a row's `Notes` cell carries a `D<total>[dddddd]` marker (six digits 0–2, one per rubric factor — scope, boundary, novelty, risk, ambiguity, coverage; written at Decompose per the architect skill's Difficulty rubric), the digit sum must equal the total (`difficulty_marker_invalid` otherwise) and the row's `Lane` must be **at or above** the rubric's lane floor (`lane_below_difficulty` otherwise). The floor, computed by `difficulty_lane_floor`: `risk == 2` or `total ≥ 7` → `complex`; `total ≥ 3`, `scope ≥ 1`, `risk ≥ 1`, or any factor `== 2` → `standard`; else `trivial`. A scored row with an **empty** `Lane` is also flagged (`lane_below_difficulty`) — a marker whose floor can't be checked defeats the audit. Re-triage **replaces** the marker in place (never append a second); if multiple markers are nonetheless present, the **last** one governs. Escalating a lane above the floor is always allowed; rows without a marker are unaffected (backward compatible).
- **Per-task convergence caps** are lane-scaled (trivial 2 / standard 3 / complex 4) with a near-ceiling **+1** bump when the marker's total is one point from the next lane's floor, computed by `per_task_cap`. See `skills/architect/SKILL.md`'s "Near-ceiling cap bump" for the canonical rule (not re-derived here).

**Exit codes:** `0` clean, `1` one or more violations (returned as a JSON list — one entry per violation, naming the row and the rule broken), `2` the file itself could not be read or parsed.

### Slug format

Slugs must match `^[a-z0-9][a-z0-9-]*$` — lowercase alphanumeric and hyphens, starting with alphanumeric. The script rejects slugs that don't match before touching any path (blocks path traversal).

### finish is idempotent; INDEX self-heals on crash

If `finish` is re-run on an already-archived slug, it skips the move and prints a warning to stderr. If a previous run crashed between the directory move and the INDEX append, `finish` detects the missing row and adds it — so the audit trail is always complete.

### Never deletes

Both `finish` and `migrate` use `shutil.move`. Nothing is ever deleted. To inspect historical state, look in `_archive/`.

---

## 3. Migration from the legacy path

### `regenloop_state.py migrate` — orchestrator state only

Relocates `.claude/orchestrator/` (the old git-tracked location) to `regenloop/local/orchestrator/_archive/legacy-<ts>/`. The entire legacy tree is moved verbatim; no content is merged or reformatted. One INDEX row is appended: `migrated from .claude/orchestrator`.

```bash
python3 "$LS" migrate
# or with explicit paths:
python3 "$LS" migrate --root regenloop/local/orchestrator --source .claude/orchestrator
```

If the source directory does not exist the command exits cleanly with "Nothing to migrate".

### `regenloop_init.py migrate` — knowledge package, gates, and hooks

A separate migration command in `regenloop_init.py` relocates `.claude/{gates.toml,knowledge/,init/,loops/,hooks/}` into `regenloop/`. It **never touches `.claude/orchestrator/`** (verified: the word `orchestrator` does not appear in `regenloop_init.py`).

**These two migrations are fully disjoint** — different source paths, different destination paths, different scripts. Run them in either order; they cannot interfere. A repo in mid-migration (one done, the other pending) is safe.

---

## 4. Committed → gitignored trade-off

Before this redesign, orchestrator state lived under `.claude/orchestrator/` and was committed to the repo. It is now under `regenloop/local/orchestrator/`, which is gitignored.

**What this means:**

- State is per-developer and per-machine, like `regenloop/local/green-gate/STATE.md` and `regenloop/local/regression/STATE.md`. It does not appear in CI and is not shared across the team.
- A fresh clone or a new developer starts with no orchestrator state. Run `regenloop_state.py start <slug>` to initialize a goal.
- There is no cross-machine continuity of `queue.md` or `memory.md`. If you need a teammate to pick up a goal mid-flight, transfer the state files manually or commit a summary to a tracked location.

**Why this was chosen:** orchestrator state is scratch/working state. The old committed location caused merge conflicts and polluted the shared history with machine-specific working notes. The `regenloop/local/` home is consistent with every other piece of regenloop working state.

**This decision is reversible.** If team-sharing of orchestrator state becomes a real need, the root path can be changed to a committed location. The file layout and `regenloop_state.py` interface are identical either way.

---

## 5. Model routing

Each subagent's tier comes from its **own frontmatter `model:`**, which regenloop relies on as the default. The `architect` skill assigns the tier at Decompose and records it in the `queue.md` `Model` column; the orchestrator only passes a `model:` param when a task diverges from the frontmatter default.

### Frontmatter is the default — don't pin

A subagent's frontmatter `model:` is authoritative on dispatch: an unpinned `regenloop:critic` runs Sonnet and an unpinned `regenloop:test-runner` runs Haiku, regardless of the session tier (verified on Claude Code 2.1.197). So the default is to pass **no** `model:` param — the agent runs its declared tier, and forgetting to pin is safe.

The full resolution order (highest → lowest) is: (1) `CLAUDE_CODE_SUBAGENT_MODEL` env var; (2) per-invocation `model:` param; (3) subagent frontmatter `model:`; (4) session model. regenloop uses level 3 for defaults and level 2 only to escalate. This requires Claude Code **≥ 2.1.196** with `CLAUDE_CODE_SUBAGENT_MODEL` unset (or not `inherit`); older builds short-circuit levels 2–3 to the session tier — the historical reason subagents appeared to "always run on the session model."

### Session tier — Sonnet by default, Opus opt-in per dispatch

Run the session on **Sonnet** by default. You do **not** need an Opus session to reach Opus: a Sonnet session **can** dispatch an Opus subagent via a `model: opus` param — subject to your org's `availableModels` allowlist. If Opus is excluded from that allowlist the dispatch **silently** falls back to the session model (no error), so on a restricted org, run the *session* on Opus for a known-hard goal. Either way the orchestrator's own reasoning runs at the session tier; that cost is only reduced by choosing a Sonnet session.

### Frontmatter tier table (defaults — not per-dispatch pins)

See `skills/architect/SKILL.md`'s "Frontmatter tiers" table for the canonical
persona → tier mapping (dispatch each persona as `regenloop:<name>`, per below).

### The only two dispatches that take a `model:` param

- **`Explore`** (built-in recon) has **no** regenloop frontmatter, so unpinned it inherits the session tier — always dispatch it `model: haiku`.
- **Opus escalation** — a complex/risky implementer, raised with `model: opus` after the plan is reviewed.

Reserve Opus for where judgment compounds; it is not a default for any role. **Optional cost ceiling:** setting `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` forces every subagent to Sonnet regardless of frontmatter or param (and disables Opus escalation).

### Dispatch by plugin-qualified name

Always dispatch agents as `regenloop:<name>` (e.g. `regenloop:spec-writer`, `regenloop:code-reviewer`). Other installed plugins may register agents with the same bare name — qualified dispatch eliminates the ambiguity. Verified: `architect-orchestrator:critic`-style qualified dispatch works at runtime; `regenloop:*` resolves identically once regenloop is installed as a plugin.
