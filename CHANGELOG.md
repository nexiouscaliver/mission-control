# Changelog

All notable changes to Mission Control will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-09-15

First release.

### Added
- **The five modes** — `plan` (triage + mandatory recon + wave/lane design with file ownership and enablement ordering), `prompts` (forge + red-team, paste-ready, RAM-derived caps), `verify` (evidence-based re-checks of returning sessions; on-the-spot remediation prompts), `next` (unblocked work, human actions owed, enablement-sequence enforcement), `close` (distill knowledge into the memory vault by project, completion record, approval-first deletion of working docs).
- **Vault-native state model** — one program note per active program, per-project config-facts notes replacing any config file, zero files written into working repos.
- **The forge grammar** (`references/prompt-anatomy.md`) — the invariant prompt skeleton, the situational module library (Part 1/Part 2, deploy window, dogfood/canary, abort path, release prep, remediation, one-shot), the red-team gate, and goal statements.
- **Sixteen distilled lessons** (`references/lessons.md`) — the production failure modes behind every rule, proven on a 5-wave, ~15-session program with two kill-switch aborts that worked.
- **Marketplace packaging** — Claude Code / ZCode installable and updatable; single-plugin marketplace manifest mirroring the omniforge-plugin layout.
