# Mission Control — the discuss phase

Design doc of record for `/mission-control-discuss`, the intake phase that runs **before** `plan`.
2026-10-06, branch `main`. Companion file: `docs/mc-discuss-command-draft.md` (copy-ready command
file, copy-ready SKILL section, and the registration checklist). Status: design — nothing here is
wired yet; every wiring edit is enumerated in the companion checklist.

Style note: this doc follows the repo's own design-doc precedent (`wall/docs/design-signal-panel.md`)
— frozen contracts stated verbatim, tables over prose, every load-bearing claim mapped to a source in
§10.

---

## 1. Problem and philosophy

**The requirement (operator's ask, distilled).** Today the operator describes his requirement in
chat, makes every decision manually — what to use and what not — and figures out everything himself,
including the loopholes and blind spots in his own idea. That manual self-review is where bugs and
"inconsistent with what I had in mind" outcomes are born. The new phase must: (1) take the raw spoken
idea and act as a companion that reinforces the idea AND grills him as well as or better than a
demanding human reviewer would; (2) scan the target codebase with subagents for a reality check of
how the idea can actually fit; (3) discuss again after that reality check; (4) write an idea markdown
file into the memory vault that the plan phase consumes. Sole goal: refine the idea in his head into
a real md file — reality check done properly, loopholes covered, end-to-end workflow from the
user-facing side to the back end included.

**Where the requirement enters the lifecycle today.** Only as the typed argument of
`/mission-control-plan <objective>` (`commands/mission-control-plan.md:3,8`); SKILL §2 is headed
`plan <objective>` and records it into the program note at step 6. There is no requirements file, no
vault note, no interrogation step anywhere upstream (`skills/mission-control/SKILL.md:5,44`). The
recorded cost of that gap is concrete: load-bearing constraints surfaced mid-flight as an OOM crash
(a single-thread mandate authored by the operator *after* the crash), a plan that never SSH-checked
the production box and failed on wrong credentials two days later, a platform-pin shape mismatch
that fail-closed a downstream lane at canary, and plan-carried formulas rejected on evidence
(session-archaeology research stream, §10-B). Every one of those is a question discuss should have
asked before plan.

**The companion duty — reinforce AND grill.** Two obligations, both mandatory, in this order each
round:

- **Reinforce (steelman).** Find the strongest version of the idea: restate it in one sentence plus
  numbered, testable success conditions; make every dimension concrete; when the operator pushes
  back with "this feels wrong", re-examine the framing — root-cause reframe plus the full option
  space with one marked recommendation — never a hardened version of the same design
  (research-stream citation, §10-B: `user-brainstorm-pushback-reframe`).
- **Grill (attack).** Lead with the single strongest objection. Agreement without a real attempt to
  break the idea is a failed round — the exact doctrine the operator's own critic agent carries: "A
  response that simply agrees is a failed review" (`~/.zcode/cli/plugins/cache/regenloop/regenloop/1.3.1/agents/critic.md:8`,
  research-stream citation). Challenge at least **3 assumptions or claims per interrogation round**,
  each with the evidence that motivates it, and hunt contradictions across the operator's own
  statements (this round vs earlier rounds vs recorded lessons and vault user-notes).

**Anti-yes-man is structural, not tonal.** The guardrails are countable and checkable: ≥3 named
challenges per round (strongest first), every meaningful decision names the alternative it rejected,
every adopted default is recorded as an assumption in the idea note, and a round that produces zero
challenges must say so explicitly ("no challenges survived this pass") rather than fall silent. A
missing challenge count is a defect in the round, the same way a missing red-team record is a defect
in a forged prompt (`SKILL.md:62`).

**What discuss is not.** Not plan: no waves, lanes, caps, program note, or wall registration — that
machinery needs plan's recon and headroom derivation and stays there (`SKILL.md:47-54`). Not a Wall
surface: prompt-log rows are born at forge and pre-forge placeholder rows are forbidden by design
(`SKILL.md:34`); an `idea-*.md` file is a silent non-candidate for wall boot discovery — discovery
scans `programs/` and declared `note_glob` dirs with a program filename regex, and its degraded
reject lines fire only for program-named notes, never for differently-named files (research stream,
§10-B: `wall/docs/discovery.md:13-16,31-36`). Not mandatory: `plan` keeps accepting a raw objective
verbatim — discuss is the front door, not a gate (open decision D5).

---

## 2. Command contract

| Property | Value |
|---|---|
| Command | `/mission-control-discuss <idea \| idea-slug>` |
| File | `plugins/mission-control/commands/mission-control-discuss.md` (filename IS the name — commands carry no `name` field, `scripts/verify_packaging.sh:24-32`) |
| Frontmatter | exactly `description` + non-empty `argument-hint` (case 04 enforces both, `tests/cases/04-command-surface.sh:16-21`) |
| Argument | `$ARGUMENTS` = the raw idea in the operator's own words (prose, typos welcome — his kickoffs are one-liners with typos, research stream §10-B), **or** an existing idea slug to resume an open idea note |
| Base-skill alias | `/mission-control discuss <idea>` — SKILL frontmatter description + argument-hint widen to `<discuss\|plan\|prompts\|verify\|next\|close> [idea \| objective \| program-slug \| session/MR reference]`; case 04 token-set-compares this hint against its MODES list, so `tests/cases/04-command-surface.sh:8` grows `discuss` in the same edit |
| Position | First command of the lifecycle. Pipeline line gains a leading stage: `DISCUSS → INTAKE → TRIAGE → …` (current text at `README.md:7-12`) |
| SKILL home | New section `## §2 discuss` with plan→§3 … Always→§9 renumbered (recommended, open decision D1); the copy-ready section text lives in the companion draft file |
| References loaded | `references/lessons.md` (the failure catalogue the interrogation hunts for) + `references/persona-gate.md` (advisory rulings on operator-facing forks). Both exist; discuss forges nothing, so `references/prompt-anatomy.md` and `references/regenloop-interface.md` are NOT loaded (they are plan/prompts surfaces, `SKILL.md:15`) |

**Session and state handling.**

- **Exists at discuss time:** nothing. No program note (created at plan, `SKILL.md:23`), no rows
  (born at forge, `SKILL.md:34`), no wall registration (per-program, at plan, `SKILL.md:51-53`).
  Discuss mints exactly one artifact: the **idea note**.
- **Written incrementally, not at the end.** The idea note is created (`write_note`) after the first
  interrogation round completes — round 1's record lands with the note's creation; later rounds,
  the scan fold-back, and stage 3 append (`edit_note`). An interrupted phase therefore leaves a
  resumable open note. This mirrors plan's "record findings into the program note as you go"
  discipline (`SKILL.md:47`).
- **Slug, lookup, and resume contract** (the slug is load-bearing on three ends — resume, the
  handoff command, and plan's consumption — so it is specified once, here):
  - **Mint:** `write_note` takes no filename parameter; Basic Memory derives the file from the
    title's kebab form ("a note's file is named for its own permalink tail",
    `_conventions.md:14-19`). The slug is therefore DEFINED as the kebab tail of the title remainder
    after `Idea — ` (title `Idea — Signal panel v2` ⇒ slug `signal-panel-v2` ⇒ file
    `idea-signal-panel-v2.md`). Choose the slug FIRST, title the note to produce it, and after
    `write_note` read the generated permalink back and verify BOTH the filename and the permalink
    against the chosen slug — a mismatch means the title is wrong; fix the title, not the references.
    KNOWN RESIDUAL: how basic-memory kebabizes the em dash in `Idea — <slug>` is unverified
    (`_conventions.md:14-19` demonstrates space→hyphen only); expect the FIRST live write to
    possibly need one title correction. The verify-filename-AND-permalink loop bounds the risk to
    that one correction, and the shakedown checklist (item 19) exercises it. The em dash in the
    title itself is deliberate house style (program-note precedent `Mission Control — <slug>
    program`, `SKILL.md:23`).
  - **Lookup:** resume and plan-side consumption resolve a slug by `read_note` on the permalink
    (e.g. `ideas/idea-<slug>`), falling back to a search scoped to the idea's title words plus
    `mission-control` tag when the direct read misses. Three never-guess rules bind the fallback
    search (the master-by-tag mis-bind class — a fuzzy match is a hypothesis, not a bind): (i) an
    argument that resolves to a note whose status is not `open` (already superseded/consumed) is
    said so plainly and treated as a raw objective, never silently re-consumed; (ii) a note whose
    status still reads `open` but whose LATEST dated observation is `[consumed one-shot]` (or
    `[consumed]`) is reported as consumed — offer to re-discuss it as a NEW idea (the §3.1
    search-before-write step then handles the succession), never resume it as if unfinished;
    (iii) more than one plausible match → list the candidates and ask — one question, mirroring
    §1's disambiguation pattern — never bind by best score.
  - **Resume:** `discuss <slug>` reads the note and resumes at the NEXT stage after the last
    completed one — the last dated, writer-tagged section names what already happened; never re-ask
    answered rounds; never re-run a scan whose recorded base SHAs still match current
    `origin/<default>` unless the operator asks. Vault unreachable at resume → there is nothing to
    read: say so plainly and stop — never reconstruct the note from session memory (the
    creation-time stop's mirror).
- **Writer provenance.** Every append carries a writer tag — `controller` or
  `controller-fork <session-id>` — per the §1 state governance (`SKILL.md:28`). Discuss may run in a
  fork (side chat); nothing it writes touches rows (there are none), so forks and master are
  equivalent writers here, but tags still apply.
- **Vault-unreachable fallback** (new entry in the §1 per-mode list, `SKILL.md:38`): `discuss` may
  interrogate and scan but **stops before the idea note** — an unrecorded refinement is lost exactly
  where it matters most (same wording logic as plan's "stops before design").
- **Wall: no surface, by design.** No `~/.mc-wall/wall.json` touch, no rows, no VOCAB extension.
  Adding a wall-visible "discussed" state would require `mc_wall/tower/notes.py` VOCAB +
  `wall/web/app.js` + `wall/web/selftest.mjs` + `wall/docs/design-signal-panel.md` to move together
  (research stream §10-B); rejected for v1 (open decision D3).
- **Machine safety.** Discuss never runs gates, test suites, builds, or installs — nothing heavier
  than read-only git. This is the operator's standing OOM rule (one heavy process at a time;
  `~/.zcode/AGENTS.md` MEMORY-SAFE PROCESS RULE, 2026-10-02) applied at design level: scouts are
  read-only code readers, and the ONE heavy thing discuss does (the subagent fan-out) is token work,
  not local compute.

---

## 3. Stage 1 — Idea intake and interrogation

### 3.1 Intake (before any question)

1. **Restate.** One sentence + numbered, testable success conditions (SC-1…SC-n). Vague acceptance
   ("works", "looks good") is a defect, not a scoping choice — validate-queue rejects it downstream
   and the spec-writer demands it upstream (research stream §10-B). The restatement is confirmed by
   the operator **before the scan runs** (a scan of the wrong codebase is wasted evidence) — as the
   OPENING ITEM of round 1's single AskUserQuestion batch (confirm-or-delta), not as a separate
   interaction, and **counting toward round 1's 4-question cap** (round 1 = 1 confirm + at most 3
   forks), so the phase's ladder total stays at ≤12 questions in ≤3 batches — plus, worst case, the
   one repo-ambiguity ask permitted outside the ladder (step 3 below): 13 questions in 4 batches,
   stated here so the ceiling is honest arithmetic, not a rounded claim.
2. **Search before minting.** Before creating any note, search `ideas/` for an existing OPEN idea
   note matching this idea (search-before-write, `_conventions.md:64`); a hit is offered for resume
   instead of a duplicate mint (failure mode F11).
3. **Resolve the target repos** from the idea text; if the idea names no repo unambiguously, ask
   once (the only question allowed outside the ladder). Record the repo list in the note.
4. **Ground the interrogation in priors.** Before round 1, read: the project facts notes for the
   named repos (`SKILL.md:35`), `references/lessons.md`, and vault user-notes about the operator
   (working style, known pains, standing preferences). A challenge grounded in a recorded lesson
   ("this exact shape OOM'd on 2026-09-13") beats a generic one.

### 3.2 The dimension ladder

Worked **in order**; every dimension must end the phase restated-and-confirmed, explicitly excluded
with a reason, or explicitly deferred as an open question with a default:

| # | Dimension | What must exist when it closes |
|---|---|---|
| 1 | Goal & success conditions | The one-sentence restatement + SC-1…n, operator-confirmed |
| 2 | Users & surfaces | Who touches it and through which surfaces (UI, CLI, API, wall, vault) |
| 3 | End-to-end flows | Every flow traced user-facing → back end, step by step; backend-only is never a silent default (silence ≠ exclusion) |
| 4 | Scope & non-goals | Every in-scope surface listed; every exclusion carries a reason |
| 5 | Data shape | Entities, fields, persistence, migration surface if any |
| 6 | Edges & failure modes | What breaks, what it costs, abort/kill-switch thinking (lesson 7's discipline applied at idea level) |
| 7 | Integration points | Repos, boxes, services, external systems, in-flight work it must not collide with |
| 8 | Assumptions & constraints | Resource ceilings (machine doctrine), standing operator rulings (vault-native state, no CI-resident automation, human-only merges), anything adopted as a default |

### 3.3 Questioning mechanics (LLM-executable, checkable)

- **Rounds:** at most **2 interrogation rounds** in stage 1. Each round is one batched
  `AskUserQuestion` block of **at most 4 questions** (round 1's block opens with the restatement
  confirm, counting toward the 4), each with the recommended option FIRST and a
  one-line trade-off per option. Zero questions is a valid round — do not invent questions to look
  thorough (architect Clarify discipline, research stream §10-B).
- **Genuine forks only:** a question qualifies only if it is (a) genuinely undecidable from
  evidence, code, facts notes, or recorded rulings AND (b) would change the idea's shape, scope, or
  success conditions. Everything else adopts a documented default recorded as an assumption.
- **Challenge quota:** every round carries **≥3 named challenges** — strongest objection first —
  each stating the assumption attacked, the evidence motivating the attack (a path, a recorded
  lesson, a prior operator statement), and what happens if the assumption is wrong. Challenges are
  statements, not questions; they do not consume the question budget.
- **Contradiction hunt:** each round cross-checks the operator's statements pairwise (round N vs
  round N-1, restatement vs originals, idea vs recorded standing rulings) and names every
  contradiction found, quoting both sides paraphrased (never verbatim session text — sanitization
  rule, §8).
- **Persona rulings:** operator-facing forks (daily mechanics, UI, flows) may be pre-answered with
  an ADVISORY persona ruling offered as the marked recommendation — the operator confirms or
  vetoes, per `references/persona-gate.md:3` (wiring this third call site is a checklist item;
  the persona is banned from evidence claims).
- **Round record:** after each round, append to the idea note: challenges raised (and which
  survived), answers/decisions, assumptions adopted, contradictions found and their resolutions.

### 3.4 Saturation (when to stop)

Stage 1 ends when ANY of: (a) **saturation** — one full ladder pass yields zero surviving
challenges AND zero new genuine forks (say so explicitly); (b) the round budget (2) is exhausted —
remaining dimensions adopt defaults and are recorded as assumptions, never silently dropped; (c) the
operator says go. Never pad: a second round that would only re-ask answered dimensions is skipped
and the reason recorded.

### 3.5 Tuned to the operator (from the recorded evidence)

| His observed pattern | Design response |
|---|---|
| One-line kickoffs with typos; bulk-ratifies recommendations ("i agree with all of your recommendations for the 3 decisions") | Recommendations marked first on every fork; ratify-in-batch via AskUserQuestion; the raw idea is accepted verbatim, never sent back for cleanup |
| Locks in 2-3 exchanges; one batched 4-question round reshaped a major design | Hard round/question caps; the restatement confirmation rides inside round 1's batch (§3.1); stage 3 is exactly one more round — total operator exposure ≤3 batches and ≤12 ladder questions (worst case 13 in 4 batches, counting the one permitted repo-ambiguity ask) |
| Picks AGAINST a marked recommendation when it conflicts with intent | Recommendations are defaults, never defaults-in-disguise; an override is recorded as the decision, per persona-gate §4 operator-veto rule |
| Terse two-word instructions; context lives in the harness | AskUserQuestion options carry the context inline (evidence pre-filled), never bare labels |
| No em dashes / no emoji in operator-facing text | Every operator-facing rendering (questions, conflict cards, handoff block) obeys the standing forwardability rule |
| Wants framing re-examined, not hardened, when something feels wrong | Pushback triggers a root-cause reframe + full option space, not a tougher variant of the same design |

---

## 4. Stage 2 — Codebase reality scan

### 4.1 Topology

**2-5 read-only scouts, dispatched in one batch** via the Agent tool from the controller session.
Scouts are subagents doing Read/Glob/Grep work — token cost, not local compute. Lens collapse rule:
fewer than 3 target repos or a visibly small surface → collapse to 2-3 lenses; **never fewer than 2**
(fit and collisions always run). Five is the ceiling — the operator's parallelism doctrine
("parallelism is a proposal", `SKILL.md:121`) applies to scouts too, and the fan-out is fixed by
this section, not chosen ad hoc per run.

**Machine-safety reconciliation (so a literal-minded session neither serializes nor refuses the
batch):** the standing MEMORY-SAFE rule is "never run two heavy test/gate processes concurrently"
(`~/.zcode/AGENTS.md`, 2026-10-02) — read-only scouts spawn no heavy local process at all, so the
one-batch fan-out neither violates the rule nor needs serializing under it; the rule's subject is
gate_runner/pytest-class processes, which discuss never starts (§4.4). The operator's own recorded
appetite for read-side parallelism is high — his D0 kickoff asked for max subagents for read-only
corpus scanning, while the OOM reversal bounded heavy EXECUTION work (research stream §10-B). This
reading is design-level interpretation, not an operator ruling: the wiring checklist (item 20)
confirms it with the operator at shakedown and records the confirmation — if the operator rules
otherwise, the scouts serialize and nothing else in the design changes.

| Scout | Lens | Reads for |
|---|---|---|
| S1 | Architecture fit & reusable patterns | Where the idea would land: entry points, existing modules solving adjacent problems, naming/layout conventions, prior art to reuse vs duplicate |
| S2 | Collisions & in-flight state | Open branches, open MRs/PRs, active regenloop goals (continuation-misroute risk, `SKILL.md:47`), owned-file overlap with anything in flight, half-finished adjacent work |
| S3 | Data-model impact | Entities/schemas the idea touches or needs, migration surface, backfill needs, format/version coupling |
| S4 | Test & verification impact | Where tests live per touched area, gate/test commands and count expectations, fixture conventions, what a verify pass would need |
| S5 | Integration & deploy surface | Boxes/services, deploy paths, knobs and flags (read from deployed code, not memory), docs debt, the end-to-end path from user-facing entry to backend |

### 4.2 Per-scout output contract (fixed)

Every scout returns exactly this, nothing else:

1. **Identity & scope** — lens name, repos/paths covered, base SHA per repo (`git rev-parse HEAD`
   after `git fetch` is NOT required at discuss time — the scan is read-only; record
   `git rev-parse origin/<default>` where a remote exists, else local HEAD, and say which).
2. **Findings** — atomic items: `claim` + `evidence` (path:line, ≤2 quoted lines) + `confidence`
   (`read` | `inferred` | `unknown`). **A finding without a path-anchored evidence line is discarded
   at fold-back, unread.**
3. **Top-3 risks** for the idea given this lens.
4. **"What the idea gets wrong about this codebase"** — the explicit mismatch list (may be empty;
   an empty list must be stated, not omitted).
5. **Gaps** — what could not be verified and why (no access, would need a run, would need the
   operator).

### 4.3 Fold-back

The controller: (1) dedups findings by evidence anchor (same-file findings merge, keeping the
strongest claim); (2) severity-sorts into `blocker | conflict | friction | reusable`; (3)
**spot-checks ≥3 findings by re-reading the cited anchors itself** — trust-but-verify is the house
doctrine for every claim, scouts included (research stream §10-B: "junior executes, I verify
read-only"); (4) pastes the digest into chat as **evidence lines, not summaries** (lesson 12,
`references/lessons.md:27`); (5) appends the findings to the idea note's Reality-check findings
section, tagged `[scan]`.

### 4.4 Machine safety (hard rules)

Scouts: read-only — Read/Glob/Grep plus read-only git (`log`, `status`, `ls-remote`, `show`, and
`rev-parse` — the per-scout contract's base-SHA line runs `git rev-parse origin/<default>`, so the
command is part of the sanctioned surface, not an improvisation) and, for the S2 collisions lens
ONLY, read-only forge-API list queries (`glab mr list` / `gh pr list` and their `view` subcommands
— listing and reading, never creating, commenting, approving, or merging). Plus `ls` and file
reads. **NEVER** run tests, gate_runner, builds, installs, pip/uv, npm, docker, or any
state-changing command; never edit any file; never contact a production box beyond reading
already-recorded facts-note entries (a live SSH probe is plan-recon territory, `SKILL.md:47` — the
recorded B4-P1 failure was a plan that SKIPPED the box check, but the fix belongs to plan's recon,
not to discuss's scouts). One heavy local process at a time is the operator's standing OOM rule
(`~/.zcode/AGENTS.md`, MEMORY-SAFE PROCESS RULE, 2026-10-02); discuss runs zero heavy local
processes.

### 4.5 Operator skip of the scan

The operator may say "skip the scan, just write the note". Then: the Handoff section records
`scan-skipped-by-operator (<date>)`; pre-flight items (c) and (e) degrade accordingly — (c) becomes
"no findings: scan skipped by operator", (e) still requires an end-to-end flow but its steps carry
no anchors unless the operator supplied them; the note carries `confidence: unverified` frontmatter
stamp (`_conventions.md:80-83`) because its claims have had no reality check. The skip is a
recorded deferral, never a silent omission — and plan's recon inherits the full weight of the
checks the scan would have seeded.

---

## 5. Stage 3 — Post-reality-check discussion

**Exactly one round, max 4 genuine forks** (same fork discipline as stage 1 and as plan's §2 step 5).
When §4.5's operator skip is recorded, stage 3 is vacuous by construction — zero cards, no round is
held, and the phase proceeds straight to stage 4 with the pre-flight degradations.

### 5.1 Conflict card format (fixed)

Every conflict between the idea and the scanned reality is presented as a numbered card:

```
N. <dimension>
   Assumed:  <what the idea assumed>
   Reality:  <what the code says> (evidence: <path:line>, confidence: read|inferred)
   Options:  (a) <recommended first, one-line trade-off>
             (b) <alternative, one-line trade-off>
   Cost:     <what each option costs: scope, risk, or work>
```

The card is an operator-facing rendering: it obeys the standing forwardability rule (no em dashes,
no emoji — rewrite the sentence, never just delete the dash), per §3.5.

Non-conflict findings are summarized in one block (reusable patterns, frictions) — they inform, they
don't fork. Every card must end the stage as either a **recorded decision** (into the idea note's
decision log, with the rejected alternative) or a **recorded open question with a default** — never
silently resolved, never dropped (P8: close on a recorded decision or a recorded deferral).

### 5.2 Updating the idea

Same turn as the round closes: (1) the refined statement is revised in place if reality forced a
shape change (the `Idea:` line is rewritten — it must always be the current one-sentence truth);
(2) each resolved contradiction gets a decision-log entry naming both sides; (3) findings that
survive unchallenged are stamped `[confirmed]`; (4) anything the operator deferred becomes an open
question with its default. The note's Handoff section is refreshed last (scan date, base SHAs).

---

## 6. Stage 4 — The idea md

### 6.1 Placement and identity (vault conventions)

- **Directory:** `ideas/` in the vault `shared` project — i.e. `~/work/memory-vault/shared/ideas/`.
  Rationale: program notes live in `shared/programs/` in observed practice (the live wall.json
  note_globs all point there, research stream §10-B), so the idea channel sits beside its consumer;
  `programs/` itself is lifecycle-wrong (created at plan, deleted at close, wall-discovery-coupled —
  `wall/mc_wall/tower/discovery.py` scans programs/ with a program filename regex; an ideas/ dir is
  invisible to the wall by construction), and `decisions/` is the load-bearing cross-project channel
  that pre-decision proposals must not pollute. New directory ⇒ vault-side edits (hub folder map +
  conventions taxonomy line) — checklist item; the rule-of-three tension is open decision D2.
- **Filename:** `idea-<slug>.md`, kebab (kebab_filenames is true, `~/work/memory-vault/shared/_conventions.md:14-19`). The filename is TITLE-DERIVED — `write_note` offers no filename parameter; Basic Memory derives the file from the title's kebab form — so the slug is defined as the kebab tail of the title remainder after `Idea — `, and the post-write check verifies filename AND permalink against the chosen slug (full contract in §2; an agent that picks one slug and titles the note differently breaks every later `<slug>` reference).
- **Frontmatter:** `title: Idea — <component noun> <future query>` (title = the future query,
  `_conventions.md:35`), `type: note` (tool-written precedent: program notes are type note,
  `SKILL.md:23`), `status: open`, `tags: [mission-control]` (both in the closed vocabularies,
  `_conventions.md:95-102,113-124`; a new `idea` tag would need a conventions amendment — reuse
  `mission-control`), plus OPTIONAL `created: YYYY-MM-DD` (Basic Memory stamps its own timestamps;
  the field is a tasks-note-precedent convenience, not a tool requirement).
- **Written via** `write_note` then `edit_note` appends (the same write/edit split plan uses,
  `SKILL.md:26,51`). The permalink is read back after write and never hand-guessed
  (`_conventions.md:135-137`).

### 6.2 Body grammar (the parser-adjacent rules are literal, not lenient)

```markdown
# Idea — <component> <future query>

Idea: <ONE sentence — literal unbolded prefix; plan copies this verbatim into the
program note's `Objective:` line, which the wall's parser matches literally
(SKILL.md:34 — same discipline, applied one artifact earlier)>

## Problem
<the why, one paragraph> Success conditions: SC-1 … SC-n <numbered, testable>

## User stories
<as-a / wants / so-that + acceptance per story>

## End-to-end workflows
<per flow: numbered steps from user-facing entry to back end; steps the scan verified
carry their path anchors>

## Scope and non-goals
In scope: <surfaces>
Out of scope: <surface> — <reason>   (every exclusion carries a reason; silence ≠ exclusion)

## Decisions
| id | date | decision | rationale | alternative rejected | source |
<one row per resolved fork — intake rounds, scan conflicts, operator overrides>

## Reality-check findings
- [finding|risk|collision|reusable] <subject> — <value> — <provenance: path:line or scout id> —
  <YYYY-MM-DD> — confidence: read|inferred|unknown
<atomic, no pronouns, no code blocks, ≤25 findings — detail lives in the anchors>

## Open questions
<id, question, why it matters, DEFAULT if unanswered — every question ships a default>

## Risks
<riskiest assumption FIRST with its check; then the rest, each with a check or accepted-by>

## Handoff to plan
Target repos: <list with base SHAs at scan time>; scan date: <YYYY-MM-DD>;
plan must import: Idea: line (verbatim objective), Decisions (program-note assumptions),
findings (recon seeds — re-verify, never trust stale), open questions (defaults become
assumptions unless the operator rules).

## Relations
<edges per vault conventions — dash + relation type + target; never bracket syntax as an example>
```

Grammar notes: the `Idea:` prefix is unbolded and literal (mirrors `Objective:`, `SKILL.md:34`);
findings obey the atomic-observation rules (`_conventions.md:54-62`); no secrets ever, no session
ids, no hostnames (`_conventions.md:184-185` + §8 below); the note is NOT skill-owned working
state — it survives close and is superseded, never deleted, when plan consumes it
(`_conventions.md:139-142` exempts only program notes from supersede-don't-delete).

### 6.3 Pre-flight gate (fail-loud, before the handoff)

All six or the gap is named and fixed in turn: (a) `Idea:` line is exactly one unbolded sentence;
(b) every non-goal carries a reason; (c) every finding carries evidence + date + confidence; (d)
every open question carries a default; (e) ≥1 end-to-end workflow spans user-facing → back end;
(f) every decision names its rejected alternative. Items (c) and (e) degrade as §4.5 specifies when
the operator skipped the scan — degraded and recorded, never silently passed. Then print the
handoff block: idea slug, note permalink (read back), and the exact next command —
`/mission-control-plan <slug>`.

### 6.4 Lifecycle

| Event | Mutation (same turn) |
|---|---|
| Created | after interrogation round 1 — `write_note`, status `open` |
| Updated | after each round, after scan fold-back, after stage 3 — `edit_note` appends, writer-tagged |
| Consumed by plan | **Branches on triage — both the imports and the supersede.** Program: `Idea:` line → `Objective:` verbatim, decisions/defaults → program-note assumptions, findings → recon seeds (re-verified); status → `superseded` + `superseded_by: memory://shared/programs/mission-control-<slug>-program` + dated `[consumed]` observation naming the controller session id + `relates_to` edge to the program note (a plain-text `memory://` pointer instead of an edge when plan maps the program outside `shared` — cross-project wikilinks do not resolve, `_conventions.md:161-162`). One-shot (no program note exists): `Idea:` line → the light prompt's goal statement, decisions/defaults/findings fold into the emitted prompt per the anatomy's One-shot row — no program note, no recon step; a dated `[consumed one-shot]` observation naming the emitted light prompt + controller session id; `superseded_by` only if a bm-decide decision note is written for it, else the note stays `open` with the observation |
| Parked/rejected by operator | stays `open` with a dated `[parked <reason>]` observation (findable, resumable) — or `superseded_by` a bm-decide decision note if one is written |
| Never | deleted by mission-control; archived to `_archive/` only by the monthly vault pass |

---

## 7. Handoff to plan — exact edits

Plan keeps working exactly as today when given a raw objective. When the argument resolves to an
open idea note, plan additionally consumes it. All quotes are current text, read this session.

**Edit 1 — `commands/mission-control-plan.md:8` (and the §2→§3 pin on line 10).**

Current:
```markdown
Operator input: $ARGUMENTS
```
and line 10 begins:
```markdown
Follow §2 (plan) exactly: triage (one-shot vs program), the non-negotiable recon (true bases, …
```

Proposed:
```markdown
Operator input: $ARGUMENTS (the objective in your own words, or an idea slug — an open idea
note is read and consumed per §3 step 1)
```
```markdown
Follow §3 (plan) exactly: idea intake when the argument names an open idea note (read and consumed
per §3 step 1 — the note is consumed in the same turn, superseded when triage routes to a program),
triage (one-shot vs program), the non-negotiable recon (true bases, …
(rest of line 10 unchanged.)

**Edit 2 — SKILL plan section (renumbered §3), new step 1, before triage.**

```markdown
1. **Idea intake (when the objective resolves to an open idea note):** resolve the argument by
   read_note on the permalink `ideas/idea-<slug>` (the permalink the discuss handoff block printed),
   falling back to a title-word + `mission-control`-tag search. Never guess the fallback: a note
   whose status is not `open` is said so plainly and treated as a raw objective; a note still
   `open` whose latest dated observation is `[consumed one-shot]`/`[consumed]` is reported as
   consumed and treated the same way; a fuzzy search yielding more than one plausible match → list
   the candidates and ask. If the slug matches BOTH an open idea note and an active program:
   one disambiguation question (consume the idea vs continue the program), mirroring §1's
   program-resolution rule. The note's imports and its supersede both branch on triage (next step),
   because a one-shot creates no program note and no recon step. Triage → program: the note's
   `Idea:` line becomes the program note's `Objective:` verbatim; its Decisions and assumption
   defaults import into the program note's decisions/assumptions; its reality-check findings seed
   recon (each is RE-VERIFIED — a finding older than the scan that produced it is a lead, not a
   fact); its open questions become recorded assumptions unless the operator rules otherwise; then
   supersede to the program-note permalink (read back after write) — status `superseded`,
   `superseded_by:` that permalink, a dated `[consumed]` observation naming the controller session
   id, and a `relates_to` edge to it (a plain-text `memory://` pointer instead of the edge if the
   program maps outside the `shared` project — cross-project wikilinks do not resolve). Triage →
   one-shot: the `Idea:` line becomes the light prompt's goal statement; decisions, defaults, and
   findings fold into the emitted prompt per the One-shot row in `references/prompt-anatomy.md`'s
   module table — no program note, no recon step is created — and a dated `[consumed one-shot]`
   observation naming the emitted light prompt and the controller session id is recorded;
   `superseded_by` only if a bm-decide decision note is written for it, else the note stays `open`
   with the observation. Mission-control never deletes an idea note.
```
(Current §2 steps 1-6 renumber 2-7 — so the question/persona step cited by persona-gate.md becomes
**step 6**, see Edit 5; wording otherwise untouched. Case 06's §1/§2 extractor pins move to §1/§3 —
checklist.)

**Edit 3 — SKILL §1 vault-unreachable fallback list (`SKILL.md:38`).**

Current (excerpt):
```markdown
… `plan` may gather recon but stops before design (no note, no program); `prompts` and `verify` abort — …
```
Proposed (one clause added at the head of the list):
```markdown
… `discuss` may interrogate and scan but stops before the idea note (no note, no handoff);
`plan` may gather recon but stops before design (no note, no program); …
```

**Edit 4 — SKILL frontmatter (`SKILL.md:4-5`).** description's command list gains
`/mission-control-discuss` first; argument-hint becomes
`<discuss|plan|prompts|verify|next|close> [idea | objective | program-slug | session/MR reference]`
(token set must equal case 04's MODES, `tests/cases/04-command-surface.sh:27-32`).

**Edit 5 — `references/persona-gate.md:3` (plus the other shipped §-citations).** Three changes in
that line: "Wired into two places" becomes three (discuss pre-answers operator-facing forks with
ADVISORY rulings); `(SKILL §2 step 5)` becomes `(SKILL §3 step 6)` — plan renumbers to §3 AND
Edit 2's new step 1 shifts the question/persona step from 5 to 6 (a plain §2→§3 rewrite would point
at "Design the waves": a wrong citation shipped by the checklist); and `§4 is evidence-only` becomes
`§5 is evidence-only` (verify renumbers). The same renumber touches four more shipped citations,
none of which any test pins — verified by `grep -n 'SKILL §' plugins/mission-control/skills/mission-control/references/*.md`
this session: `references/prompt-anatomy.md:47` and `:84` ("SKILL §3 points here" — prompts → §4),
`references/regenloop-interface.md:170` ("procedure is SKILL §4" — verify → §5), and
`references/verify-runbook.md:1` and `:3` ("SKILL §4" — verify → §5). CAUTION:
`references/regenloop-interface.md` also carries regenloop-INTERNAL § references (bare `§N` tokens —
e.g. lines 5, 8, 22, 28, 41, 60, 96, 111, 155, 167, 185, 201, 213-214, 225, 232, grep-verified this
session) that must NOT be rewritten; the `SKILL §` prefix is the discriminator — only tokens with it
are MC-SKILL citations. The checklist's repo-wide grep step enforces exactly this scope.

**Edit 6 — `README.md:7-12,14-24`.** Pipeline gains `DISCUSS →` at the head; the `README.md:14`
heading "## The five modes" → "## The six modes" (the table directly under it gains
`/mission-control-discuss <idea>` as its first row — "interrogates the idea against you and
the codebase, then writes the idea note plan consumes"); and `README.md:24` "All five also work
through the base skill" → "All six also work through the base skill".

---

## 8. Failure modes and guardrails

| # | Failure mode | Guardrail (checkable) |
|---|---|---|
| F1 | **Yes-man drift** — rounds that validate instead of attack | ≥3 named challenges per round, strongest first (§3.3); every decision names its rejected alternative (§6.2 Decisions column is mandatory); zero-challenge rounds must say so explicitly; contradiction hunt is a per-round obligation; persona output is ADVISORY-only and never counts as evidence (`references/persona-gate.md:35-39`) |
| F2 | **Shallow scan** — scouts return vibes | Finding-without-anchor is discarded unread (§4.2); controller re-reads ≥3 cited anchors itself (§4.3); digest pasted as evidence lines, not summaries (lesson 12); scout gaps section is mandatory, an empty one must be stated |
| F3 | **Idea-md/plan divergence** — plan re-derives a different objective | `Idea:` line copies verbatim into `Objective:` (Edit 2); decisions import as program-note assumptions in the same turn; idea note superseded in the same turn — exactly one live artifact, the same single-authority discipline as prompt-log rows (`SKILL.md:26`) |
| F4 | **Stale ideas** — an old scan dressed as current fact | Handoff section stamps scan date + base SHAs (§6.2); plan RE-VERIFIES every finding during its own recon (Edit 2 — the rule, not a hope); an idea note consumed months later gets its drift named by plan's recon, not silently trusted |
| F5 | **Sanitization leaks** — session ids, hostnames, host paths, secrets in the note | Evidence anchors are repo-relative or `~/` paths only (§6.2); operator phrasing is paraphrased into decisions, never verbatim-transcripted; secrets never in the vault, pointers only (`_conventions.md:184-185`); `[consumed]` observations name a session id (provenance, §1 discipline `SKILL.md:28`) but findings never do |
| F6 | **Interruption loses the phase** | Note created after round 1 and appended per stage (§2 state handling); resume via `discuss <slug>`; every append writer-tagged and dated |
| F7 | **Scope creep into plan's job** — discuss starts designing waves/lanes/caps | §1 "what discuss is not"; the section text forges nothing, registers nothing, caps nothing; those verbs appear only in plan/prompts sections |
| F8 | **Machine damage** — scouts run gates/tests | §4.4 hard rules; scouts' Bash surface is enumerated read-only; the OOM doctrine is cited in-section so the constraint survives copies |
| F9 | **Question flooding** — the phase becomes an interrogation wall | Hard caps: stage 1 ≤2 rounds × ≤4 questions, stage 3 = 1 round × ≤4; genuine-fork test; zero-question rounds legal; total phase exposure ≤12 questions (§3.5) |
| F10 | **Orphaned ideas** — notes that never reach plan, clogging the vault | status stays `open` with `[parked <reason>]` when the operator walks away (findable/resumable); monthly vault pass archives long-superseded ones (per vault conventions, outside mission-control's remit) |
| F11 | **Duplicate idea notes** — the same idea minted twice because the operator re-describes it without the slug | §3.1 step 2 searches `ideas/` for an existing OPEN match before minting (search-before-write, `_conventions.md:64`) and offers resume instead; slug/post-write verification (§2) keeps `<slug>` references resolvable |

### 8.1 Known residuals (accepted, with their bounds)

- **Em-dash kebabization** of the `Idea — <slug>` title is unverified against basic-memory's
  slugger; bounded to one title correction by the §2 verify loop; exercised at shakedown (item 19).
- **`--project shared` routing** (whether a `project` parameter is discarded under the constrained
  server) is a research-stream claim, marked unverified at checklist item 15. The consumption
  mechanics are coherent either way — `edit_note` on the shared server still writes the supersede;
  only the `relates_to` edge degrades to a plain-text `memory://` pointer
  (`_conventions.md:161-162`). The FIRST cross-project program should re-verify the routing live.
- **The scout-batch MEMORY-SAFE reading is provisional** until checklist item 20 records the
  operator's shakedown confirmation; the fallback (serialize the scouts, nothing else changes) is
  pre-computed in §4.1.
- **Wall suites were not run in any round of this design's authorship or review** (checklist item
  18's contingency stands: nothing under `plugins/mission-control/wall/` is touched). The
  wall-relevant claims were instead verified by direct reads: the round-4 independent reviewer
  reported verifying `wall/mc_wall/tower/discovery.py:11-12` (FILENAME_RE matches only
  `mission-control-<slug>-program.md`; `idea-*.md` is a silent non-candidate), `notes.py:41`
  (VOCAB, the exact 7-token set, untouched by this design), and the live `~/.mc-wall/wall.json`
  (all three declared note_globs point at `shared/programs/`, confirming D4's observed-practice
  basis) — reported by the reviewer, not re-run by this design's author.
- **Research-stream §10-B citations stand on the ask's research material**: the transcript
  archaeology and regenloop plugin-side line numbers (architect/critic SKILL.md cites) were not
  re-derived in any session of this design; repo, vault, and wall sides were read directly.
- **Docs placement**: these two deliverables ship in-repo at `plugins/mission-control/docs/`,
  riding the `wall/docs/` precedent (four design docs ship in-repo, versioned through the
  packaging gate) rather than the operator's vault-native default for working docs (lesson 14
  targets working state, and these are frozen design contracts). If the operator disagrees, the
  two files move to the vault and only the packaging advisory expectation changes — open decision
  D8.

---

## 9. Open decisions (with recommended defaults)

| # | Decision | Recommendation + one-line rationale |
|---|---|---|
| D1 | SKILL section placement: insert as new §2 with plan→§3…Always→§9 renumber, vs append as §9 | **Insert as §2 + renumber.** Section order = lifecycle order is the load-bearing invariant; the pin updates (5 command files, SKILL internal §refs, case 06's extractor, persona-gate.md:3) are mechanical and gate-covered. The draft file is written against this choice. |
| D2 | `ideas/` directory now vs park idea notes in `notes/` until the rule of three earns a folder (`_conventions.md:49-52`) | **Create `ideas/` now + amend the hub folder map and conventions taxonomy in the implementing session.** Plan consumes by directory; burying ideas in notes/ dissolves the consumption contract. The rule of three governs organic growth; programs/ precedent shows defined lifecycle channels get their folder. |
| D3 | Wall visibility for discussed ideas (a "discussed" chip or idea backlog card) | **None in v1.** Wall VOCAB/app.js/selftest/design-contract would all have to move together for a pre-forge state that §1 explicitly forbids as rows; revisit only if open ideas actually pile up unactioned. |
| D4 | Idea-note project routing: always `shared`, vs the project owning the target repos | **Always `shared/ideas/`.** Matches observed practice (program notes live in shared/programs), keeps the consumption contract one-directory, and avoids a new fork question at intake; plan already asks project mapping where it matters (`SKILL.md:40`). |
| D5 | Is discuss mandatory before plan? | **No — both doors stay open.** Plan keeps accepting raw objectives verbatim (zero breakage for existing muscle memory and one-shots); discuss is the front door for ideas that deserve interrogation. |
| D6 | Question budget shape (2×4 + 1×4 proposed) vs architect's 8-in-≤2-batches convention | **2×4 + 1×4 (max 12 ladder questions; 13 worst-case with the repo-ambiguity ask).** Matches his observed plan-phase cadence (one batched 4-question round, bulk-ratified) scaled to a conversation phase; architect's 8-cap governs regenloop lanes, not MC modes. |
| D8 | Where these two design docs live: in-repo `plugins/mission-control/docs/` (wall/docs precedent, packaging-gate-versioned) vs the vault (his vault-native default for docs) | **Keep in-repo.** `wall/docs/` already ships four frozen design contracts in-repo through the packaging gate; lesson 14's vault-native rule targets working state, and these are frozen contracts wired to repo surfaces. If the operator prefers the vault, the move is mechanical and only the packaging advisory expectation changes (§8.1 residual). |

---

## 10. Evidence appendix

**A. Read directly this session** (paths repo-relative to the clone root; vault paths `~`-relative):

| Claim | Source |
|---|---|
| Plan command is a thin entry point; `Operator input: $ARGUMENTS`; loads 3 references | `plugins/mission-control/commands/mission-control-plan.md:1-10` (whole file read) |
| Command-file format (description + argument-hint + entry-point body + §-pin) | `commands/mission-control-{close,next,prompts,verify}.md` (all four read in full) |
| plugin.json carries no commands/skills arrays; version 1.9.1 | `plugins/mission-control/.claude-plugin/plugin.json:1-22` |
| §1 state model: program note grammar, row authority, writer tags, vault-first fallback list, project mapping | `skills/mission-control/SKILL.md:19-40` |
| §2 plan steps 1-6 (triage/recon/caps/waves/questions/note+wall) | `SKILL.md:44-54` |
| "never emit pre-forge placeholder rows"; unbolded `Objective:` | `SKILL.md:34` |
| allowed-tools already covers Agent, AskUserQuestion, Read/Glob/Grep/Bash, all mcp__shared-memory__ tools | `SKILL.md:6` |
| Never/Always sections (no implementing, no launching, evidence lines) | `SKILL.md:114-130` |
| Case 04 MODES list + SKILL hint token-set compare | `tests/cases/04-command-surface.sh:8,27-32` |
| Case 03 tool-token auto layer (Read Write Edit Glob Grep Bash Agent WebFetch + mcp regex) | `tests/cases/03-allowed-tools-cover.sh:17-22` |
| Case 06 §1/§2 extractor + wall-registration pins | `tests/cases/06-wall-registration.sh:15-20` (grep of § pins this session) |
| Cases 01/02 pin anatomy + interface doc only — unaffected by a SKILL-section insert | `tests/cases/01-anatomy-invariants.sh`, `tests/cases/02-interface-citations.sh` (read in full) |
| Packaging gate: frontmatter rules, references-named-must-exist scans all of the plugin dir (docs/ included), version identity | `scripts/verify_packaging.sh:24-56` |
| README pipeline line, five-modes table, maintenance/versioning | `README.md:7-24,72-76` |
| CHANGELOG [Unreleased] head convention + gates-line convention | `CHANGELOG.md:8-19` |
| Lessons catalogue (stale-base, capsule overclaim, silent skips, evidence lines, OOM proposal, genuine forks) | `references/lessons.md:5-35` (read in full) |
| Persona gate: advisory-only, wired at plan step 5 + red-team check 11, banned at verify; operator-veto rule | `references/persona-gate.md:3,35-39` |
| Five shipped `SKILL §`-prefixed citations outside SKILL.md (persona-gate.md:3, prompt-anatomy.md:47,84, regenloop-interface.md:170, verify-runbook.md:1,3) — exactly what the `grep 'SKILL §'` sweep finds; ONE additional bare MC-SKILL ref ("§4 is evidence-only" in persona-gate.md:3) caught only by reading, not by the grep; regenloop-internal bare-§N tokens must not be rewritten | `grep -n 'SKILL §' plugins/mission-control/skills/mission-control/references/*.md` + `grep -n '§' .../regenloop-interface.md`, both run this session (revision round 2); the checklist item 8 enumeration is the operative list |
| `write_note` takes no filename parameter (title, content, directory, tags, note_type, metadata, overwrite, project… only) — filenames are title-derived | the shared-memory MCP tool schema as exposed in this session |
| Confidence stamp `unverified` exists as a frontmatter convention for unverified claims | `~/work/memory-vault/shared/_conventions.md:80-83` |
| references/ contains exactly the five files the design names | `ls plugins/mission-control/skills/mission-control/references/` this session |
| No `plugins/mission-control/docs/` existed; only `wall/docs` | `find plugins/mission-control -type d -name docs` this session |
| Vault conventions: kebab, closed root, rule of three, note shape/size cap, status/type/tag closed sets, relations gotchas, supersede-don't-delete, skill-owned working state, secrets/English, archive tier | `~/work/memory-vault/shared/_conventions.md:14-19,35-65,85-124,135-142,144-196` (read in full) |
| Design-doc precedent (frozen contracts, tables, severity maps, selftest-as-contract) | `wall/docs/design-signal-panel.md` (read in full) |

**B. Research-stream citations (from the six reports in the ask; files NOT re-read this session —
reported as research findings):**

| Claim | Reported source |
|---|---|
| Shahil's kickoff/decision cadence, bulk ratification, override-against-recommendation, OOM reversal, box-recon failure (B4-P1), platform-pin fail-closed, plan-carried formula rejections | session-archaeology + usage-patterns streams: `evaluation/00-evidence/transcript-main.md:76,524,5708-5709,5560`, `evaluation/02-fleet-performance/B4-plan-next-state.md` §1.1-1.4, vault checkpoint notes under `~/work/memory-vault/shared/sessions/` |
| Critic doctrine ("agrees = failed review"), Clarify gate mechanics, spec-writer silence≠exclusion, RED-first, decision-note grammar, P1-P8 grilling principles | collaboration-style stream: regenloop 1.3.1 `architect/SKILL.md:59-81,402-408`, `agents/critic.md:8`, `agents/spec-writer.md:12-14`, `skills/tdd/SKILL.md`, `~/.zcode/skills/bm-decide/SKILL.md:30-43` |
| Wall VOCAB closed set, discovery grammar (programs/ only, filename regex, degraded lines), boot-only config | phase-landscape stream: `wall/mc_wall/tower/notes.py:39-41`, `wall/mc_wall/tower/discovery.py:11-36`, `wall/README.md:156-160` |
| Idea-note placement analysis (ideas/ vs programs/ vs decisions/, `Idea:` literal-prefix precedent, supersede-at-consume lifecycle) | vault-conventions stream: `_conventions.md` + `notes.py:182-188` + bm-remember/bm-decide skills |
| Ideas/ideas-contract absent today (no `idea` concept anywhere in the plugin) | vault-conventions stream grep, reported zero hits |
| No em dashes / no emoji forwardability rule; framing-reframe pushback; junior-executes-I-verify doctrine | usage-patterns stream: `~/.zcode/cli/memories/projects/.../forwardable-messages-no-em-dashes.md`, `user-brainstorm-pushback-reframe.md`, `~/work/memory-vault/shared/upgrades-by-shahil.md:87` |
| MEMORY-SAFE single-heavy-process rule | `~/.zcode/AGENTS.md` (system context of this session) |

**Checks run this session (after authoring the two docs, confirming they break nothing; smoke +
packaging + the scans re-run after the revision rounds 2, 3, AND 4 edits with identical results):**

- `bash tests/run_smoke.sh` → exit 0, all six cases PASS (case 03's WARNs are its pre-existing
  advisory inverse — granted-but-unused allowed-tools entries — unrelated to these docs).
- `bash scripts/verify_packaging.sh` → "OK: packaging blocking checks passed (version 1.9.1)". The
  two advisory WARNs (marketplace synced copy and plugin cache differ from repo) are the gate's
  by-design release-time advisories; the new docs/ dir is now part of that expected diff.
- Packaging check 4 simulation (the `grep -rho 'references/…'` scan) over the whole plugin dir
  including docs/ → only the five shipped references named, all exist.
- Case-04 frontmatter simulation on the copy-ready command block → delimiters at line 4, one
  `description:`, non-empty `argument-hint`, `SKILL.md` referenced, `§2` pin present.
- Case-03 token simulation on the fenced SKILL-section text → zero standalone `Write`/`Edit`, no
  partial mcp tokens; `Agent`, `Read`, `Glob`, `Grep`, `AskUserQuestion` all granted in
  allowed-tools (`SKILL.md:6`).

**Checks NOT run this session:** wall pytest and web selftest — the change touches nothing under
`plugins/mission-control/wall/`, and the draft's checklist item 18 names them for the (contingent)
wiring-time run. No SKILL.md, command, plugin.json, or vault file was modified — the only writes
this session are the two files this ask names.
