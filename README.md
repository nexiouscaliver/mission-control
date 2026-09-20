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

## 2. Install

From the canonical checkout:

    /Users/shahil/.zcode/mc-wall/bin/mc-wall install

One run writes everything, then starts the agent:

- `~/.zcode/mc-wall/wall.json` — token + port 8765, chmod 600. The token is
  preserved across reinstalls (a silent rotation would break the pinned URL);
  `--regenerate-token` forces a new one.
- `~/.zcode/mc-wall/state/` and `~/.zcode/mc-wall/logs/` directories.
- `~/.zcode/mc-wall/run.sh` (chmod 700) — pins
  `PATH=/opt/homebrew/bin:/usr/bin:/bin`, pins `PYTHONPATH` to this checkout,
  and execs `python3.14 -m mc_wall.server`.
- `~/Library/LaunchAgents/ai.zcode.mc-wall.plist` — label `ai.zcode.mc-wall`,
  KeepAlive Crashed-only, RunAtLoad, launchd stdout/stderr under
  `~/.zcode/mc-wall/logs/`.
- The mc-status skill, copied to `~/.zcode/skills/mc-status/` — redeployed on
  every install; a failed deploy prints one line and install continues.

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
| `log`      | Tail `~/.zcode/mc-wall/logs/wall.log`; `-n N` (default 50) |
| `open`     | Chrome app-mode window on the wall URL (dedicated chrome-profile); plain `open` fallback |

`status` with no usable wall.json prints `no wall.json — run mc-wall install
first` and exits 1.

Uninstall is manual (no subcommand):

    /Users/shahil/.zcode/mc-wall/bin/mc-wall stop
    rm ~/Library/LaunchAgents/ai.zcode.mc-wall.plist
    # optionally remove the generated artifacts (the checkout lives in the
    # same directory — remove per file, never the directory itself):
    rm ~/.zcode/mc-wall/wall.json ~/.zcode/mc-wall/run.sh
    rm -rf ~/.zcode/mc-wall/state ~/.zcode/mc-wall/logs ~/.zcode/mc-wall/chrome-profile
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
sqlite db → tower `collect_state` → one JSON document (`schema_version` 1,
shape pinned by the frozen contract in `mc_wall/tower/contract.py`) → server
`GET /<token>/state` (the collector runs on every request, no caching) → the
page polls every 5 s and reloads itself when `schema_version` changes
(flap-safe: checked against the last applied version only). The served
document is the frozen tower doc plus one server-added key, `wall`, carrying
the pending-launch record (`{"pending": null}` when idle) — so the page's
root shape is contract.py's shape plus `wall`.

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
`~/.zcode/mc-wall/state/pending.json` (atomic write, chmod 600). A corrupt
file is quarantined (`pending.corrupt-<ts>`) and the server still boots; a
crash between persist and promote leaves prompt-armed, which boot recovery
promotes back to await-birth with one audit line.

## 6. mc-status skill

Text-board twin of the wall, rendered by the real tower (read-only):

    cd /Users/shahil/.zcode/mc-wall && .venv/bin/python -m scripts.mc_status

Shows the generation timestamp and degraded lines, then per program: the
objective, the master session, the lanes table; then the verify queue, human
actions, unmapped sessions (capped at 15 + overflow count), and the pending
launch record. Flags: `--config PATH`, `--db PATH`.

Environment: `MC_WALL_DB` (default `~/.zcode/cli/db/db.sqlite`),
`MC_WALL_TOWER_CONFIG` (default `<MC_WALL_HOME>/tower.json`), `MC_WALL_HOME`
(default `~/.zcode/mc-wall`). tower.json shape:

    {"programs": [{"program","tag","note_glob","master_tag"}],
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
  partial document. Two KNOWN production gaps are pending operator approval:

```
B2: mc_wall/server/app.py:63-66 — _default_collect_state() calls
collect_state() with no TowerConfig -> TypeError -> /state 503 in
production today.
```

```
B3: state_contract.find_row reads a "rows" key no real tower doc produces ->
every POST /launch 404s unknown-row in production today.
```

  Until both are fixed the page shows its degraded banner — that is the
  honest current state, not a bug in this README.
- Corrupt `pending.json` → quarantined, server boots normally.
- Agent crash → launchd KeepAlive (Crashed-only) restarts it; armed state is
  rebuilt from disk (prompt-armed promoted on boot).
- Matcher errors → one WARNING per failure + an empty result; the handshake
  retries on the next tick.
- git/glab unreachable → the affected signals go null with degraded entries
  and source-age badges; a stale last-good value is never silently served
  (successful values always carry the age of the actual source read).

## 8. Repo layout + gates

    bin/mc-wall          control CLI (install/start/stop/restart/status/log/open)
    mc_wall/tower        state collector: notes, session db, goals, signals, derivations
    mc_wall/server       HTTP server, auth, pending store, matcher, handshake monitor
    web/                 the static page (index.html, app.js, style.css, selftest.mjs)
    templates/           plist, run.sh, prompt/goal blocks, notifications, wrong_token.html
    scripts/             mc_status board CLI + skill deploy helper
    skills/mc-status     the deployed skill source
    tests/               unit tests + tests/integration (both sides of the stack)

Gates (`regenloop/gates.toml`): `python-test` (`.venv/bin/pytest -q`),
`web-selftest` (`node web/selftest.mjs`), `bin-py-compile`
(`py_compile bin/mc-wall`), `templates-check` (`bash -n
templates/run_sh.template`). The command surface is the table in §3.
