# Changelog

All notable changes to Mission Control will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
