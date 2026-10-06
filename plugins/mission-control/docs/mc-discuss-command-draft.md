# mc-discuss — copy-ready command draft + registration checklist

Companion to `docs/design-mc-discuss.md` (the design of record). Everything below is written against
the design's recommended defaults (open decisions D1-D6 as recommended there): discuss is the new
SKILL **§2**, plan→§3 … Always→§9 renumber, idea notes live in `shared/ideas/`, no wall surface,
plan keeps accepting raw objectives.

Two packaging-gate constraints shaped this draft (verified against `scripts/verify_packaging.sh:34-39`
this session): every `references/*.md` string named anywhere under the plugin dir must exist — this
draft names only the five that ship; and SKILL body text may not use the capitalized standalone
tokens `Write`/`Edit` (case 03 auto layer, `tests/cases/03-allowed-tools-cover.sh:17`) — the section
text below avoids them.

---

## 1. The command file — copy verbatim to `plugins/mission-control/commands/mission-control-discuss.md`

```markdown
---
description: "Mission Control — discuss: interrogate the raw idea against you and the codebase, then write the idea note plan consumes"
argument-hint: <idea in your own words | idea-slug>
---

Execute the **mission-control** skill in **discuss** mode. First read `skills/mission-control/SKILL.md` — it is authoritative; this command is only an entry point.

Operator input: $ARGUMENTS (the raw idea — prose, typos welcome; or an existing idea slug to resume an open idea note)

Follow §2 (discuss) exactly: restate the idea with numbered testable success conditions and get the restatement confirmed (inside round 1's question batch), then the interrogation rounds over the dimension ladder (max 2 rounds, max 4 questions each, recommended option first, every round attacks at least 3 assumptions — strongest objection first — and hunts contradictions); the codebase reality scan (2-5 read-only scouts, lenses and per-scout contract per the section; never tests, gates, builds, or installs); the post-reality-check round (every conflict gets an evidence-backed card; at most 4 become questions/forks, the rest close as decisions-by-default or open questions with defaults); then the idea note written to the vault per its grammar and passed through the pre-flight gate. Load `references/lessons.md` (the failure catalogue the interrogation hunts for) and `references/persona-gate.md` (advisory rulings on operator-facing forks) before the first round.
```

---

## 2. The SKILL section — copy as the new `## §2` in `skills/mission-control/SKILL.md`

Renumber the existing §2-§8 to §3-§9 first (mapping in §4 below). Text below follows the house style
(dense numbered steps, tool names only as granted in allowed-tools — `SKILL.md:6` already covers
everything this section uses).

```markdown
## §2 `discuss <idea | idea-slug>` — interrogate the idea, then write the idea note

The lifecycle's first mode: before plan, before any program machinery. Input is the operator's raw idea (spoken prose, typos welcome) or an idea slug to resume an open idea note. Output is ONE idea note in the vault — refined statement, decisions, reality-check findings with evidence — that `plan` consumes as its objective (§3 step 1). No program note, no prompt-log rows, no wall registration exist at this stage: rows are born at forge, and discuss never mints wall state. Two duties, both mandatory every round: reinforce (steelman the idea — restate it concretely, find its strongest version) and grill (attack it — a round that merely agrees is a failed round).

1. **Intake.** Restate the idea in one sentence plus numbered, testable success conditions (SC-1…n) — vague acceptance ("works", "looks good") is a defect, not a scoping choice. Before minting anything, search `ideas/` for an existing OPEN idea note matching this idea (search-before-write) and offer resume instead of a duplicate. Resolve the target repos from the idea (ask once if ambiguous — the only question allowed outside the ladder); record them. Read the project facts notes, `references/lessons.md`, and vault user-notes about the operator BEFORE round 1 — a challenge grounded in a recorded lesson beats a generic one. The restatement is confirmed by the operator as the OPENING ITEM of round 1's single question batch (confirm-or-delta, counting toward the 4-question cap — round 1 is 1 confirm + at most 3 forks); never start the scan before that confirmation — a scan of the wrong codebase is wasted evidence.
2. **Interrogation rounds (max 2, max 4 questions each).** Work the dimension ladder in order: goal & success conditions → users & surfaces → end-to-end flows (user-facing to back end, every step) → scope & non-goals (every exclusion carries a reason; silence is not exclusion) → data shape → edges & failure modes → integration points → assumptions & constraints. Question discipline is plan's: only genuine forks (undecidable from evidence AND idea-changing), batched via AskUserQuestion, each with the recommended option first and one-line trade-offs; zero questions is a valid round. Every round ALSO carries at least three named challenges — strongest objection first, each stating the assumption attacked, the evidence motivating it, and the cost if it is wrong — and a contradiction hunt across the operator's own statements (this round vs earlier rounds vs recorded lessons and standing rulings). Operator-facing forks may be pre-answered with an ADVISORY persona ruling offered as the marked recommendation per `references/persona-gate.md`. Round record appended to the idea note: challenges raised and which survived, answers, decisions, assumptions adopted, contradictions and their resolutions (round 1's record lands with the note's creation in step 5; later rounds append). Saturation ends the stage early: one full ladder pass with zero surviving challenges and zero new genuine forks — said explicitly, never implied by silence.
3. **Reality scan.** Dispatch 2-5 read-only scouts (Agent) in one batch, lenses: architecture fit & reusable patterns; collisions & in-flight state (open branches/MRs, active regenloop goals, owned-file overlap); data-model impact; test & verification impact; integration & deploy surface. Collapse lenses for a small surface; never fewer than 2 (fit and collisions always run). Read-only scouts spawn no heavy local process, so the one-batch fan-out neither violates the machine-safety rule (never two heavy test/gate processes) nor needs serializing under it. Scouts read code only (Read, Glob, Grep, read-only git: log, status, ls-remote, show, rev-parse for the contract's base SHAs) and, for the collisions lens only, read-only forge-API list queries (glab mr list / gh pr list and view — listing and reading, never creating, commenting, approving, or merging); they NEVER run tests, gates, builds, or installs, never install anything, never edit any file, never probe a live box (box checks belong to plan recon). Per-scout contract, fixed: identity & scope with base SHAs; findings as claim + path-anchored evidence + confidence (read | inferred | unknown) — a finding without an evidence anchor is discarded at fold-back; top-3 risks; "what the idea gets wrong about this codebase" (an empty list is stated, not omitted); gaps. Fold-back: dedup by evidence anchor, severity-sort (blocker | conflict | friction | reusable), re-read at least three cited anchors yourself before trusting them, paste the digest as evidence lines (not summaries), append findings to the idea note tagged [scan]. The operator may skip the scan ("just write the note"): record scan-skipped-by-operator with the date in the Handoff section, stamp the note confidence: unverified, and let pre-flight items (c) and (e) degrade as recorded gaps — never silently pass.
4. **Post-reality-check round (exactly 1, max 4 forks).** Every idea-vs-code conflict is a numbered card: Assumed | Reality (with its evidence anchor) | at least two options, recommended first, one-line trade-offs | the cost of each — at most 4 cards become questions/forks; the rest close on the spot as decisions-by-default or open questions with defaults, never silently dropped. When the operator skipped the scan (step 3), this stage is vacuous: zero cards, no round held, proceed to step 5 with the degraded pre-flight. Each card closes as a recorded decision or a recorded open question with a default — never silently resolved. Same turn: revise the refined statement if reality forced a shape change, add decision-log entries naming both sides of each resolved contradiction, stamp surviving findings [confirmed], refresh the handoff section (scan date, base SHAs).
5. **The idea note.** Written incrementally: created (write_note) after round 1 so an interrupted phase resumes via `discuss <slug>`; appended (edit_note) after each round, the scan fold-back, and stage 3 — every append writer-tagged per §1. Lives in the vault `shared` project, directory `ideas`. Slug contract: write_note takes no filename — Basic Memory derives the file from the title's kebab form — so the slug IS the kebab tail of the title remainder after `Idea — `; choose the slug first, title the note to produce it, and after write verify BOTH the generated filename and the read-back permalink against the chosen slug (fix the title on mismatch, never the references). Resume: `discuss <slug>` reads the note (read_note on the permalink `ideas/idea-<slug>`, search fallback on a miss — never guessing: a non-open status is said so and treated as a raw idea; a note still open but whose latest dated observation is [consumed one-shot] or [consumed] is reported as consumed, offered as a NEW idea, never resumed; more than one plausible match → list and ask; vault unreachable → nothing to read, say so and stop, never reconstruct from memory) and resumes at the NEXT stage after the last dated writer-tagged section — never re-ask answered rounds, never re-run a scan whose recorded base SHAs still match. Frontmatter: title `Idea — <component> <future query>`, type `note`, status `open`, tags `[mission-control]`, plus optional `created: <date>` (Basic Memory stamps its own timestamps; convenience, not a requirement). Body grammar: H1 title; the literal unbolded `Idea: <one sentence>` as the first body line — plan copies it verbatim into the program note's `Objective:`; `## Problem` (the why + SC-1…n); `## User stories`; `## End-to-end workflows` (user-facing to back end, scan-verified steps carry path anchors); `## Scope and non-goals` (every exclusion reasoned); `## Decisions` (id, date, decision, rationale, alternative rejected, source round); `## Reality-check findings` (atomic: [finding|risk|collision|reusable] + value + provenance anchor + date + confidence; no pronouns, no code blocks, at most 25); `## Open questions` (each with a default); `## Risks` (riskiest assumption first, with its check); `## Handoff to plan` (repos, base SHAs at scan time, scan date, what plan must import); `## Relations` edges. No secrets, no session ids, no hostnames: evidence anchors are repo-relative or `~/` paths only.
6. **Pre-flight gate (fail-loud) then handoff.** All six pass or the gap is named and fixed in turn: (a) the `Idea:` line is exactly one unbolded sentence; (b) every non-goal carries a reason; (c) every finding carries an evidence anchor, date, and confidence; (d) every open question carries a default; (e) at least one end-to-end workflow spans user-facing to back end; (f) every decision names its rejected alternative. Items (c) and (e) degrade as recorded gaps when the operator skipped the scan (step 3) — never silently pass. Then print the handoff block: idea slug, note permalink, and the exact next command — `/mission-control-plan <slug>`.

`discuss` never registers anything on the Wall, never forges prompts or rows, never runs gates or test suites, and never edits any repo file. If the vault is unreachable: interrogate and scan if asked, but stop before the idea note — an unrecorded refinement is lost exactly where it matters most.
```

---

## 3. The other SKILL edits (same PR)

1. **Frontmatter (`SKILL.md:4-5`):** description's command list gains `/mission-control-discuss`
   first; argument-hint becomes
   `<discuss|plan|prompts|verify|next|close> [idea | objective | program-slug | session/MR reference]`
   — token set MUST equal case 04's MODES after its update (`tests/cases/04-command-surface.sh:27-32`).
2. **§1 vault-unreachable fallback list (`SKILL.md:38`):** add at the head of the per-mode list:
   `` `discuss` may interrogate and scan but stops before the idea note (no note, no handoff); `plan` may gather recon but stops before design (no note, no program); … `` (rest unchanged).
3. **Renumbered plan section (new §3) gains step 1** (existing steps 1-6 become 2-7 — so the
   question/persona step persona-gate.md cites becomes **step 6**, see item 8; wording otherwise
   untouched): the idea-intake step quoted verbatim in `docs/design-mc-discuss.md` §7 Edit 2 —
   resolve the slug (read_note on permalink `ideas/idea-<slug>`, search fallback; non-open status →
   say so and treat as a raw objective; a note still open but whose latest observation is
   [consumed one-shot]/[consumed] → report consumed, treat as raw objective; fuzzy search yielding
   more than one plausible match → list and ask; slug matching BOTH an open idea note and an active
   program → one disambiguation question, never a guess). The imports AND the supersede both branch on triage:
   program → `Idea:` line → `Objective:` verbatim, decisions/defaults → program-note assumptions,
   findings seed recon and are RE-VERIFIED, then status `superseded` + `superseded_by:`
   program-note permalink (read back after write) + dated `[consumed]` observation + `relates_to`
   edge (plain-text `memory://` pointer instead of the edge if the program maps outside `shared`);
   one-shot → the `Idea:` line becomes the light prompt's goal statement, decisions/defaults/findings
   fold into the emitted prompt per the anatomy's One-shot row (no program note, no recon step), and
   a dated `[consumed one-shot]` observation naming the emitted light prompt + controller session id
   is recorded — `superseded_by` only if a bm-decide note is written, else the note stays `open`;
   mission-control never deletes an idea note.

---

## 4. Registration checklist (wiring is mechanical, in this order)

**Repo-side:**

- [ ] 1. **RED first:** add `tests/cases/07-discuss-intake.sh` modeled on case 06 — extract the `## §2`
  section (the case 06 awk extractor pattern) and pin ~10 literal phrases from the section text above
  (suggested pins — each grep -F-matched against the section text verbatim: "at least three named
  challenges", "strongest objection first", "2-5 read-only scouts", "NEVER run tests, gates,
  builds, or installs", "a finding without an evidence anchor is discarded", "exactly 1, max 4
  forks", "literal unbolded `Idea:", "every open question carries a default", "riskiest assumption
  first", "an unrecorded refinement is lost exactly where it matters most"). Run it against the
  current SKILL.md and commit the failure
  first (house RED-first convention — e.g. `f6ce09e` before `dbac366` in this repo's history).
- [ ] 2. Renumber SKILL §2-§8 → §3-§9. Rewrite EVERY internal §-cross-reference in `SKILL.md`
  (grep -n '§' and apply the mapping: plan §2→§3, prompts §3→§4, verify §4→§5 incl. §4.0→§5.0 and
  §4.5→§5.5, next §5→§6, close §6→§7, Never §7→§8, Always §8→§9). CAUTION: SKILL.md carries its
  own interface-INTERNAL bare §N tokens pointing into `references/regenloop-interface.md`'s own
  sections — grep-verified: line 15 "(its §10)", line 47 "§10", line 60 "§8/§10" (the SAME line also
  carries the mode ref §3 — mixed kinds in one sentence), line 78 "§6" (interface doc §6, NOT close
  mode, though "§6" is a mapping key in shape). Rewrite per-token with the surrounding-sentence
  check, never line-wide: a blind §6→§7 sed corrupts line 78.
- [ ] 2b. **Shipped reference-file §-citations** (nothing test-pins them; grep-verified against the
  current tree): run `grep -rn 'SKILL §' plugins/mission-control --include='*.md'` and apply ONLY
  the MC-SKILL citation moves — `references/prompt-anatomy.md:47` and `:84` (SKILL §3 → §4, the
  prompts section), `references/regenloop-interface.md:170` (SKILL §4 → §5), `references/verify-runbook.md:1`
  and `:3` (SKILL §4 → §5), plus persona-gate.md per item 8. CAUTION: regenloop-interface.md carries
  regenloop-INTERNAL § references as bare `§N` tokens WITHOUT the `SKILL` prefix (e.g. lines 5, 8,
  22, 28, 41, 60, 96, 111, 155, 167, 185, 201, 213-214, 225, 232 — its own §1-§10 sections and
  regenloop-skill section refs); the `SKILL §` prefix is the discriminator — bare `§N` tokens in
  that file are NOT rewritten. RESIDUAL the grep discriminator also misses, grep-verified in the
  command files: `commands/mission-control-next.md:10` "emit the next prompts via §3" is an MC-SKILL
  citation (prompts) WITHOUT the prefix — rewrite to "via §4" in the same pass. Do NOT touch
  `commands/mission-control-verify.md:10` "(§9 verify-side knowledge" or
  `commands/mission-control-plan.md:10` "per its §10" — both are regenloop-interface-internal
  section refs (the interface doc's own §9/§10), not MC-SKILL citations.
- [ ] 3. Insert the new `## §2` section from this file's §2 above; apply SKILL frontmatter + §1
  fallback edits from §3 above; apply the plan-section idea-intake step (`docs/design-mc-discuss.md`
  §7 Edit 2).
- [ ] 4. Copy the command file (§1 above) to `plugins/mission-control/commands/mission-control-discuss.md`.
- [ ] 5. Update the five existing command files' §-pins: plan `Follow §2 (plan)` → `Follow §3 (plan)`
  (+ the Operator-input line and lead-in rewording per `docs/design-mc-discuss.md` §7 Edit 1);
  prompts §3→§4; verify §4→§5; next §5→§6 AND next's unprefixed "emit the next prompts via §3" →
  "via §4" (item 2b residual); close §6→§7.
- [ ] 6. `tests/cases/04-command-surface.sh:8`: MODES becomes `discuss plan prompts verify next close`.
- [ ] 7. `tests/cases/06-wall-registration.sh`: the §-extractor's `S2=$(sec "§2")` becomes
  `S3=$(sec "§3")` and every "§2 …" fail-message names §3 (plan's wall registration moved);
  §1 pins unchanged.
- [ ] 8. `references/persona-gate.md:3`, three changes in the line: "Wired into two places" → three
  (discuss pre-answers operator-facing forks with ADVISORY rulings); `(SKILL §2 step 5)` →
  `(SKILL §3 step 6)` — NOT step 5: plan renumbers to §3 AND its new idea-intake step 1 shifts the
  question/persona step from 5 to 6, so a §3-step-5 citation would point at "Design the waves";
  `§4 is evidence-only` → `§5 is evidence-only` (verify renumbers). Verify-ban clause otherwise
  unchanged.
- [ ] 9. `README.md`: pipeline line gains `DISCUSS → ` at the head (`README.md:7-12`); the
  `README.md:14` heading "## The five modes" → "## The six modes"; the table under it becomes six
  with `/mission-control-discuss <idea>` first; and `README.md:24` "All five also
  work through the base skill" → "All six also work through the base skill".
- [ ] 10. CHANGELOG under `[Unreleased]`:
  ```markdown
  ### Added
  - **`/mission-control-discuss <idea | idea-slug>`** — the intake phase before `plan`:
    interrogates the raw idea (dimension ladder; ≥3 named challenges per round, strongest objection
    first; contradiction hunt; batched forks with recommendations), runs a 2-5-scout read-only
    codebase reality scan (no gates, no test runs), holds one post-reality-check round, and writes
    the idea note to the vault (`shared/ideas/`, literal `Idea:` line, decisions, evidence-anchored
    findings, handoff contract). `plan <idea-slug>` consumes the note as its objective verbatim and
    supersedes it in the same turn. SKILL sections renumbered: discuss = §2, plan→§3 … Always→§9.
  ```
- [ ] 11. **plugin.json: no command registration exists to make** (commands are directory-driven;
  the manifest carries no commands/skills arrays — `scripts/verify_packaging.sh:24-32`). The only
  plugin.json edit is the release version bump.
- [ ] 12. **Wall registration: NOT required.** Registration is per-PROGRAM at plan
  (`SKILL.md:51-53`); discuss mints no wall state by design (`docs/design-mc-discuss.md` §2, D3).
- [ ] 13. `tests/required-tools.txt`: no change — every tool the new section names (Agent,
  AskUserQuestion, Read, Glob, Grep, Bash, the mcp__shared-memory__ family) is already in
  allowed-tools (`SKILL.md:6`, case 03 verified against the section text).
- [ ] 14. Version bump at release: `plugins/mission-control/.claude-plugin/plugin.json` +
  SKILL frontmatter `version:` together, CHANGELOG head entry, tag `v1.10.0` (minor: additive),
  `bash scripts/verify_packaging.sh` before the tag (four-way identity is blocking).

**Vault-side (implementing session, per `docs/design-mc-discuss.md` D2/D4):**

- [ ] 15. Create the `ideas/` channel in the shared vault project: amend the hub folder map
  (`~/work/memory-vault/shared/shared-memory-hub.md`) with an `ideas/` line and add the routing
  line to `~/work/memory-vault/shared/_conventions.md` folder taxonomy (one sentence:
  "ideas to ideas/ — pre-plan proposal notes consumed by mission-control plan"). The first
  `write_note(directory='ideas')` creates the directory. Route by SERVER, never a `project`
  parameter — the connected Basic Memory server is constrained to `--project shared` (the
  discard-parameter mechanism is a vault-conventions research-stream claim, not re-verified this
  session; the instruction itself stands either way).

**Gates to run at wiring time** (smoke + packaging were run after authoring these docs — both green
against the current five-mode surface; the runs below re-apply once the wiring edits land):

- [ ] 16. `bash tests/run_smoke.sh` — exit 0 (cases 01-07).
- [ ] 17. `bash scripts/verify_packaging.sh` — blocking checks green at the new version.
- [ ] 18. Wall suites are untouched by this change; run them only if the renumber touched anything
  under `plugins/mission-control/wall/` (it should not): `.venv/bin/pytest -q` with
  `MC_WALL_DISCOVERY=0` and `node web/selftest.mjs` from the wall root.
- [ ] 19. Shakedown per `README.md` §Shakedown: one small real idea through
  `discuss → plan`, checking the `Idea:`→`Objective:` copy, the same-turn supersede, and that the
  wall still boots with an ideas/ dir present in the vault (discovery must ignore it — it scans
  programs/ and declared note_globs only). Expect the FIRST live write to possibly need one title
  correction: how basic-memory kebabizes the em dash in `Idea — <slug>` is unverified
  (design §2 known residual) — the verify-filename-AND-permalink loop catches it; record the
  observed kebabization in the shakedown notes so the rule is grounded for every later note.
- [ ] 20. At shakedown, confirm the §4.1 MEMORY-SAFE reading with the operator — "a 2-5 read-only
  scout batch is not a heavy test/gate process and may run as one batch" — and record the ruling
  (program-note decision line, or a bm-decide note if it should bind beyond the program). If the
  operator rules otherwise: serialize the scouts (one at a time, same contracts); nothing else in
  the design changes. The design-level reading in `docs/design-mc-discuss.md` §4.1 is provisional
  until this confirmation exists.
