# HANDOFF — Parent-session linkage for wall sessions (true lineage)

Handoff written 2026-09-22 from session sess_e1fe54d8 (rounds 1–4 of the wall
UI overhaul are done and pushed on `feat/wall-ui-overhaul`, PR #4, unmerged).
This brief is self-contained: a fresh session should be able to execute it
without reading this conversation.

## Mission (operator terms)

Today the wall hides workflow subagents and side chats behind a collapsed
section and clusters subagents by their **workflow run id** — the only lineage
the state contract carried. The user wants the real thing: **every workflow
actor group and every side chat linked to the conversation that spawned it.**

Grounded discovery (verified by this session, read-only, against the live
`~/.zcode/cli/db/db.sqlite`): **the data already exists.**

- `session` table has a `parent_id` column, POPULATED for both kinds:
  - `sess_dwf-dwfrun-bd975e25-…-actor_1_1` → `parent_id = sess_84e1a49c-…`
  - `Selection side chat` rows → `parent_id = sess_f0d00172-…` / `sess_3221deb8-…`
- `dwf_run` table has `parent_session_id` (redundant second source for actors).
- So this is a **read → emit → thread → render** job. No ZCode-side changes,
  no schema migration (the column already exists in the product db).

## Current state of the wall (what round 4 shipped)

Web client: `plugins/mission-control/wall/web/` (canonical) — `app.js`,
`style.css`, `index.html`, `selftest.mjs`; byte-identical copies in
`web/assets/` (AC-32 — `cp` after every edit; selftest enforces).

- `sessionKind(id, title)` → `"workflow" | "sidechat" | "main"`;
  `workflowRunKey(id)` extracts the run id from
  `sess_dwf-dwfrun-<run>-actor_N_M`; `splitBackgroundRows(rows)` partitions.
- Unmapped strip: head reads `unmapped (N · M hidden)`; collapsed
  `.unmapped-hidden` section (state var `backgroundOpen`, survives polls)
  reveals run clusters (`workflow run <first8> · count`, full run id on hover)
  + a `side chats · N` group. State var `showBackground` +
  `background: hidden/shown` toggle does the same for the PROJECTS view.
- These stay. This feature **upgrades the labels and nesting inside the
  hidden section** and tags mapped rows; it does not re-architect it.

Server: `plugins/mission-control/wall/mc_wall/tower/` builds the state doc.
- `zcode_db.py` — the ONLY file that talks to the zcode sqlite db
  (`~/.zcode/cli/db/db.sqlite`, opened READ-ONLY; `session_rows()` selects
  `id, title, directory, time_updated` by id; `unmapped_rows()` selects
  `id, title, directory, time_updated` over a time window; `check_schema()`
  reports schema drift; `open_db_ro` / `probe_factor` handle the s↔ms factor).
- `collect.py` — `_join_sessions` / `_read_sessions` assemble
  `lanes[].session` and `sessions_unmapped` from those rows.
- `contract.py` — state-doc shape helpers (read it before changing shapes).
- The state doc reaches the browser via `GET /<token>/state`; the server also
  serves `web/` directly from the repo working tree (no restart needed).

Id formats: interactive chats `sess_<uuid>`; workflow subagents
`sess_dwf-dwfrun-<run-uuid>-actor_<n>_<m>`.

## Work plan (suggested slice order)

### 1. Server: emit `parent_session_id` (nullable)
- `zcode_db.session_rows()` and `unmapped_rows()`: add `parent_id` to the
  SELECT. Guarded: if an older db lacks the column, degrade to NULL rather
  than degrading the whole store (follow the existing drift/check_schema
  pattern — read `check_schema` first).
- Thread through `collect.py` into both `lanes[].session` and
  `sessions_unmapped` rows as `parent_session_id` (null when absent).
- Tolerance ladder: an absent/wrong-typed value reads as null (L4 pattern —
  see `nullable()` usage in `collect.py` / `web/app.js`).
- Server tests live in `tests/` (pytest). Add: a row with a parent emits the
  id; a row without emits null. `tests/e2e/mock_serve.py` +
  `seed_pending.py` are the browser rig (serve any embedded QA fixture over
  HTTP on a temp home + port).

### 2. Contract: KEYSETS + fixtures
- `web/app.js` KEYSETS: `session` and `unmappedRow` gain
  `"parent_session_id"`. `selftest.mjs` mirrors them in its `K` map
  (`assertKeySet` walks every fixture row — every fixture row must carry the
  new key or the conformance test fails).
- `web/index.html` fixtures: add `parent_session_id` to session/unmapped rows.
  Exercise BOTH paths: a parent that IS in the doc (e.g. an unmapped row whose
  parent is a mapped `s-…` session) and a dangling parent (parent not present
  — happens live when the parent is older than the session window).
- Emit only the id, NOT parent_title: the parent may be outside the doc
  window; the wall resolves the title client-side when it can.

### 3. UI: nest + annotate
- Hidden section (wall): within a run cluster, sub-group actors by parent.
  Parent label = the parent's title **if the parent session is present in the
  doc** (search lanes' sessions, masters, unmapped), else the short parent id
  (`first 13 chars…`, full id on hover). Proposed cluster head:
  `workflow run bd975e25 · 10 → <parent name>`; side chat rows get a
  `↳ <parent name>` suffix line or tag.
- PROJECTS view: background rows, when shown, keep their kind tag and gain
  the parent as hover/title on the row (`parent: <name/id>`); no structural
  change needed there.
- Reading state: the reveal (`backgroundOpen`) and scroll preservation
  (`captureCol3ViewState`/`restoreCol3ViewState`) already survive poll
  rebuilds — keep that property; any new collapsed-by-parent state needs the
  same treatment (module var, not node state).

### 4. Design decision to confirm with the user BEFORE implementing UI
(remediation lesson: approval gate before UI work)
- Option A (recommended): nest per parent inside each run cluster (above).
- Option B: annotate-only — keep run clustering, add `↳ parent` text to rows.
  Cheaper, less readable with 10 actors of one parent.
Ask with two tiny static-HTML mockups; implement the chosen one.

## Acceptance criteria (all falsifiable)

1. Spot-check vs sqlite: for every dwf-actor row in `GET /<token>/state`,
   `parent_session_id` equals the db's `parent_id` (script the check).
2. Wall hidden section: actors of one run sub-group under the parent's title
   when the parent is in the doc; under a short-id hover-full label when not.
3. Side chat rows show their parent (title or short id).
4. A parent that is not in the doc degrades to the short id — never a crash,
   never a raw id dump in the default view (hover carries the full id).
5. KEYSETS/AC-31 conformance passes (both KEYSETS maps + all fixtures updated).
6. Gates: `.venv/bin/pytest -q` green; `node web/selftest.mjs` green with NEW
   tests added (server emit, tolerance, UI nesting/persistence per the
   round-5/6 patterns); assets byte-identical (AC-32).
7. No regression of shipped contracts: background rows stay hidden by default;
   reveal + scroll + expanded-idle state survive poll rebuilds; severity lane
   order intact.

## Guardrails & gotchas (learned the hard way — obey)

- Fake DOM (selftest) is a SUBSET: no `querySelector`/`innerHTML`; some nodes
  lack `getAttribute` — read via `.attrs[...]` guarded, exactly like `sigOf`.
- Any CSS cap on a toggleable container must be scoped `:not(.open)` or the
  toggle silently dies (specificity).
- Poll churn guard rebuilds columns when text changes — operator state
  (scroll, expanded) must live in module vars, captured/restored around swaps.
- `web/selftest.mjs` pins exact strings (AC-4/AC-22 banner text, `unmapped
  (2)` head, chip borders) — change copy together with its pin, deliberately.
- Offline-clean: no external URLs in web files; QA mounts via file:// mocks,
  browser verification via `tests/e2e/mock_serve.py --case <name> --home
  <tmp> --port <p>` (HTTP), armed states via `seed_pending.py` BEFORE boot.
- The live wall at `127.0.0.1:8765` (token in `~/.mc-wall/wall.json`) serves
  the WORKING TREE of this repo — it reflects whatever branch is checked out.
- Commits: plain `git commit -m`, default identity, no AI attribution.
  Push only when the user asks (standing AGENTS.md rule; rounds 2–4 of PR #4
  were individually authorized).
- Screenshots for evidence go in `shots/` and are committed on the branch;
  PR #4 body leads with newest round section (`gh pr edit 4 --body-file`).

## Out of scope (do not build here)

- Grandparent chains / lineage trees (parent_id is one hop).
- Persisting view preferences across reloads (localStorage).
- Making the wall server write anything to the zcode db (it is read-only).
