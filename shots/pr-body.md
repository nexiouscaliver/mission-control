# Wall UI overhaul — severity-first chips, guided empty states, a launch dialog worth opening

This PR overhauls the mc-wall web client (`plugins/mission-control/wall/web/`) end to end. Every change below traces to a finding from the UI audit: the wall was a grid of hollow boxes with developer diagnostics as copy, a color grammar that told the opposite story of the data, and flagship interactions that were either invisible (the launchable lane) or silently dead (NEEDS ME NOW at zero owed). All screenshots live in-repo under [`docs/screenshots/`](plugins/mission-control/wall/docs/screenshots) so this description stays valid even as the branch moves.

## What changed, and why

**1. Chip grammar v2 — color now encodes severity, not provenance.**
Previously every parsed status — including `failed` — rendered as a healthy green chip, while a benign `UNPARSED: Waiting on CI!!` note rendered as alarm-red hatching. The palette told the opposite story of the data. An additive severity axis now rides on top of the provenance classes (`statusSeverity()` in `web/app.js`): `failed` → blocked red, `partial` / `UNPARSED` / a stalled lane / an unstamped note → watch amber, everything stamped-and-healthy → ok green. A stale-provenance chip that is watched or blocked re-hatches in its severity hue, and severity tokens are contrast-checked against all three chip backgrounds (selftest `sev-contrast`, 4.5:1).

**2. NEEDS ME NOW is legible and never dead.**
The cryptic `0v·0m` counter became `0 verify · 0 merge`, and a zero-owed click no longer does literally nothing: the button goes idle-styled (`aria-disabled`, still focusable) and surfaces a transient **"nothing owed"** note, backed by a persistent `nothing owed — updates every 5 s` hint beside it. The audit's flagship-CTA complaint — disabled at zero with only a hover-only tooltip — is gone (screens 03).

**3. The armed cycle moves out of the top bar into its own strip.**
The top bar was a chip landfill: wordmark, mode badge, CTA, a ~590px armed chip with four controls, two degraded badges and a `state 21h` caption all in one ungrouped row. It is now three labeled clusters — identity (wordmark · live dot · mode badge), owed (CTA + hint), health (degraded badges + state age) — and the armed indicator gets a dedicated full-width strip directly beneath the bar, so its re-copy / cancel / × controls are always reachable, including while the launch panel is open (the strip docks beside the panel via `body.panel-open` and lifts above the backdrop). Dismissing it collapses to zero height with no blank band (screens 10b).

**4. Every empty state teaches.**
`no programs`, `nothing to verify`, `nothing owed`, `no lanes`, `no sessions` each gain a companion hint line that says what would fill the panel ("Programs appear here once the tower registers one — this page updates itself every 5 s.", "When a lane finishes, its COPY VERIFY command lands here.", "Merge requests waiting on you will appear here.", …). Error paths get recovery guidance too: the bad-state banner now carries "Panels stay blank until a valid state document arrives — nothing on this page is rendered from invalid data." instead of dead-ending on internal classifier text, and the missing-state banner points at `mc-wall open` (screens 01, 13, 14, 19).

**5. The unmapped strip is explorable, and copying confirms.**
The 240px internal scroller with no affordance — 16 of 19 rows invisible — is replaced by a `show all ▾` / `show less ▴` toggle (`aria-expanded`, rows stay in the DOM; the expanded state survives poll rebuilds because it lives on the strip, not the discarded nodes). Clicking a row to copy its session id now confirms with an in-row **"id copied"** transient note instead of a silent clipboard write, and the note lands inside the clicked row so it can't fall below the scroller fold (screens 01, 01c, 02, 11).

**6. The launch journey is visible.**
The clickable forged lane's only affordance was a border hue shift. Launchable lanes now carry a persistent at-rest treatment plus a `▸` chevron that elevates and turns cyan on hover **and** on keyboard focus — the row reads as openable before you touch it (screens 05 vs 06).

**7. The launch panel is a real dialog.**
The bare key:value dump that dead-ended on "prompt preview: not in v1 state contract" is restructured into grouped sections (LANE / MANIFEST) with a mirrored status chip in the lane's own colors, an `esc` hint, a × button, and a dimming backdrop that click-closes. Focus returns to the triggering lane on close. The panel now re-renders during the arm cycle, so its `pending:` line tracks the strip (`prompt-armed` → `goal-armed` → cleared) instead of contradicting it — and focus stays pinned on the fresh LAUNCH node (screens 07–10). LIVE keeps its honest lock: LAUNCH disabled with a `launch via master (v1)` note and a CSS 🔒.

**8. Keyboard shortcuts are discoverable.**
`n` / `r` / `esc` existed only in code. A footer legend now names them: **shortcuts: n needs-me-now · r refresh · esc close panel** (screen 20). Pressing `n` scrolls to and cyan-flashes the top owed item.

**9. Chrome explains itself.**
The meaningless `state 0s` / `state age unknown` caption gets a tooltip ("age of the last state document received from the wall server"), degraded badges are grouped as a `role="status"` region, and long UNPARSED notes clamp with ellipsis plus the full text in a `title` tooltip (screens 14, 16).

**10. Boot and error states stop dead-ending.**
Before the first poll the panels say `waiting for first state` under a stale live dot (screen 01b); the raw-JSON 404 for a wrong path is addressed at the server layer — see scope note below.

**Scope note:** this commit is intentionally client-side only (`wall/web/` + `wall/docs/`). The unknown-route redirect that `after/04` depicts (an unknown path under a valid token 302-ing back to the canonical wall page) is implemented in `mc_wall/server/app.py` and travels separately; the branded wrong-token page itself (`templates/wrong_token.html`) already exists on `main`.

## Before / After

> **Note:** the before and after sets were captured hours apart against the same live tower, so the live session data differs between them (e.g. 19 vs 25 unmapped sessions, different ages). Judge the **design** — layout, hierarchy, color grammar, affordances — not the data.

### 01 — Live wall, default
**Before**
![before 01](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/before/01-live-wall-default.png)
**After**
![after 01](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/01-live-wall-default.png)
*Hollow panels get companion hints; the CTA counter reads `0 verify · 0 merge` with a persistent "nothing owed" hint; the unmapped strip gets a `show all ▾` toggle and clearer rows; a shortcuts legend anchors the page.*

### 02 — Live wall, full page after "show all"
**Before**
![before 02](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/before/02-live-wall-fullpage.png)
**After**
![after 02](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/02-live-wall-fullpage.png)
*`show all` expands the unmapped strip in place — every row is in the document, no hidden internal scroller (page height 1000 → ~2100px).*

### 03 — NEEDS ME NOW clicked with nothing owed
**Before**
![before 03](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/before/03-live-needs-me-now-clicked.png)
**After**
![after 03](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/03-live-needs-me-now-clicked.png)
*The idle CTA stays clickable and answers the click with a "nothing owed" note instead of being a disabled dead button.*

### 04 — Bogus path / lost token
**Before**
![before 04](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/before/04-live-error-bogus-token.png)
**After**
![after 04](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/04-live-error-bogus-token.png)
*No more raw 404 JSON on a black page: an unknown route now lands back on the wall (server-side 302 — travels separately, see scope note).*

### 05 — QA full fixture, default
**Before**
![before 05](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/before/05-qa-full-default.png)
**After**
![after 05](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/05-qa-full-default.png)
*Severity-first grammar: `failed` reads blocked-red, `partial`/`UNPARSED` read watch-amber, healthy lanes read ok-green; the armed strip owns its own band; degraded badges are grouped right; the launchable lane shows its at-rest affordance.*

### 06 — Forged lane, hover/focus affordance
**Before**
![before 06](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/before/06-qa-forged-lane-hover.png)
**After**
![after 06](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/06-qa-forged-lane-hover.png)
*The launchable lane visibly responds: elevated outline, cyan-tinted chevron — via mouse hover and keyboard focus alike.*

### 07 — Launch panel open
**Before**
![before 07](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/before/07-qa-launch-panel-open.png)
**After**
![after 07](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/07-qa-launch-panel-open.png)
*A real dialog: grouped LANE / MANIFEST sections, the lane's status chip mirrored inside, `esc` + × closers, a dimming click-to-close backdrop, and the wall anchored behind it.*

### 08 — Arm cycle: prompt armed
**Before**
![before 08](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/before/08-qa-armed-prompt.png)
**After**
![after 08](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/08-qa-armed-prompt.png)
*Strip and panel agree: `prompt armed: [secfix W2-L7] — paste in ZCode` up top, `pending: prompt-armed` in the panel, LAUNCH focused.*

### 09 — Arm cycle: goal armed
**Before**
![before 09](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/before/09-qa-armed-goal.png)
**After**
![after 09](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/09-qa-armed-goal.png)
*The old contradiction is gone — the panel's pending line re-renders to `goal-armed` in the same tick the strip announces "goal copied — paste in the SAME session".*

### 10 — Arm cycle: cleared
**Before**
![before 10](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/before/10-qa-armed-cleared.png)
**After**
![after 10](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/10-qa-armed-cleared.png)
*The third LAUNCH click completes the cycle and the UI returns to a consistent rest state.*

### 11 — Unmapped strip expanded (idle rows)
**Before**
![before 11](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/before/11-qa-idle-expanded.png)
**After**
![after 11](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/11-qa-idle-expanded.png)
*Idle rows expand in place under a guide line; the expansion survives the next 5-second poll rebuild.*

### 12 — Banner dismissed
**Before**
![before 12](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/before/12-qa-banner-dismissed.png)
**After**
![after 12](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/12-qa-banner-dismissed.png)
*Dismissing the banner leaves no gap — the grid sits directly under the top bar with no layout shift.*

### 13 — Empty lanes
**Before**
![before 13](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/before/13-qa-empty-lanes.png)
**After**
![after 13](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/13-qa-empty-lanes.png)
*"no lanes" and "no sessions" now say what would fill them ("No lanes are open for this program yet.", "Sessions working on a lane appear here, grouped by repo.").*

### 14 — Minimal fixture, no programs
**Before**
![before 14](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/before/14-qa-minimal-no-programs.png)
**After**
![after 14](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/14-qa-minimal-no-programs.png)
*The empty program column teaches instead of shrugging, and the state-age caption explains itself on hover.*

### 15 — Frozen / degraded tracking
**Before**
![before 15](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/before/15-qa-frozen.png)
**After**
![after 15](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/15-qa-frozen.png)
*The frozen page dims under hatching with a named degraded badge ("tracking degraded: session store unreadable") and keeps its guided empty states.*

### 16 — UNPARSED note handling
**Before**
![before 16](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/before/16-qa-unparsed.png)
**After**
![after 16](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/16-qa-unparsed.png)
*UNPARSED notes now read as watch-amber, not alarm-red, and long notes clamp with the full text in a `title` tooltip (this fixture's text is short enough to fit, so no visible ellipsis).*

### 17 — Flagged launch
**Before**
![before 17](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/before/17-qa-flagged.png)
**After**
![after 17](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/17-qa-flagged.png)
*The flag surfaces as a dismissible, red-bordered alert band under the top bar instead of being lost in the chrome.*

### 18 — Null program / unstamped note
**Before**
![before 18](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/before/18-qa-null-program.png)
**After**
![after 18](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/18-qa-null-program.png)
*`launched · stamped: unknown` reads as watch-amber instead of error-red, and "skipped 1 malformed rows" is a dim data note, not a shout.*

### 19 — Bad state document (schema)
**Before**
![before 19](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/before/19-qa-bad-schema.png)
**After**
![after 19](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/19-qa-bad-schema.png)
*The failure banner gains a recovery line ("Panels stay blank until a valid state document arrives — nothing on this page is rendered from invalid data.") and the health cluster explains the stale dot.*

### 20 — Keyboard `n` (needs-me-now)
**Before**
![before 20](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/before/20-qa-keyboard-n-needs-me.png)
**After**
![after 20](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/20-qa-keyboard-n-needs-me.png)
*The shortcut is now advertised in the footer legend; pressing `n` scrolls to and cyan-flashes the top owed item (W2-L3 here).*

## All screens (after)

| Screenshot | What it shows |
| --- | --- |
| ![01](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/01-live-wall-default.png) | **01-live-wall-default** — live wall at rest: three-cluster top bar, idle CTA with owed hint, guided empty panels, collapsible unmapped strip (25 live sessions), shortcuts footer. |
| ![01b](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/01b-live-boot.png) | **01b-live-boot** — boot state before the first poll lands: all panels read "waiting for first state" under a stale live dot instead of pretending to be empty. |
| ![01c](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/01c-live-row-copy-note.png) | **01c-live-row-copy-note** — a real click on an unmapped row puts an "id copied" confirmation inside the row. |
| ![02](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/02-live-wall-fullpage.png) | **02-live-wall-fullpage** — the wall after `show all`: every unmapped session in the document, page grows to fit, no internal scroller. |
| ![03](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/03-live-needs-me-now-clicked.png) | **03-live-needs-me-now-clicked** — the idle CTA answers a click with "nothing owed" instead of doing nothing. |
| ![04](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/04-live-error-bogus-token.png) | **04-live-error-bogus-token** — a bogus path no longer shows raw 404 JSON; the operator lands back on the wall (server-side redirect, travels separately). |
| ![04b](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/04b-live-wrong-token.png) | **04b-live-wrong-token** — a wrong/expired token gets the branded recovery page pointing at `mc-wall open`. |
| ![05](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/05-qa-full-default.png) | **05-qa-full-default** — full QA fixture under the new grammar: blocked-red `failed`, watch-amber `partial`/`UNPARSED`, ok-green healthy lanes; armed strip in its own band. |
| ![06](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/06-qa-forged-lane-hover.png) | **06-qa-forged-lane-hover** — the launchable lane hovered/focused: elevated outline and cyan chevron mark the journey's entry point. |
| ![07](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/07-qa-launch-panel-open.png) | **07-qa-launch-panel-open** — the launch dialog: grouped LANE/MANIFEST sections, mirrored status chip, esc/× closers, dimming backdrop. |
| ![08](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/08-qa-armed-prompt.png) | **08-qa-armed-prompt** — first LAUNCH click: strip and panel both say prompt-armed, LAUNCH holds focus. |
| ![09](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/09-qa-armed-goal.png) | **09-qa-armed-goal** — second click: strip says "goal copied — paste in the SAME session", panel tracks `pending: goal-armed` — no contradiction. |
| ![10](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/10-qa-armed-cleared.png) | **10-qa-armed-cleared** — third click clears the cycle; the UI returns to a consistent rest state. |
| ![10b](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/10b-qa-armed-collapsed.png) | **10b-qa-armed-collapsed** — the armed strip's × collapses it to zero height: banner sits directly under the top bar, no blank band. |
| ![11](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/11-qa-idle-expanded.png) | **11-qa-idle-expanded** — idle rows expanded under a guide line; expansion survives poll rebuilds. |
| ![12](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/12-qa-banner-dismissed.png) | **12-qa-banner-dismissed** — banner dismissed with no residual gap or layout shift. |
| ![13](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/13-qa-empty-lanes.png) | **13-qa-empty-lanes** — empty-lanes fixture with next-step hints on every empty panel. |
| ![14](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/14-qa-minimal-no-programs.png) | **14-qa-minimal-no-programs** — minimal fixture: guided empties and a state-age caption that explains itself on hover. |
| ![15](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/15-qa-frozen.png) | **15-qa-frozen** — frozen fixture: hatched dim page, named degraded badge, hints intact. |
| ![16](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/16-qa-unparsed.png) | **16-qa-unparsed** — UNPARSED as watch-amber; long notes clamp with full text in a tooltip (this fixture's text fits, so no visible ellipsis). |
| ![17](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/17-qa-flagged.png) | **17-qa-flagged** — flagged launch shown as a dismissible alert band under the top bar. |
| ![18](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/18-qa-null-program.png) | **18-qa-null-program** — unstamped note as watch-amber (not error-red); malformed-row skips demoted to a dim data note. |
| ![19](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/19-qa-bad-schema.png) | **19-qa-bad-schema** — bad state document with a plain-language banner and a recovery hint; panels stay provably blank. |
| ![20](https://github.com/nexiouscaliver/mission-control/blob/feat/wall-ui-overhaul/plugins/mission-control/wall/docs/screenshots/after/20-qa-keyboard-n-needs-me.png) | **20-qa-keyboard-n-needs-me** — keyboard `n` scrolls to and cyan-flashes the top owed item; the legend in the footer advertises all three shortcuts. |

## Testing

- `.venv/bin/pytest -q` (from `plugins/mission-control/wall/`) — **234 passed**.
- `node web/selftest.mjs` — **SELFTEST PASS 91/91** (includes the new suites: severity grammar + 4.5:1 contrast, idle-CTA click with zero fetches, unmapped toggle/strip state, empty hints, kbd legend, panel sync/backdrop).
- `web/app.js` and `web/style.css` are byte-identical to their `assets/` copies (`cmp`) — the served bundle ships exactly what this PR reviews.
- UI verified live in a browser at `http://127.0.0.1:8765/VyNmthRij2oU4_ISXrcm-tRS-jLkbjT70T-namLk28g/` — 72 DOM/visual assertions, 0 failures, plus a visual pass over all 24 PNGs, exercising every canonical screen interactively (expand/collapse, real mouse clicks, clipboard-permissioned copy, Tab-focus walk, panel open/close via backdrop and ×, the full prompt→goal→cleared arm cycle, banner dismiss, and the `n` shortcut).
- One item is code-verified only: the LIVE launch lock (LAUNCH disabled with the `launch via master (v1)` note + CSS 🔒) — the live tower currently has zero programs/lanes, so no launch panel can open there; the path is asserted at the code level.
