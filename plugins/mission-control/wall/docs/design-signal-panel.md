# MC Wall — Signal Panel design contract

Design contract of record for the Signal Panel reskin of the wall frontend
(`web/style.css` + `web/app.js`). 2026-09-22, branch `loop/mcwall-signal-panel`.

**The executable contract is `web/selftest.mjs`** (104 checks; run
`node web/selftest.mjs` from the wall root). This doc explains the contract;
the selftest enforces it. Any CSS/JS change that breaks a pin here is a
contract change and must update both.

## Frozen contract (verbatim, SPEC §1)

- Tokens: `--bg #09090b` · `--raise #101013` · `--hover #18181b` · `--border #27272a` ·
  `--border-2 #3f3f46` · `--ink #fafafa` · `--dim #a1a1aa` · `--faint #71717a` ·
  `--green #34d399` · `--amber #fbbf24` · `--red #f87171` · `--blue #60a5fa`.
- Fonts: ui-monospace / SF Mono / Menlo for ALL data + labels; system-ui for prose;
  `font-variant-numeric: tabular-nums` everywhere numeric.
- Status grammar: status = 7px dot + OUTLINE mono badge (uppercase 10px, 1px colored border
  at ~40% alpha, transparent bg — NO fills). Severity hue only green/amber/red/blue;
  done/parked/unparsed-quiet render faint gray. The chip provenance cipher (solid/dashed/
  hatched borders + corner dots + fills) is REPLACED, not refined.
- Buttons: primary = solid `#fafafa` bg + `#09090b` text (armed-strip RE-COPY, NEEDS ME NOW,
  COPY VERIFY); ghost = 1px `--border-2`; disabled ~55% opacity.
- Layout: 3-column ledger `1.15fr 1fr 0.85fr`, 1px hairline row separators (`--border`),
  row hover `--hover`, NO boxes-in-boxes (columns separated by rules, not nested cards);
  topbar 46px sticky with blur; armed strip = `--raise` band + amber pulse dot; footer =
  mono legend line (`● in-flight ◐ ready ✓ done ✕ failed ◌ parked ? unparsed ⚠ stalled`) +
  kbd shortcuts.
- Radii ≤ 6px; uppercase micro-labels 11px / weight 600 / letter-spacing 0.14em / `--faint`.
  Ages humanized (15m / 40m / 2.9d) everywhere — no raw seconds anywhere in the UI. No emoji
  anywhere; unicode glyphs only where they ARE the language (legend, chevrons).

## Tokens (`style.css` `:root`)

The 12 surface tokens are pinned by `AC-26` (each must be 6-digit hex). Accent discipline
(comment at the top of style.css): `--blue` = interactive / look here, never an alarm;
`--amber` = pending human action ("your move"); `--red` = blocked/broken only.

| Token | Value | Role |
|---|---|---|
| `--bg` | `#09090b` | page background |
| `--raise` | `#101013` | armed strip, launch panel, raised surfaces |
| `--hover` | `#18181b` | row hover, default button bg |
| `--border` | `#27272a` | hairlines, row separators |
| `--border-2` | `#3f3f46` | ghost-button outlines |
| `--ink` | `#fafafa` | primary text + primary button bg |
| `--dim` | `#a1a1aa` | secondary text, `.sig` data |
| `--faint` | `#71717a` | micro-labels, quiet severity |
| `--green` | `#34d399` | ok |
| `--amber` | `#fbbf24` | watch |
| `--red` | `#f87171` | blocked |
| `--blue` | `#60a5fa` | live / interactive |

Supporting tokens: `--s1..--s5` (4/8/12/16/24px spacing — the only spacing vocabulary),
`--r-s 4px` / `--r-m 6px` (radii ≤ 6px), `--font-mono` (ui-monospace, SF Mono, Menlo,
Consolas), `--font-ui` (system-ui stack), `--focus: var(--blue)`.

Removed legacy names — grep for these should find nothing (a stale usage silently loses
its color and breaks pins):

| Removed | Replaced by |
|---|---|
| `--panel` | `--raise` |
| `--panel-2` | `--hover` |
| `--ink-dim` | `--dim` |
| `--note` | `--green` |
| `--derived` | `--amber` |
| `--stale` | `--red` |
| `--attention` | `--blue` (also feeds `--focus`) |
| `--r-pill` | removed — badges are rectangular, 3px radius |

## Class grammar

Only the status-chip grammar was renamed; all row/panel class names (`lane`,
`program-card`, `verify-row`, `merge-card`, `session-row`, `mr-badge`, …) are behavioral
pin anchors and stayed.

| Old | New | Notes |
|---|---|---|
| `div.chip` | `div.status` | contains dot + badge (+ `.status-note` for UNPARSED) |
| `.chip--note` / `.chip--stale` | removed | folded into the severity map |
| `.chip--derived` | `.sig` | plain mono data text in `--dim`; no border, no dot, never a severity class |
| `.chip--sev-ok` | `.status-dot--ok` + `.status-badge--ok` | `--green` |
| `.chip--sev-watch` | `.status-dot--watch` + `.status-badge--watch` | `--amber` |
| `.chip--sev-blocked` | `.status-dot--blocked` + `.status-badge--blocked` | `--red` |
| — (new) | `.status-dot--live` + `.status-badge--live` | `--blue` |
| — (new) | `.status-dot--quiet` + `.status-badge--quiet` | `--faint` |
| `.chip--s-done/-parked/-in-flight` | removed | covered by quiet / live |
| `.chip.stalled` | `.status--stalled` | amber treatment on the container (recolors quiet/ok badges + dot) |
| `.chip-unparsed-note` | `.status-note` | clamp: 280px, ellipsis, full text in `title` |
| `.padlock` (`🔒`) | `.padlock` = mono outline badge `locked`, amber | emoji gone; hover still lists `precondition_mrs` |
| `.verify-tag` + `::after` corner dot | `.tag-verify` | outline amber badge, NO corner dot |
| `.mr-badge--gitlab` / `--github` | kept | hues: gitlab `--blue`, github `--green`; `!`/`#` prefix is the primary encoding |

Badge anatomy (exact-string pins via `parseCssRules`): `.status-dot` = 7px × 7px,
`border-radius: 50%`, `background: var(--<hue>)`. `.status-badge` = mono, 10px, uppercase,
letter-spacing 0.08em, `border: 1px solid color-mix(in srgb, var(--<hue>) 40%,
transparent)`, transparent bg, color `var(--<hue>)`, radius 3px, padding `1px 6px`.

## Severity map

`statusSeverity(lane, mtime)` in `app.js` (~L662) returns the render hue key:

| Condition | Sev class | Hue |
|---|---|---|
| `status_parsed === "failed"` | `--blocked` | red |
| `status_parsed === "partial"` | `--watch` | amber |
| `lane.stalled !== null` (any status) | `--watch` | amber |
| `status_parsed === "UNPARSED"` | `--quiet` | faint |
| `note_mtime === 0` (unstamped) | `--watch` | amber |
| `status_parsed === "done"` / `"parked"` | `--quiet` | faint |
| `status_parsed === "forged"` | `--ok` | green |
| `status_parsed === "launched"` / `"in-flight"` | `--live` | blue |

Two non-obvious rules, both pinned:

- **Precedence**: the UNPARSED → quiet check runs BEFORE the unstamped → watch check, so an
  UNPARSED lane with `mtime === 0` still renders quiet (AC-22 unparsed-case pin); every
  other unstamped status reads watch (screen-18 regression pin).
- **Order independence (sorting ≠ styling)**: the triage DOM order is blocked first, watch
  second, remainder in served order. `laneSeverityRank` (~L625) keeps an explicit
  `status === "UNPARSED"` check so UNPARSED stays in the watch TIER of the pinned order
  even though `statusSeverity` renders it quiet. Do not "simplify" one function to derive
  from the other.

The launch-panel status mirror uses the same render map.

## Buttons

- **Primary** (solid `--ink` bg, `--bg` text, 1px `--ink` border): `#needs-me-now` when
  owed, `.copy-verify-btn`, `.armed-recopy`.
- **Ghost** (transparent bg, 1px `--border-2`): `#view-wall`, `#view-projects`,
  `.projects-sort`, `.projects-bg`, `.activate-btn`, `.launch-btn`, `.launch-close`,
  `.banner-dismiss`, `.armed-dismiss`, `.armed-cancel`, `.unmapped-toggle`,
  `.unmapped-hidden-toggle`.
- `#needs-me-now.nmn-idle` and `button:disabled`: `opacity: 0.55`; disabled is
  `cursor: not-allowed` but stays focusable per the aria pins.
- **Focus**: `outline: 2px solid var(--focus); outline-offset: 2px` on `:focus-visible`
  for buttons plus the launchable-lane/row/toggle selectors.
- Hover on any button: border-color `--blue`.

## Layout contract

- `#grid`: `grid-template-columns: 1.15fr 1fr 0.85fr`, `gap: 0`; `#col2` and
  `#col3-sessions` separate by `border-left: 1px solid var(--border)` — rules, not
  gutters, no nested cards. The eight adaptive `has-/no-` templates use fr units with
  `min-width: 0` (the old `minmax` pixel floors clipped the ledger below 1156px — do not
  reintroduce them).
- Rows (`lane`, `verify-row`, `merge-card`, `session-row`, `unmapped-row`, `project-row`)
  carry `border-bottom: 1px solid var(--border)` and hover `--hover`.
- Topbar: `min-height: 46px`, `position: sticky`, `backdrop-filter: blur(8px)` over
  translucent `--bg`.
- Armed strip: full-width `--raise` band; `.armed-dot` = 7px amber circle pulsing
  (`armed-pulse` 1.6s); `.armed--armed` border `var(--amber)`. With the launch panel open
  the strip docks via `margin-right: min(420px, 92vw)` (z-index 25 pin).
- Launch panel: fixed right, `--raise` bg, 1px `--border` left edge, width
  `min(420px, 92vw)`.
- Footer `#kbd-hint`: ONE mono legend line, maintained in BOTH `index.html` (static) and
  the `ensureShell` builder in `app.js` — the `kbd-hint` pin asserts both. Legend:
  `● in-flight ◐ ready ✓ done ✕ failed ◌ parked ? unparsed ⚠ stalled · shortcuts: [n]
  needs-me-now [r] refresh [esc] close panel` (three `.kbd` spans).
- Radii ≤ 6px everywhere; micro-labels (`.card-head`, `*-head` classes) are 11px / 600 /
  0.14em / uppercase / `--faint`; `tabular-nums` on `body` aligns numeric columns.

## Humanized ages

`humanizeAge(s)` (`app.js` ~L3460): `<60s` → `45s`; `<60m` → `3m`; `<24h` → `2h`; days
with one decimal only when fractional — `250000 → "2.9d"`, `172800 → "2d"`, `345600 →
"4d"`. Floors at 0; non-numbers degrade to `0s`.

Every UI-composed age string goes through it (`push 2.9d`, `mr !34 open 1h`, `idle 15m ·`,
verify signals `signals: done · 2.9d`, the `0s (unknown)` special case kept).

**Exemption**: verbatim server strings are NOT humanized — `stalled.because`
(`inactive for 21600s > stall_t 6h`), degraded entries, banner text, verify_cmd. Their
verbatim pins are KEEP. The raw-seconds sweep test is therefore SCOPED to the
UI-composed containers only — `.sig`, `.session-idle`, `.verify-signals` — across all
nine QA cases; unscoped it can never pass.

## Responsive

`@media (max-width: 900px)`: `#grid` stacks to a single column
(`grid-template-areas: "programs" "verify" "sessions"`, mirrored for the tagged
`has-/no-` templates at matching specificity so a tagged grid stacks too); column rules
flip from
`border-left` to `border-top` on `#col2` / `#col3-sessions`; the topbar wraps; the launch
panel keeps `min(420px, 92vw)`. Between 900px and desktop the fr-only ledger compresses
without horizontal clip.

## Accessibility notes

- Contrast (AC-26): `--ink` / `--dim` / the four hues are pinned ≥ 4.5:1 against
  `--bg` / `--raise` / `--hover`. **Known, owned deviation**: `--faint #71717a` cannot
  reach 4.5:1 on `#09090b` (~4.0:1) and is pinned ≥ 3:1, scoped to 11px uppercase
  micro-labels and the quiet severity. Data text never uses `--faint`. Status-badge 40%-
  alpha borders carry no contrast duty — the badge TEXT carries it.
- Reduced motion: `.armed-dot` pulse disabled under `prefers-reduced-motion: reduce`.
- Focus ring: 2px `--focus` offset 2 on every interactive element (see Buttons).
- Disabled controls read at 0.55 opacity.
- Emoji: none in rendered strings (selftest sweeps U+1F300–U+1FAFF across all cases);
  glyph whitelist is the legend set (`✓ ✕ ● ◐ ◌ ? ⚠`), chevrons (`▾▴▸`), `↳`, `ⓘ`.
