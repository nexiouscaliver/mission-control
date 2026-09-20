---
name: mc-status
description: Print the MC Wall program/lanes/session board as text (read-only tower render).
---

# mc-status

Prints the MC Wall board as text by running the real tower
(`mc_wall.tower.collect_state`) over the live session db and the vault
program notes. The board shows:

- the generation timestamp and any degraded-source lines (db, notes,
  goals, network, launch state);
- per program: objective, the master session (id, title, last-active age),
  and the lanes table (row id, parsed status, branch, slug, joined session
  id, last-active);
- the verify queue (lanes finished but not yet verified), human actions
  (open merge requests awaiting a decision), sessions unmapped (recent
  sessions no lane claimed), and the pending-launch record.

## Command

Run from the canonical checkout root:

    cd /Users/shahil/work/regenai-repo/mission-control/plugins/mission-control/wall && .venv/bin/python -m scripts.mc_status

Optional flags: `--config PATH` (tower.json) and `--db PATH` (session db).

## Environment overrides

- `MC_WALL_DB` — session db path (default
  `~/.zcode/cli/db/db.sqlite`).
- `MC_WALL_TOWER_CONFIG` — tower config path (default
  `<MC_WALL_HOME>/tower.json`).
- `MC_WALL_HOME` — wall home directory (default `~/.mc-wall`); the
  default tower config resolves under it.

## Notes

- The command is strictly read-only: it renders state, it never writes the
  db, the notes, or the wall.
- Output may contain session titles — treat it as operator-private and do
  not paste it into shared channels.
- Fail-open by design: a missing config renders `(none configured)` for
  programs, a missing db renders the tower's degraded entries; the only
  nonzero exit is 2, for a `--config` file that cannot be parsed.
- See README §mc-status for the full reference.
