# MC Wall

## 1. What the Wall is

A deterministic, local status wall — there is no LLM behind the UI. The tower
(`mc_wall/tower`) joins state from read-only sources — the vault program notes
(lane rows), the ZCode session database, the regenloop goal directories under
each repo (including their forge manifests), and git/glab MR signals — into
one JSON document. The server (`mc_wall/server`) serves that document plus a
static page at
`http://127.0.0.1:8765/<token>/`, where `<token>` is a secret generated at
install time.

### Session-store adapter (extension point)

The tower reaches the harness session database through ONE seam:
`mc_wall/tower/session_store.py` (config: `TowerConfig.store`, default
`"zcode"`, plus the db path). The boot accepts a `"store"` key in wall.json
and fails loudly on an unknown name; the sole registered adapter today is
`zcode_db` (read-only sqlite over `~/.zcode/cli/db/db.sqlite`). A **Claude
Code adapter is an extension point — NOT IMPLEMENTED**: it would be a new
registry entry plus a module implementing the zcode_db surface
(`open_db_ro`, `check_schema`, `probe_factor`, `cutoff_stored`,
`to_seconds`, `scan_tag_products`, `products_to_tags`,
`products_to_bindings`, `scan_title_bindings`, `scan_tags`,
`session_rows`, `lane_join`, `unmapped_rows`, `check_drift`,
`DEGRADED_*`), with its own canonical default db path. No
such module exists in this repo; nothing under `mc_wall/` names a Claude
Code path.

## 2. Install

The Wall lives in this repo (`plugins/mission-control/wall/`) and always runs
FROM THE CLONE — `bin/mc-wall` resolves the clone root as the parent of its
own location and bakes it into run.sh's `PYTHONPATH` and the LaunchAgent.
Its data home is harness-neutral: `~/.mc-wall` (`MC_WALL_HOME` overrides),
created on first install. From the canonical checkout:

    /Users/shahil/work/regenai-repo/mission-control/plugins/mission-control/wall/bin/mc-wall install

One run writes everything, then starts the agent:

- `~/.mc-wall/wall.json` — token + port 8765, chmod 600. The token is
  preserved across reinstalls (a silent rotation would break the pinned URL);
  `--regenerate-token` forces a new one.
- `~/.mc-wall/state/` and `~/.mc-wall/logs/` directories.
- `~/.mc-wall/run.sh` (chmod 700) — pins
  `PATH=/opt/homebrew/bin:/usr/bin:/bin`, pins `PYTHONPATH` to this checkout,
  and execs `python3.14 -m mc_wall.server`.
- `~/Library/LaunchAgents/ai.zcode.mc-wall.plist` — label `ai.zcode.mc-wall`,
  KeepAlive Crashed-only, RunAtLoad, launchd stdout/stderr under
  `~/.mc-wall/logs/`.
- The mc-status skill, copied to `~/.zcode/skills/mc-status/` — redeployed on
  every install; a failed deploy prints one line and install continues.

**Moving or re-cloning the repo → re-run `mc-wall install`**: run.sh and the
plist bake absolute paths to the clone, so a new clone location needs one
install to re-pin them (the token in `~/.mc-wall/wall.json` survives).

Flags: `--dry-run` (prints the rendered plist and run.sh, writes nothing),
`--regenerate-token`.

## 3. Open / stop / status / log / restart

| Subcommand | What it does |
|------------|--------------|
| `install`  | Write wall.json / run.sh / plist / skill (above) and start the agent |
| `start`    | `launchctl bootstrap` the LaunchAgent (kickstart fallback) |
| `stop`     | `launchctl bootout`; "not loaded" is tolerated, rc 0 |
| `restart`  | stop, then start |
| `status`   | `launchctl print` + HTTP GET `/<token>/state`; rc 0 iff both ok (2xx) |
| `log`      | Tail `~/.mc-wall/logs/wall.log`; `-n N` (default 50) |
| `open`     | Chrome app-mode window on the wall URL (dedicated chrome-profile); plain `open` fallback |

`status` with no usable wall.json prints `no wall.json — run mc-wall install
first` and exits 1.

Uninstall is manual (no subcommand):

    /Users/shahil/work/regenai-repo/mission-control/plugins/mission-control/wall/bin/mc-wall stop
    rm ~/Library/LaunchAgents/ai.zcode.mc-wall.plist
    # optionally remove the generated artifacts (the checkout lives in the
    # same directory — remove per file, never the directory itself):
    rm ~/.mc-wall/wall.json ~/.mc-wall/run.sh
    rm -rf ~/.mc-wall/state ~/.mc-wall/logs ~/.mc-wall/chrome-profile
    rm -rf ~/.zcode/skills/mc-status

## 4. Security model

- **Token in the URL path.** Compared constant-time (`secrets.compare_digest`
  as UTF-8 bytes); any mismatch is a plain 404 page. The token is never
  logged: request logging is a no-op and a redaction filter scrubs any
  occurrence from log records. The token exists only in `wall.json`
  (chmod 600) — never in the plist, run.sh, or logs.
- **Host allowlist.** `Host` must be `127.0.0.1` or `localhost` — anything
  else is a 403 before any token-shaped input is touched (DNS-rebinding
  defense). A boot self-test probes both directions and refuses to go ready
  if either fails.
- **No CORS, ever.** No `Access-Control-*` header is emitted on any route.
- **Loopback only.** The server binds `127.0.0.1` only.
- **Read-only session db.** Both the tower and the matcher open the ZCode
  session db strictly through a sqlite `file:...?mode=ro` URI; any db failure
  degrades the display, never a write and never a crash.

## 5. How state flows

Vault note lane rows + goal-dir artifacts + regenloop goal dirs + the zcode
sqlite db → tower `collect_state` → one JSON document (`schema_version` 2 —
wall-overhaul contract v2: lane `deps`/`verified`/`verify_due`, program
`parse_defects`, document `needs_me`, fail-visible note parsing; shape pinned
by the frozen contract in `mc_wall/tower/contract.py`) → server
`GET /<token>/state` (the collector runs on every request, no caching) → the
page polls every 5 s and reloads itself when `schema_version` changes
(flap-safe: checked against the last applied version only). The served
document is the frozen tower doc plus one server-added key, `wall`, carrying
  the pending-launch record (`{"pending": null}` when idle) — so the page's
  root shape is contract.py's shape plus `wall`.

Lane autonomy: a lane whose row declares no `sess_` token binds to a session
on its own — precedence `row-token > pasted-tag > title-tag` (a pasted
`Session title: [<tag> <W-L>]` line beats the session's title, which only
carries the bracket when the goal block rode `sendGoalCommand` past the
`sendText` scan). Contention for one lane resolves newest-wins with one
`tag bind degraded: …` line and the losers still visible in
`sessions_unmapped`; a controller-written `verify:ok` in the artifacts cell
clears the lane's `suggest_verify`/`verify_due` cue AND removes its
`verify_queue` row (contract v2, decision D15: cue and queue clear
together). A done/partial lane with no joined session renders
due-with-unknown-age (`finished_ago_s` null in the queue row).
Full reference: `docs/autonomy.md`.

Launch handshake:

1. `POST /launch` validates `{row_id, repo_root}`, persists a **prompt-armed**
   record (fsync'd before any side effect), copies the prompt to the
   clipboard, opens the repo deep link, promotes to **await-birth**.
2. **await-birth** — the monitor (2 s tick) scans `session_input` (read-only)
   for the lane tag after the click. Exactly one matching session in the
   launched repo → **goal-armed** (goal block copied + notification). Tag seen
   in sessions of other repos → flagged `conflict`; multiple in-repo matches
   → flagged `ambiguous`.
3. **goal-armed** — a duplicate prompt paste re-copies the goal; the `/goal`
   landing in the same session (a `session_target` row) confirms → **cleared**
   (`goal-confirmed`), freeing the single launch slot.

Crash recovery: the pending record is persisted fsync'd at
`~/.mc-wall/state/pending.json` (atomic write, chmod 600). A corrupt
file is quarantined (`pending.corrupt-<ts>`) and the server still boots; a
crash between persist and promote leaves prompt-armed, which boot recovery
promotes back to await-birth with one audit line.

### Boot-time program-note discovery

At boot the tower also scans the parent dirs of the declared `note_glob`s
(fallback: `~/work/memory-vault/shared/programs`) for undeclared
`mission-control-<slug>-program.md` notes (slug kebab-case lowercase) and
appends them — plus repos derived from their lane rows — after the declared
programs/repos. A candidate needs a recognized prompt-log table and an
objective line (exact grammar in the reference); rejected candidates and
repo-probe failures emit `discovery degraded:` lines into the wall's
degraded list. Declared wall.json config always wins (same-slug notes skip
silently). Registration is per-poll (contract v2): each collect cycle
re-checks `wall.json`'s mtime (config rebuilt on change) and re-runs the
discovery scan — new or deleted program notes appear on the next poll with
no restart, alongside note content and lane status. Opt out with
`MC_WALL_DISCOVERY` set to `0`, `off`, `no`, or
`false` (case-insensitive): declared-only boot, no discovery degraded
lines, and one `MC_WALL_DISCOVERY set — boot-time discovery disabled`
line in wall.log on the launchd boot path. Hermetic suite:
`MC_WALL_DISCOVERY=0 .venv/bin/pytest
-q` keeps the zero-program boot tests off the real vault on
vault-equipped machines. Only the wall server discovers — `mc_status`
builds `TowerConfig` from tower.json and the e2e helpers inject a
hardcoded one; neither surfaces discovered programs.
See `docs/discovery.md` for the full reference.

## 6. mc-status skill

Text-board twin of the wall, rendered by the real tower (read-only):

    cd /Users/shahil/work/regenai-repo/mission-control/plugins/mission-control/wall \
      && .venv/bin/python -m scripts.mc_status

Shows the generation timestamp and degraded lines, then per program: the
objective, the master session, the lanes table; then the verify queue, human
actions, unmapped sessions (capped at 15 + overflow count), and the pending
launch record. Flags: `--config PATH`, `--db PATH`.

Environment: `MC_WALL_DB` (default `~/.zcode/cli/db/db.sqlite`),
`MC_WALL_TOWER_CONFIG` (default `<MC_WALL_HOME>/tower.json`), `MC_WALL_HOME`
(default `~/.mc-wall`). tower.json shape:

    {"store": "zcode",
     "programs": [{"program","tag","note_glob","master_tag"}],
     "repos": [{"name","path","host"}],
     "pending_launch_path": null}

Degrade behavior (fail-open by design; the CLI never shows a traceback):

- Missing config → one `tower config not found:` hint line + the db-derived
  sections over an empty program list, exit 0.
- Unparseable / not-a-JSON-object config → exit 2 with one stderr line ONLY
  when the path came from `--config`; resolved via env or default it takes the
  same degrade-and-render path (`tower config unreadable:`), exit 0.
- Config that parses as a JSON object but whose program/repo rows have wrong
  keys → one `tower config invalid: <path>` line + the same degrade-and-render
  path (empty programs, db-derived sections still render), exit 0 — including
  for an explicit `--config`.
- Missing/unreadable db → the tower's own degraded entries, exit 0.

Output may contain session titles — treat it as operator-private.

## 7. Failure modes

- Missing/unreadable `wall.json` → one stdout line, rc 1 (server entry);
  `open`/`status` print `no wall.json — run mc-wall install first`.
- `/state` returns **503 degraded** (with the pending record and the exception
  type name) whenever the tower document cannot be built — never a guessed or
  partial document. (B2, the boot-time `_default_collect_state` TypeError,
  was fixed by F-1's built-once tower config. B3 — find_row reading a
  `"rows"` key no real tower doc produces, so every POST /launch 404ed —
  was fixed by wall-overhaul contract v2: find_row reads
  `programs[].lanes[]`, keeping the legacy `"rows"` bridge for stub/legacy
  producers.)
- Corrupt `pending.json` → quarantined, server boots normally.
- Agent crash → launchd KeepAlive (Crashed-only) restarts it; armed state is
  rebuilt from disk (prompt-armed promoted on boot).
- Matcher errors → one WARNING per failure + an empty result; the handshake
  retries on the next tick.
- git/glab unreachable → the affected signals go null with degraded entries
  and source-age badges; a stale last-good value is never silently served
  (successful values always carry the age of the actual source read).

### Liveness knobs (wall-deadlock wd1, 2026-10-07)

The 2026-10-07 incident class — "accepts TCP, never answers" — was the /state
handler running the full tower collect synchronously: on cold caches that
collect serializes dozens of subprocess spawns (measured ~17.5 s on an idle
machine), the browser polls every 5 s, and under load the handler-thread
pile-up meant no request ever completed. Every collect consumer now goes
through a single-flight gate with a hard deadline.

- `MC_WALL_STATE_DEADLINE_S` (default `10`) — per-request deadline for every
  route that reads state (`/state`, POST launch/re-copy/copy-goal/
  needs-me-now, and the handshake monitor's collect). On overrun the route
  returns **503 degraded** with `error: collect_state_deadline` and a
  `detail` naming the overran stage (the deepest `mc_wall` frame of the
  in-flight collect, e.g. `netcache.py:_run_cmd`) — never a hang. Concurrent
  requests coalesce onto the one in-flight collect; a fresh idle request
  always starts a fresh collect (the no-caching contract is unchanged).
- `MC_WALL_BOOT_DEADLINE_S` (default `60`) — after binding, the server
  completes one full collect under this hard deadline (also the cache warm).
  An overrun means the collect is wedged: the server logs `FATAL boot
  self-check`, dumps every thread's stack to stderr, and exits **5** so
  launchd restarts it — a wall that cannot answer never pretends to be up.
  A healthy-but-slow cold boot (~17-18 s) passes well inside the default.
- `MC_WALL_MONITOR=0` (or `off`/`no`/`false`) — kill switch: the handshake
  monitor thread is entirely absent (one log line); every route, /state
  included, still serves. Use it to rule the monitor out when debugging.
- Runner side effects (`pbcopy`, `open`, `osascript`) carry explicit 5 s
  timeouts — a wedged GUI service fails the one side effect (contained,
  retried per the existing tick rules) instead of hanging the monitor tick.
- A failing discovery `git remote -v` probe is held for a 300 s backoff
  before retrying (success-only caching made failed probes respawn every
  cycle — a livelock vector).

Exit codes: `0` signal shutdown, `1` port busy, `3` never ready, `4` serve
loop died unrequested, `5` boot self-check overran (wedge — threads dumped).

## 8. Repo layout + gates

    bin/mc-wall          control CLI (install/start/stop/restart/status/log/open)
    mc_wall/tower        state collector: notes, session db, goals, signals, derivations
    mc_wall/server       HTTP server, auth, pending store, matcher, handshake monitor
    web/                 the static page (index.html, app.js, style.css, selftest.mjs)
    templates/           plist, run.sh, prompt/goal blocks, notifications, wrong_token.html
    scripts/             mc_status board CLI + skill deploy helper
    skills/mc-status     the deployed skill source
    tests/               unit tests + tests/integration (both sides of the stack)
    docs/                design contract + QA screenshots

Design contract: `docs/design-signal-panel.md` is the design contract of
record for the Signal Panel reskin — the frozen token set, the status
grammar (7px dot + outline badge; the old provenance chip cipher is
replaced), the severity map, buttons, and layout rules. Its executable
form is `node web/selftest.mjs` (104 checks).

Gates (`regenloop/gates.toml`): `python-test` (`.venv/bin/pytest -q`),
`web-selftest` (`node web/selftest.mjs`), `bin-py-compile`
(`py_compile bin/mc-wall`), `templates-check` (`bash -n
templates/run_sh.template`). The command surface is the table in §3.
