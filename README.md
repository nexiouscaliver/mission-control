# Mission Control

**The master session for multi-session agent programs.** Mission Control plans regenloop programs into waves and lanes, forges red-teamed execution prompts you paste into fresh sessions, verifies what those sessions actually did (against reality, never their self-reports), and distills the program's knowledge into your memory vault before deleting its working docs.

It never implements anything itself. It never merges, releases, or deploys — those stay human, by design.

```
INTAKE → TRIAGE (deep vs one-shot) → RECON → PLAN (waves/lanes)
       → FORGE (red-teamed prompts) → EXECUTE (you paste & run sessions)
       → VERIFY (evidence, not claims) → ADAPT (next wave / remediation)
       → CLOSE (distill to vault → delete working docs)
```

## The five modes

| Command | What it does |
|---|---|
| `/mission-control-plan <objective>` | Triages, runs mandatory recon (true bases, active goals, open MRs, live knobs, RAM headroom), designs waves/lanes with file ownership, asks only genuine forks, writes the program note to the vault |
| `/mission-control-prompts [wave]` | Forges the wave's prompts (full anatomy, fresh-verified SHAs/flags/paths, RAM-derived caps), **red-teams each before emitting**, prints paste-ready blocks + launch steps |
| `/mission-control-verify <session/MR>` | Re-checks reality — merge ancestry, API state, suite re-runs, box knobs/journal, artifact re-reads, stray-branch debris — then forges a remediation prompt on the spot if gaps exist. Accepts a bare MR/PR number (`!1560`, `#42`) |
| `/mission-control-next` | Human actions owed, lanes free, waves unblocked; enforces build-parallel/enable-serial |
| `/mission-control-close <program>` | Distills decisions/config-facts/debug-wins/follow-ups into the vault **by project**, writes a completion record, deletes the working docs (approval-first sweep) |

All five also work through the base skill: `/mission-control <mode> …`.

## Design principles (the short version)

- **Grounding over templates** — every SHA, MR number, and knob name in a forged prompt is verified in the same turn it is written.
- **Evidence over claims** — handoff capsules are claims; verification re-runs, re-reads, and re-checks.
- **Vault-native state, zero repo files** — one program note while active; distilled knowledge by project at close; nothing ever leaks into a working repo's tree.
- **Human-only gates stay human** — merges, releases, tags, deploys. The controller prepares everything around the click; the environment's guards enforce the rest.
- **Fail-loud everything** — abort paths and kill switches are forged into every production-adjacent prompt; two real production bugs were caught cheaply exactly this way.
- **Parallelism is a proposal** — caps derive from measured headroom; the operator approves and launches.

The full craft lives in the skill's references: `prompt-anatomy.md` (the forge grammar + module library) and `lessons.md` (the sixteen failure modes behind the rules).

## Requirements

- **Claude Code** (or a Claude Code-compatible harness such as ZCode) with the **regenloop plugin** installed — Mission Control forges `/regenloop-run --deep --safe --ship` prompts and shares regenloop's disciplines (TDD receipts, gates, ship protocol, the never-merge guard).
- **A Basic Memory vault reachable via the `shared-memory` MCP tools** — program state, per-project config-facts, and distilled knowledge all live there. (Without it, Mission Control warns and falls back to in-turn recon only.)

## Install

**Claude Code:**
```
/plugin marketplace add nexiouscaliver/mission-control
/plugin install mission-control@mission-control
```

**ZCode (local-dir marketplace):** clone the repo, then add it as a local marketplace pointing at the clone and install the plugin, or place a synced copy under `~/.zcode/cli/plugins/marketplaces/` — same mechanics as any local-dir marketplace.

Updates: `/plugin marketplace update mission-control` (or `git pull` the local clone), then reinstall/refresh the plugin. Every release is a lightweight tag + GitHub Release; `plugins/mission-control/.claude-plugin/plugin.json` carries the version.

## Shakedown (first use)

Give it one small real objective, run `plan → prompts`, paste one prompt into a fresh session, then `verify` — and tune against wherever it drifts before trusting it with a full program.

## Maintenance

Versioning follows Keep-a-Changelog: bump `plugins/mission-control/.claude-plugin/plugin.json`, add a `CHANGELOG.md` entry, tag `vX.Y.Z`, publish the release with `--verify-tag --latest`. The marketplace manifest (`marketplace.json`, repo root) lists the single plugin — mirror the omniforge-plugin layout for anything more.

## License

MIT — see [LICENSE](LICENSE).
