# regenloop knowledge package

This directory is the team's shared regenloop home — committed and version-controlled.

- `knowledge/` — always-loaded INDEX and authored "why" cards for each module
- `gates.toml` — gate definitions (mirrors CI config)
- `hooks/` — hook templates for pre-commit / pre-push automation

Run `python3 regenloop_init.py init <repo_root>` to refresh the knowledge package.
Derived files (`derived/`) and per-user run state (`local/`) are gitignored.
