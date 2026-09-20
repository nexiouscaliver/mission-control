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
| `/mission-control-prompts [wave]` | Forges the wave's prompts (full anatomy, fresh-verified SHAs/flags/paths, caps via --safe or pinned env exports), **red-teams each before emitting**, prints paste-ready blocks + launch steps |
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

- **Claude Code** (or a Claude Code-compatible harness such as ZCode) with the **regenloop plugin** installed — Mission Control requires the **regenloop plugin** (hard dependency, declared in the skill's load surface). It forges `/regenloop-run` prompts per the lane policy in `references/regenloop-interface.md` — pointers into the installed regenloop's own docs and knowledge cards, re-derived at every plan (the plugin wins on any disagreement), plus mission-control's controller-side rules. The never-merge guard is regenloop's; mission-control's own guarantee is never-implements.
- **A Basic Memory vault reachable via the `shared-memory` MCP tools** — program state, per-project config-facts, and distilled knowledge all live there. (Without it, Mission Control warns and falls back to in-turn recon only.)

## Install

**Claude Code:**
```
/plugin marketplace add nexiouscaliver/mission-control
/plugin install mission-control@mission-control
```

**ZCode (local-dir marketplace):** clone the repo, then add it as a local marketplace pointing at the clone and install the plugin, or place a synced copy under `~/.zcode/cli/plugins/marketplaces/` — same mechanics as any local-dir marketplace.

Updates: `/plugin marketplace update mission-control` (or `git pull` the local clone), then reinstall/refresh the plugin. Note there are **two** installed surfaces: the marketplace synced copy (`~/.zcode/cli/plugins/marketplaces/mission-control/`) and the versioned plugin cache (`~/.zcode/cli/plugins/cache/mission-control/mission-control/<version>/`) — a refresh that touches only one leaves stale registrations behind (an early program opened on a deleted 1.1.0 cache path). Every release is a lightweight tag + GitHub Release gated by `scripts/verify_packaging.sh` + `tests/run_smoke.sh`; `plugins/mission-control/.claude-plugin/plugin.json` and the SKILL frontmatter carry the version.

## The Wall (in-repo, clone-and-run)

The MC Wall — the deterministic local status board the controller's programs project onto — lives in this repo at `plugins/mission-control/wall/` (imported with its full git history). It always **runs from the clone**, never from the installed plugin cache:

```
git clone <this repo> && cd mission-control/plugins/mission-control/wall
bin/mc-wall install        # writes ~/.mc-wall (harness-neutral home) + LaunchAgent, starts the server
bin/mc-wall open           # opens the Wall in an app-mode Chrome (pinned-token URL)
```

`install` resolves the clone root as the parent of its own location and bakes it into the agent's run.sh — so moving or re-cloning the repo means re-running `mc-wall install` (the token in `~/.mc-wall/wall.json` survives). The marketplace plugin copy ships these files unchanged; only the clone can run them. Full reference: the wall's own [`README.md`](plugins/mission-control/wall/README.md) (install, security model, state flow, failure modes, the ZCode session-store adapter with its documented Claude Code extension point — NOT IMPLEMENTED).

## Shakedown (first use)

Give it one small real objective, run `plan → prompts`, paste one prompt into a fresh session, then `verify` — and tune against wherever it drifts before trusting it with a full program.

Provenance note (honesty over marketing): v1.0.0–v1.2.0 shipped before their first real use — and that first use was a full three-wave multi-repo program (2026-09-15 → 09-17), not the small shakedown above. Its findings are folded into v1.3.0. Take the advice the house didn't.

## Maintenance

Versioning follows Keep-a-Changelog: bump `plugins/mission-control/.claude-plugin/plugin.json`, add a `CHANGELOG.md` entry, tag `vX.Y.Z`, publish the release with `--verify-tag --latest`. The marketplace manifest (`marketplace.json`, repo root) lists the single plugin. For anything more, add plugins under `plugins/<name>/` — each with its own `.claude-plugin/plugin.json` — and register them in `marketplace.json`. Run `bash scripts/verify_packaging.sh` before every tag: it checks manifests, frontmatter, reference existence, and version identity (plugin.json == tag == CHANGELOG head == SKILL frontmatter).

Loading policy: references load per-mode; the interface doc is load-on-act (at plan and before forging/verifying); `SKILL.md` is the only always-loaded surface.

## License

MIT — see [LICENSE](LICENSE).
