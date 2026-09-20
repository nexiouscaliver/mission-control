# Changelog

All notable changes to Mission Control will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

New changes accumulate here between releases, above the latest version entry (Keep a Changelog convention; the release gates skip this section when reading the head version).

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
