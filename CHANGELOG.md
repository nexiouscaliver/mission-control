# Changelog

All notable changes to Mission Control will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

New changes accumulate here between releases, above the latest version entry (Keep a Changelog convention; the release gates skip this section when reading the head version).

## [1.12.0] - 2026-10-07

The registration release: the operator gets deterministic, restart-free commands for the wall.json program surface and the lane↔session binding (idea note `shared/ideas/idea-wall-registration-commands-and-old-session-migration`, decisions D1-D5), plus a guided migration path for historical sessions. Driven by three impossibilities: adding a program required controller-side wall.json surgery, removing a finished program was impossible at all (grammar-correct notes re-register via discovery), and pre-window historical sessions could never reach their lanes.

### Added (wall — registration commands, rg1)
- **`bin/mc-wall register <slug|note-path>`**: declares a program in wall.json `programs[]` (tag = slug for new entries; existing tags never rewritten; changed note_globs repointed) and merges repos derived from the note's row path tokens into `repos[]` — the same `git remote -v` derivation discovery runs for undeclared programs, so declared programs keep their git signals. Lint-first: any lint defect refuses the registration with the lint output. Idempotent (nothing to change → nothing written).
- **`bin/mc-wall deregister <slug>`**: removes the declaration AND appends the slug to the new wall.json `ignore[]` — deregistration is REAL. Backs up every write to `~/.mc-wall/backups/` first; refuses unknown slugs; idempotent.
- **wall.json `ignore[]` (discovery exclusion list, D2)**: `tower_boot` parses and validates the key loudly (bad shape = boot error, not a silent no-op); every discovery scan (boot AND per-poll) skips ignored slugs exactly like declared ones. Declared entries still win structurally. `TowerConfig.discovery_ignore` carries the list; `discover_programs(ignored_slugs=…)`.
- **`bin/mc-wall list`**: declared / discovered / ignored programs with parsed lane counts, lint status (including the forge-manifest cross-check), and note paths; exit 1 on any defect or glob miss.
- **`bin/mc-wall bind <slug> <row-id> <sess-id>`**: writes the session token into the row's session/MR-artifacts cell — the first `sess_` token is the binding, so rebinding REPLACES an existing token while `verify:ok` and MR refs survive; null cells become the bare token. The vault-note edit is backup-first + atomic, post-edit lint refuses defective writes, the session db is pre-checked (zero-match tokens refused; an unreadable db degrades to a warning like the wall itself), and the `/state` assert line (`state: program=… row=… session=… — bound`) is polled across up to three poll beats.
- **`mc_wall/tower/registration.py`**: the pure transform layer (register/deregister transforms, row binding, backup naming, atomic writes, slug lookup) — no I/O in the transforms, so tests pin exact mutations; `notes.locate_row` is the write-side row anchor using the production parser's table detection.

### Changed
- **Skill §7 close**: gains step 5 — deregister the program from the wall after the note deletion (`mc-wall deregister <slug>`), with the ignore[] rationale and the verify step (`list` shows ignored, `/state` drops the program); the section is now "distill, record, delete, deregister".
- **README**: new §7 "Program registration commands" documents all four commands, the backup-first/per-poll/no-restart contracts, and the ignore[] semantics (Failure modes and Repo layout renumbered §8/§9).

### Tests
- 20 new rg1- tests: tower ignore semantics (skip, control, declared-beats-ignore, boot validation incl. loud bad-shape errors, per-poll refresh) and the CLI surface (register by path/slug, lint refusal, idempotency, token/port/entry preservation, repo derivation, stale-ignore clearing, deregister remove+ignore+backup+idempotent+unknown-refusal+boot-respected, list kinds/lanes/lint, bind happy/idempotent/rebind/refusals/dispatch).

## [1.11.1] - 2026-10-07

The wall-honesty release, motivated by the 2026-10-07 lane-invisibility incident: a blank line inside the wall-overhaul program note's prompt-log table made the parser silently drop ALL five lane rows (rendered as a 0-lane card) while the four lane sessions were simultaneously excluded from the unmapped strip by their title tags — double invisibility, zero defects, four verdicts written on top. The operator's ruling: any single instance where the wall is incorrect with the real state is unacceptable. This release installs the invariant: the wall is NEVER silently wrong.

### Added (wall — wall-honesty invariants, W5-L5)
- **Stray-row defects**: a lane-shaped line outside any table (row-id-shaped first cell — distinguished from the notes' other markdown tables) records a `lane row outside prompt-log table` defect instead of vanishing.
- **Blank-line tolerance + defect**: a blank line inside the table region (followed within 2 lines by another table line) no longer closes the table — rows beneath it parse — and each such blank records a visible defect. The incident's exact shape can never silently zero a program again.
- **Row-width mismatch defects**: a row with MORE cells than its header parses its known-prefix cells AND defects per row (`N extra cells under variant-X header`) — extra cells are no longer silently truncated.
- **Cell-parse defects**: a non-empty repo/branch cell that parses to None defects naming the cell and what failed — the lane's silently-lost git signals are now visible.
- **Forge-manifest cross-check (the day-one alarm)**: per program, every write-once manifest row_id under `~/.mc-wall/forge/<program>/*/manifest.json` absent from the note parse records a TOP (`line 0`) defect — `forged row <id> absent from note parse (manifest exists)`. Survives table breaks of ANY cause; fires even on glob-miss notes.
- **Session conservation + orphan surfacing**: every session in the activity window is accounted (lane-bound, unmapped, master, orphan-tagged, or background); tagged-for-a-known-program sessions that bind to no lane surface in the new `sessions_orphaned` state key (id/title/tag/last_active_ago_s); an unaccountable session emits a `conservation defect: N sessions unaccounted` degraded line. The incident's invisible class is now a first-class surface.
- **`bin/mc-wall lint-note <note>` / `--all`**: the write-side hook controllers run after every row edit — the SAME parser the server collects with (one truth), defects printed with their lines, a per-note "N rows parsed" summary line, exit 1 on any defect; `--all` walks every declared + discovered program.
- **Web**: the five new defect kinds render through the existing PARSE DEFECTS grammar (mock-full carries one row per class); orphaned sessions render as their own collapsed strip section with the same reveal pattern — including when the unmapped list is empty (the incident's exact shape); the defect strip now aggregates per-program defects when the root key is absent (pre-v3 server behind fresh assets).
- `/state` `schema_version` 3 (additive root keys `sessions_orphaned` + root `parse_defects` aggregation; `merges[]` from W4-L4 rides the same version).

### Changed (skill — controller protocol v1.11.1)
- **§3 step 8 (plan)**: after the program note is written and registered, the controller MUST run `bin/mc-wall lint-note` and the `/state` probe asserting the parsed lane count equals the rows written; both probe lines recorded in the program note; a mismatch is a STOP-and-diagnose.
- **§4 step 5 (forge)**: after EVERY prompt-log row edit, the same two-step verification within one poll beat, evidence appended with the row.
- **§5 step 7 (verify)**: every verify turn closes with a wall-reality check — `/state` must show the row's status/session/branch matching the verdict just written; any wall-vs-vault mismatch is a FINDING in the verdict, never a footnote.
- **§1 grammar**: the prompt-log table must be contiguous (no blank lines inside it); `mc-wall lint-note` is the pre-flight after any row edit; the fail-visible sentence now names the wall-honesty defect classes.
- **Case 06 extended** with the protocol pins (lint-note + `/state` assert in §3/§4/§5, the blank-line ban).

Gates: `tests/run_smoke.sh` exit 0 (7/7 cases, case 06 extended); `scripts/verify_packaging.sh` green at 1.11.1; wall suite — hermetic pytest 344 passed (MC_WALL_DISCOVERY=0; 317 baseline + 21 wh1 tower + 6 wh1 lint) + web selftest 115/115 (112 baseline + 3 wh1).

## [1.11.0] - 2026-10-06

The skill now teaches the system that exists: the Wall's contract v2 — 9-cell deps rows, per-poll registration, fail-visible parsing — is what `/mission-control` forges and verifies against. Program-end release of the wall-overhaul program (with its sibling v1.10.1).

### Changed
- **SKILL §1 program-note grammar re-specified to the 9-cell variant-C row** — `id | wave | lane | repo/branch | slug | base | session/MR artifacts | status | deps` (contract v2): the deps cell carries the row_ids the lane gates on (comma/space-separated, `—` when none); the legacy 8-cell A and B header variants still parse unchanged, so old programs are never rewritten. Malformed rows are fail-visible, not silent: the old "a 7-cell row is silently skipped" text is replaced with the truth — short-celled, empty-id, or off-vocabulary rows surface on the Wall as parse defects naming the note path, the 1-based line, the defect, and the row_id when known.
- **SKILL §3 step 7 registration is per-poll — no restart**: the Wall re-checks wall.json's mtime on every collect cycle, so a plan-time registration is visible on the next ~5 s poll; the BOOT-only/restart instruction is gone (a restart remains necessary only for wall code changes or env, never for registration). The `/state` verify now expects the program present with its lanes and READS `degraded` lines (fail-visible defects may legitimately appear there) rather than demanding the field empty.
- **SKILL §4 step 5 forge writes the deps cell** from the plan's wave-table dependency edges — each row's deps list the row_ids it gates on, `—` when none; derived from the plan, never improvised at emit time.
- **Case 06 re-pinned** to the per-poll + 9-cell grammar: positive pins for `EXACTLY 9 cells`, the deps-cell format, legacy-header compatibility, the fail-visible parse-defect rule, and per-poll/NO-restart registration; negative guards fail the suite if the stale `BOOT only` or `silently skipped` text returns.

### Added (wall — tower contract v2, merged in PR #16)
- **Deps variant C** prompt-log parsing (9-cell trailing `deps` column, backward-compatible with the A/B headers); **fail-visible parse defects** per malformed row (`note_path`, 1-based `line`, `defect`, `row_id` when known) surfaced on the Wall and in `/state` `parse_defects`; **`verified` / `verify_due`** lane fields in the `/state` payload; **per-poll registration** (wall.json mtime re-check + discovery re-scan every collect cycle — new programs and note edits appear on the next poll with no restart); **`needs_me`** state-level aggregation (merge-ready + verify-due + stalled); **verify:ok token-clearing** of the verify queue; the **find_row "rows"-key fix** (POST /launch 404); and **`fix/<name>` branch-form parsing** in `_BRANCH_RE`. `/state` `schema_version` 2.

### Fixed (wall — released in the sibling v1.10.1, PR #14)
- **Unmapped group recency ordering + 50vh strip cap** — detailed in the [1.10.1] entry below; named here so the program-end release notes cover the full wall-overhaul delta.

Gates: `tests/run_smoke.sh` exit 0 (7/7 cases, case 06 re-pinned); `scripts/verify_packaging.sh` green at 1.11.0. Wall suite not run by this lane (owns no `wall/` files) — covered by PR #16's receipts at the identical tree (hermetic pytest 317 passed + web selftest 106/106, `MC_WALL_DISCOVERY=0`).

## [1.10.1] - 2026-10-06

The Wall's busiest repos surface at the top of the unmapped strip instead of burying themselves down its scroll box.

### Fixed
- **WALL-view unmapped groups now order by latest session activity (was alphabetical)** (wall web app) — `renderUnmappedGroups` sorted dir groups A→Z, so with 423 sessions the machine's most-active repos rendered ~3000px down the strip's 240px scroll box (measured live 2026-10-05). Groups now sort by their newest row's `last_active_ago_s` (smallest first — the group holding the most-recently-active session leads), ties broken by dir name ascending for determinism. Within-group row order (already most-recent-first), group head text, the idle>24h collapse, the show-all toggle, and the PROJECTS view are unchanged; `web/app.js` and `web/assets/app.js` stay byte-identical.

Gates: wall hermetic pytest 291 passed (`MC_WALL_DISCOVERY=0`); web selftest 106/106; `tests/run_smoke.sh` exit 0; `scripts/verify_packaging.sh` green at 1.9.2.

## [1.10.0] - 2026-10-06

The intake phase before plan: `/mission-control-discuss` interrogates a raw idea against the operator and the codebase, then writes the vault idea note `plan` consumes verbatim as its objective.

### Added
- **`/mission-control-discuss <idea | idea-slug>`** — the intake phase before `plan`:
  interrogates the raw idea (dimension ladder; ≥3 named challenges per round, strongest objection
  first; contradiction hunt; batched forks with recommendations), runs a 2-5-scout read-only
  codebase reality scan (no gates, no test runs), holds one post-reality-check round, and writes
  the idea note to the vault (`shared/ideas/`, literal `Idea:` line, decisions, evidence-anchored
  findings, handoff contract). `plan <idea-slug>` consumes the note as its objective verbatim and
  supersedes it in the same turn. SKILL sections renumbered: discuss = §2, plan→§3 … Always→§9.

Gates: `tests/run_smoke.sh` exit 0 (7/7 cases incl. the new case 07 discuss-intake pins); `scripts/verify_packaging.sh` green at 1.10.0. Wall pytest/selftest not run — this change touches nothing under `plugins/mission-control/wall/` (0-line diff).

## [1.9.1] - 2026-10-05

Long-lived sessions stay visible on the Wall while they are active: the unmapped-sessions window is now an activity window.

### Fixed
- **Unmapped-sessions window rides `time_updated`** (wall tower) — `unmapped_rows` and `scan_title_bindings` filtered on `time_created`, so a session created more than 24 h ago was invisible from `sessions_unmapped` forever, even while actively running (verified live: three sessions touched within the hour, created Sep 12–Oct 1, all invisible). Both queries now cut on `time_updated`, and the `session_window_s` default widens 86400 → 604800 (7 days): a session lingers visible for a week after its last touch, while the UI's idle>24 h collapse keeps old ones visually quiet.

Gates: wall hermetic pytest 291 passed (`MC_WALL_DISCOVERY=0`); web selftest 104/104; `tests/run_smoke.sh` exit 0; `scripts/verify_packaging.sh` green at 1.9.1.

## [1.9.0] - 2026-09-24

Wall autonomy: lanes bind to their sessions from the prompt's own tag line, and the verify cue is machine-clearable.

### Added
- **Titled-form tag grammar + unmapped exclusion** (wall tower, `zcode_db` parse layer) — a pasted `Session title: [<program-tag> <row-id>] <name>` line now parses (name optional, whitespace-tolerant inside the bracket); the bare single-token form is bit-for-bit unchanged. One `sendText` scan now yields two views (tags for masters/entry-7, bindings for lanes); titled configured-tag pastes are excluded from `sessions_unmapped` exactly like bare ones.
- **Tag-driven lane binding** (wall tower) — precedence `row-token > pasted-tag > title-tag`: a lane whose artifacts cell carries a `sess_` token never binds by tag; token-less lanes bind from pasted tag lines, with a title fallback (24 h window) for goal-steered sessions the `sendText` scan never sees. Contention (≥2 sessions claiming one program,row) resolves newest `time_updated` wins, one degraded line naming the losers; losers and ambiguous sessions (≥2 pasted rows → nothing bound) render in `sessions_unmapped`. The session store remains strictly read-only (byte-hash proven).
- **`verify:ok` verified token** — the controller writes it into the row's session/MR-artifacts cell at verdict time; the Wall's lane-level `suggest_verify` cue clears on reading it. `verify_queue` is unchanged — verified rows keep their queue entry.
- **Autonomy docs** (wall) — `docs/autonomy.md` documents the binding precedence chain, the ambiguity rule, the verified token, and the read-only guarantee; README §5 names the chain and links the doc.

### Changed
- **Launch paste-back retired** (SKILL §5/§1/§4, prompt anatomy) — the prompt-anatomy launch block's base-recheck field and SKILL §5's stall sweep now advance `forged` rows from machine launch evidence (goal dir, branch on remote, tag-scanned session) in one batch sweep, never from operator prose; §4/§1 document the `verify:ok` convention; anatomy item 16's scan source corrected to `session_input`.
- A multi-token bracket with no trailing name (previously one opaque tag) is now a titled form — binding cannot depend on a name the app may trim; masters/entry-7 semantics operate on program tags (normalization keeps the exact-singleton rule).

Gates: wall hermetic pytest 285 passed (`MC_WALL_DISCOVERY=0`); web selftest 104/104; `tests/run_smoke.sh` cases 01-06 PASS; `scripts/verify_packaging.sh` green at 1.9.0.

## [1.8.0] - 2026-09-22

Programs appear on the MC Wall the moment they are planned: plan mode registers them in `~/.mc-wall/wall.json` itself, and the program note gains the grammar the Wall's parser actually reads.

### Added
- **Plan-mode Wall registration** (SKILL §2 step 6) — after `write_note` records the program note, the controller registers the program on the Wall, idempotently, as a plain machine-config file edit plus shell commands (no MCP tool). When `~/.mc-wall/wall.json` exists: a timestamped backup copy first, then `{program: <slug>, tag: <slug>, note_glob: <program note path>}` appended to `programs[]` if no entry with that program name exists, and `{name, path, host}` appended to `repos[]` for each repo the waves work if no matching entry exists — written back as valid JSON preserving `token`, `port`, and every existing entry. The step states the Wall reads this config at BOOT only (restart via `launchctl kickstart -k gui/$(id -u)/ai.zcode.mc-wall`) and verifies via `curl -s "http://localhost:<port>/<token>/state"` — program present with its lanes, `degraded` empty. When the file does not exist (wall not installed): registration is skipped and the program note records "wall not installed" — the file is never created.
- **Program-note grammar for the Wall** (SKILL §1) — the note-shape rules the Wall's parser taught us: prompt-log rows carry EXACTLY 8 cells (a 7-cell row is silently skipped); the repo/branch cell carries a path token (`~/…` or `/…` prefix — a bare repo name parses repo=None and the lane loses its git signals); never emit pre-forge placeholder rows (the status vocabulary has no "planned" state — a row is born at forge as `forged`); the objective line is unbolded `Objective:` (`**Objective:**` does not parse).
- **Smoke case 06 — wall registration** (`tests/cases/06-wall-registration.sh`) — pins the §2 registration behavior (wall.json, idempotent append, timestamped backup, kickstart restart, state probe, skip-when-absent) and the §1 grammar rules (8-cell rows, path token, no placeholder rows, unbolded objective, fork-disclosure display rule); red at the 1.7.0 base by design.

### Changed
- **Fork disclosure display rule** (SKILL §1, operator ruling 2026-09-22) — for a program declared on the Wall, off-mode fork disclosure lives in evidence prose only, never as a prompt-log row: an FX row renders as a lane on the Wall and misrepresents the program. Programs not on the Wall keep the FX row as before; the same-turn evidence-line disclosure requirement is unchanged for both.

Gates: `tests/run_smoke.sh` (6 cases incl. the new 06) and `scripts/verify_packaging.sh` green at 1.8.0.

## [1.7.0] - 2026-09-22

Parent-session lineage for the Wall: every workflow-actor cluster and side chat links to the conversation that spawned it, readable at a single glance (PR #6).

### Added
- **True parent lineage in the state contract** — the tower reads `session.parent_id` from the zcode session db (read-only; verified populated for both workflow actors and side chats) and emits `parent_session_id` on `lanes[].session` and every `sessions_unmapped` row; an older db without the column degrades parents to null, never schema drift. `parent_title` additionally resolves the parent conversation's name by id, unwindowed — the parent chat is usually older than the 24 h session window, exactly when client-side resolution would fail.
- **Glanceable run clusters (wall)** — the hidden background section leads each run cluster with the parent conversation's title over a dim `project · run <first8> · N actors` meta line; side-chat rows show `↳ <parent title>` plus the project segment (full dir on hover). Unresolvable parents degrade to a short id; the full id always rides the hover; a rare multi-parent run falls back to per-parent sub-heads.
- **One expandable row per workflow run (projects view)** — same-run actors fold into a single row leading with what the workflow did, counting its actors, expandable in place to the individual rows; expansion survives the 5 s poll rebuilds and the card's session total stays weight-honest.
- **Parent lineage in the projects view** — background rows show `↳ <parent>` inline (hover still carries the full parent id).

### Changed
- Run ids demoted to metadata — cluster leads are the task, not the uuid; wrong-typed or missing parent data reads as "no parent", never a crash or a raw id dump.

## [1.6.0] - 2026-09-22

The Wall UI, redesigned end to end around operator triage: severity-ordered lanes, an adaptive layout with no dead space, a PROJECTS view, and background sessions that stay out of sight until asked (PR #4).

### Added
- **PROJECTS view** (WALL/PROJECTS switcher in the top bar) — every session (mapped lanes, masters, unmapped) grouped by project, project name only with the full path on hover, most-recently-active first, a recent/name sort toggle, and click-to-copy session ids (short form; full id copied).
- **Background-session hiding** — workflow subagent sessions and side chats are classified from the state the wall already carries (subagent ids embed their workflow run id; side chats by title) and hidden by default: the strip reads `unmapped (N · M hidden)`, and the reveal clusters every actor under its workflow run (`workflow run <id> · count`, full id on hover) with side chats grouped separately. The projects view gets a `background: hidden/shown` toggle and kind tags. The reveal and all reading state survive the 5s poll rebuilds.
- **Adaptive grid** — contentless columns leave the layout; sole content centers full-width; a fully-empty wall hides the grid behind an "All clear" hero; a slim context line replaces the empty programs column when no programs are registered. No more half-empty dashboards.
- **Discoverability** — shortcut legend (n needs-me-now · r refresh · esc close panel), persistent lane-open affordance with hover elevation, `show all ▾` on the unmapped strip, guided copy on every empty and error state, and a human "wrong or expired link" error page.
- **Launch journey made visible** — the clickable forged lane opens a restructured launch side panel (LANE / MANIFEST sections, mirrored status chip, esc hint, backdrop click-close, focus return).

### Changed
- **Severity-first triage order** — lanes sort failed → watch (UNPARSED / partial / stalled) → healthy (stable within tier); verify queue renders oldest-first (the same order the needs-me-now jump walks); NOT-ready merges float above ready ones with readiness edges; all pinned by DOM-order assertions in the wall selftest.
- **Chip grammar v2** — additive severity axis over the provenance classes (failed red / watch amber / ok green), per-status variants (done quiet, in-flight filled, parked dim), and the program-level authority stamp renders once on the card head instead of on every chip.
- **Accent discipline** — amber is the "your move" family (armed strip, owed NEEDS ME NOW, COPY VERIFY, verify-row edges), red reserved for blocked/broken, cyan for interactive (focus, hover, jump flash).
- **Armed indicator promoted to its own full-width strip** — re-copy / cancel / × stay clickable while the launch panel is open, docking flush against the panel edge; the top bar becomes three labeled clusters (identity / owed / health).
- NEEDS ME NOW counter reads "N verify · M merge"; a zero-owed click confirms with its own "all clear ✓" transient instead of echoing the hint.
- NEEDS ME NOW jump: page-side fallback and panel ordering agree (oldest verify head wins).

### Fixed
- The armed strip no longer clips the launch panel's first rows (panel is border-box so its drawn width matches the dock; verified flush geometrically).
- Poll rebuilds no longer reset the session list's scroll position or re-collapse expanded idle>24h groups.
- Boot renders one waiting box per column under an amber pulsing "booting" dot — red now means measured staleness only.
- Unknown GET/HEAD routes under a valid token 302 to the canonical wall page instead of dumping raw JSON (`mc_wall/server/app.py`).
- The launch panel's `pending:` line tracks goal-armed / cleared instead of going stale.

Gates: wall `.venv/bin/pytest -q` 234 passed; `node web/selftest.mjs` 101/101 (severity-order, adaptive-grid, hero-forms, booting-dot, projects and background-hiding contracts added); repo `tests/run_smoke.sh` and `scripts/verify_packaging.sh` green at 1.6.0.

## [1.5.0] - 2026-09-21

One repo for everything: the MC Wall joins mission-control with its full git history (subtree import at `plugins/mission-control/wall/`).

### Added
- **The Wall, in-repo and clone-and-run** — the entire mc-wall codebase (tower, server, web page, tests, e2e harness) imports under `plugins/mission-control/wall/` preserving its original commit history; `bin/mc-wall install` resolves the clone root as the parent of its own location and bakes it into the LaunchAgent's run.sh, so the Wall always runs from the clone — moving or re-cloning the repo means one re-run of `mc-wall install` (README §2).
- **Harness-neutral data home (`~/.mc-wall`)** — the wall's default home moves from `~/.zcode/mc-wall` to `~/.mc-wall` everywhere it is resolved (server resolver, module entry, control CLI, mc-status skill; `MC_WALL_HOME` override unchanged), created on first install.
- **ZCode session-store adapter seam** — the tower's session-store access goes through one boundary (`mc_wall/tower/session_store.py`; config: `TowerConfig.store`, default `zcode`, plus the db path; wall.json/tower.json accept a `"store"` key, unknown names fail loudly). Pure refactor, zero behavior change for the zcode path; a Claude Code adapter is documented as an extension point in the wall README — NOT IMPLEMENTED.
- Wall README gains the session-store adapter paragraph (NOT IMPLEMENTED extension point) and the moving/re-cloning note.

### Changed
- **Forge-artifact home paths in the skill** — SKILL §1/§3 and `references/verify-runbook.md` now write and read forge artifacts under `~/.mc-wall/forge/<program>/<row-id>/` (was `~/.zcode/mc-wall/forge/…`); pre-1.5.0 rows fall back to the runbook's existing "no forge artifacts" ask.
- Wall README §7 truthing: the B2 production gap (boot-time `_default_collect_state` TypeError) is marked fixed by F-1's built-once tower config; B3 remains open and documented.

## [1.4.0] - 2026-09-21

Proven in the mc-wall program (2026-09-19/21): every change below ran live as the controller's own skill during a full three-wave build.

### Added
- **Persona gate** (`references/persona-gate.md`) — design-time operator-fit review: grounded brief built only from project facts + vault user-notes (persona specifics labeled assumptions), fixed output contract (per-phase scores, ranked frictions, 9pm-confusion list, dealmakers/breakers, fork rulings with rationale, explicit verdict), two-round same-agent protocol with a lazier adversarial round 2, and hard epistemics — ADVISORY only, gates operator review never build, operator veto always wins, max 2 rounds, operator-facing artifacts only. Plan may pre-answer genuine forks with a persona ruling (SKILL §2); the red-team gate gains check 11 "Operator fit" (prompt-anatomy).
- **Forge artifacts** — `prompts` mode now writes write-once `prompt.md` / `goal.md` / `manifest.json` per forged row under `~/.zcode/mc-wall/forge/<program>/<row-id>/` and records the manifest path in the prompt-log row; content, never status — the note remains the single status authority (SKILL §1/§3).
- **Session-title line + goal block** (prompt-anatomy) — every forged prompt ends with `Session title: [<program> <W-L>] <name>` (the bracket tag is the program↔session join key, scanned read-only from the session store); the launch block gains the ready-to-paste `/goal` block that regenerates the title through the app's own generator.

### Changed
- **Verify runs the overlay-diff first** (SKILL §4, runbook commands 1–2): the session's actual pasted prompt and `/goal` text are extracted read-only from the session store's `session_input` and diffed against the forge artifacts — machine overlay detection instead of operator self-report; rows forged before artifacts existed fall back to the manual ask.

## [1.3.0] - 2026-09-17

Revised from the det-filter program (2026-09-15 → 09-17) — the skill's first full run; evaluation evidence retained locally (not published).

### Added
- **`references/regenloop-interface.md`** — the regenloop interface, hybrid by surface: [STABLE] contracts transcribed in full, [VOLATILE] facts as cited one-liners re-derived at every plan, [CONTROLLER] rules stated once; PLUGIN WINS on any disagreement. Points at the plugin's own stamped knowledge package (`knowledge/INDEX.md`, 29 cards) wherever the plugin already teaches — transcribed only what the cards don't carry (records paths, branch/worktree layout, budget arithmetic, controller rules).
- **`references/verify-runbook.md`** — the 15 ordered verify commands after the two setup lines: the machine-record reads behind SKILL §4, copy-pasteable.
- **Release gate + smoke harness** — `scripts/verify_packaging.sh` (both manifests parse, frontmatter required keys, every named `references/*.md` exists, version identity plugin.json == tag == CHANGELOG head == SKILL frontmatter; install-surface drift stays advisory) and `tests/run_smoke.sh` with five cases: anatomy invariants (14-item skeleton, module bindings), interface-doc citation resolution against a pinned regenloop 1.3.1 fixture (bracketed-key grammar, line-bounds, paired mechanism literals, fixture-target existence), allowed-tools coverage, command-surface consistency, version identity.
- **Version identity** — SKILL.md frontmatter now carries `version:` (1.3.0) alongside `plugin.json`; the packaging gate makes the four copies one invariant.

### Changed
- **Anatomy (`references/prompt-anatomy.md`)** — rewritten as a negative scope: §0 states what a forged prompt NEVER carries (records contract, controller rules, mechanics the loaded manual already owns), killing the hollow-prompt class; the 14-item skeleton; the canonical 8-field launch block as the one home (session type + envelope pin, run location + in-fence cwd, positioning command, base recheck + operator paste-back, caps + wall-clock T line, host/ship policy, operator launch gate, goal statement); the unified 10-check red-team gate v2 with one evidence line per check, replacing SKILL's inline 6-check list.
- **Forging substance** — base verification is tiered, not binary (descends from the pinned SHA with no owned-file overlap → adopt loudly; non-ancestor or overlap → STOP); `--slug` rides in the invocation line and the prompt-log row as the join key to goal dir, branch, and machine reports; stall rules are wall-clock only (the envelope owns retries; prompts carry a derived time bound T, or "T unset — operator wall-clock watch"); an active foreign goal is a continuation-misroute risk, never a kickoff STOP.
- **Verify (SKILL §4) — verify-from-records** — machine-written records (archive INDEX row, `record.json`, slug-scoped gate reports, queue terminal statuses, `ledger.jsonl`, `budget.json` tallies vs caps) are read BEFORE any hand re-run; a hand suite re-run is reserved for merged trees and report mismatches. Session-family rules: verification judges the branch/worktree/report family the session actually produced, never the handoff capsule's prose. Fork rules: merge ancestry from the recorded base — equality with the forge-time SHA is never the criterion; a "done" claim with a goal still in `goals/` fails cleanup. §5 `next` gains a liveness sweep (pushed? goal still ACTIVE? MR/PR state?) that prints stalled rows above the owed-actions list.
- **Caps canon + ship policy + pin expiry** — caps have one home: `--safe` is the throttle (ceiling read from `regenloop_guard.py plan --json`, stamped with the run date) or the launch block exports the current env names — the legacy `REGENLOOP_SAFE_PYTEST_JOBS` mandate is gone; lane arithmetic may only lower load. Ship policy is decided per host from `git remote -v` at forge (`--ship` only where ship can run; GitHub-hosted lanes get `--no-ship` + an explicit human/`gh` PR path). Pins expire: the base pin is re-checked at launch (red-team check 9 freshness; paste-back recorded in the prompt-log row), and a Derived-from version older than the installed plugin is a hard stop for forging.
- **Lessons (`references/lessons.md`)** — two-tier provenance header (what is proven, where) + coherence edits: falsified folklore removed; L1/L3/L6 rewritten to match the shipped mechanics.
- **Commands** — consistent reference-load lines across all five; `/mission-control-next` takes `[program-slug]` with an Operator-input line; frontmatter `allowed-tools` now declares the vault MCP tools (including `mcp__shared-memory__delete_note`), AskUserQuestion, and ReadSessionContext — and drops Write/Edit (controller writes go through the vault MCP).

### Weight + loading policy (honest recount — measured line counts; tokens by the byte/4 heuristic)
| Surface | v1.2.0 | v1.3.0 | Δ |
|---|---|---|---|
| SKILL.md — ALWAYS-LOADED | 94 ln / ~2.8k tok | 125 ln / ~6.8k tok | **~2.2–2.5×** |
| anatomy (forge/plan/verify) | 44 ln / ~1.3k | 82 ln / ~4.4k | ~3.4× |
| interface doc (plan/prompts/verify/next; load-on-act) | — | 233 ln / ~4.4k | new |
| lessons (all 5 modes) | 36 ln / ~1.2k | 35 ln / ~1.4k | ~+15% |
| verify-runbook (verify only) | — | 25 ln / ~0.7k | new |
| **Full forging load** | **~5.4k tok** | **~17k tok** | **~3.1–3.2×** |

Loading policy: references load per-mode; the interface doc is load-on-act (at plan and before forging/verifying), never always-loaded — SKILL.md is the only always-loaded surface. Field trial: the first program run under v1.3.0 records forge turns + wall-clock per prompt against the det-filter baseline; the v1.3.x council trims advisory checks if forge cost measurably degrades. E18's per-check records are the trial's instrument.

### Fixed
- **Corrective note on 1.2.0's rename entry** — 1.2.0 said the commands were "previously the bare mode names, e.g. `/mission-control:plan`", conflating 1.1.0's bare *filenames* (`plan.md`) with the *namespaced invocation* (`/mission-control:plan`). History stays immutable; the correction ships forward.

## [1.2.0] - 2026-09-15

### Changed
- **Commands renamed to self-identifying names** — `/mission-control-plan`, `/mission-control-prompts`, `/mission-control-verify`, `/mission-control-next`, `/mission-control-close` (previously the bare mode names, e.g. `/mission-control:plan`). The embedded prefix keeps every command unambiguous and autocomplete-grouped under `/mission-control…` in any harness, namespaced or flat (the regenloop-plugin naming pattern). The 1.1.0 command names are replaced; the base skill form `/mission-control <mode>` still works.

## [1.1.0] - 2026-09-15

### Added
- **Slash entry points for all five modes** — `/mission-control:plan`, `:prompts`, `:verify`, `:next`, `:close`. Thin wrappers only: each passes its operator arguments straight through to its mode section, with the right references preloaded — `SKILL.md` remains the single source of truth and nothing about mode behavior changed.
- `verify` accepts a bare MR/PR number (`!1560`, `#42`), resolved to the prompt-log row via the program note — the most-used mode is now the most frictionless.

## [1.0.0] - 2026-09-15

First release.

### Added
- **The five modes** — `plan` (triage + mandatory recon + wave/lane design with file ownership and enablement ordering), `prompts` (forge + red-team, paste-ready, RAM-derived caps), `verify` (evidence-based re-checks of returning sessions; on-the-spot remediation prompts), `next` (unblocked work, human actions owed, enablement-sequence enforcement), `close` (distill knowledge into the memory vault by project, completion record, approval-first deletion of working docs).
- **Vault-native state model** — one program note per active program, per-project config-facts notes replacing any config file, zero files written into working repos.
- **The forge grammar** (`references/prompt-anatomy.md`) — the invariant prompt skeleton, the situational module library (Part 1/Part 2, deploy window, dogfood/canary, abort path, release prep, remediation, one-shot), the red-team gate, and goal statements.
- **Sixteen distilled lessons** (`references/lessons.md`) — the production failure modes behind every rule, proven on a 5-wave, ~15-session program with two kill-switch aborts that worked.
- **Marketplace packaging** — Claude Code / ZCode installable and updatable; single-plugin marketplace manifest mirroring the omniforge-plugin layout.
