# Before/After review notes

## ROUND 2 (2026-09-21) — outcome
All round-1 findings fixed + adaptive layout (user: "half the page unused"):

1. Armed strip clipped launch panel → strip docks by margin AND panel is box-sizing:border-box (was 453px drawn vs 420 docked — 33px overlap). Verified geometrically: strip right 1165 == panel left 1165.
2. "nothing owed nothing owed" header duplication → transient now displays "all clear ✓" (returned note contract unchanged).
3. Boot: middle column collapses to ONE waiting box (CSS, notes stay in DOM for AC-5); LIVE dot amber "booting" until first poll settles.
4. Bogus-token: 302-to-canonical fix was already in the tree (uncommitted) — kept, verified, committed.
5. Lanes severity-ordered (failed > watch > healthy, stable); verify queue oldest-first = jump order; NOT-ready merges first.
6. Chips: per-chip "stamped" removed (card head carries it once); done/parked/in-flight visual variants.
7. Unmapped: repo-dir groups with counts; rows are title/age/id segments; ids short-form, click copies full id.
8. Accent discipline: armed + owed CTA = amber (--derived); red reserved for blocked.
9. Adaptive grid (4b): has-/no-classes on #grid choose grid-template-areas; contentless columns display:none (dangling areas auto-place and garble the template — found live); sole content one-col centered (1100px); fully-empty hides grid behind big hero; slim hero line when programs absent ("No programs registered yet — N unmapped sessions below."); sole-sessions list cap 62vh.

Gates: pytest 234 passed; selftest 98/98; assets byte-identical.
Evidence: shots/round2/01..13 (live default/groups/all-clear, QA full/panel/goal/flagged/minimal/empty/frozen/unparsed/null/bad-schema).

---

# Round-1 review notes (before/after audit)

Pre-observation: before/01 and before/02 are byte-identical (70442B) — "fullpage" before shot is a dup of default view. before/07 vs 08 nearly identical sizes too.

### 13/14/19 empty + error fixtures
- Guided copy added everywhere (before: bare "no programs"/"no data"). 19 adds "nothing rendered from invalid data" line. Same dead-space problem persists.
- 19 nit: header still says "nothing owed — updates every 5 s" while the state doc is INVALID (cheerful cadence text during hard error).
- 18: "skipped 1 malformed rows" grammar survives both versions.

### 15 frozen
- Red hatch + dimming carried over (same both). Degraded badge moved next-to-title (before) → far header right (after): lower salience for an error chip.

### 16/18 unparsed + null-program
- UNPARSED / malformed chips: RED (before) → AMBER hatch (after). "Waiting on CI" amber defensible; parse-failure semantics under-signaled.

### 17 flagged
- Tiny red chip (before) → full-width red strip (after): much harder to miss. Good. × pushed to far right edge.

### 20 keyboard n
- Jump-target highlight on first verify card: orange (before) → blue (after). Equivalent mechanism, accent recolored.

## Code-confirmed defects (wall/web)
1. Armed strip `z-index: 25` deliberately rides over launch panel (z 20) — style.css:160-180. Screenshots 07/08/09/10 show it clipping the panel's first field row ("row:" label cut to fragment). padding-right dock doesn't fix the box overlap.
2. transientNote("nothing owed") (app.js:1398/1418) renders next to nmnHint "nothing owed — updates every 5 s" (app.js:201) → header reads "nothing owed — updates every 5 s nothing owed" (screens 01c, 03).
3. Boot (01b): two identical unlabeled "waiting for first state" boxes in middle column; LIVE dot red during boot.
4. After-04 (bogus token) shows healthy wall, no error surface — verification gap or swallowed error.

## Before-set evidence quality
- before/01 == before/02 byte-identical (no real fullpage before evidence).
- before/03 needs-me-now click: no visible delta at all (nothing to compare).
- before/07 ≈ before/08 near-identical.
- Before fullpage is byte-dup of before default — no real before evidence for this screen.
- After fullpage: unmapped expanded to 25 rows → right column runs full page while left/mid columns end near top: badly unbalanced. Rows are a monotonous wall (same gray card, raw uuid+path each), no grouping by repo/actor, no time bucketing.

### 03 needs-me-now clicked
- Before: no visible feedback at all.
- After: armed strip appends a second bare "nothing owed" text after "nothing owed — updates every 5 s" — reads like a duplication bug, not feedback. No distinct chip/position/color.

### 04 error screens
- Before bogus token: raw JSON body in browser (browser chrome "Pretty print"), i.e. no error page at all.
- After wrong-token (04b): proper human error page "This mc-wall link is wrong or expired" + remediation (`mc-wall open`). GOOD. But unthemed white page — jarring vs dark app.
- After 04 (bogus token): shows the NORMAL healthy wall (ages 7s/9s), NO visible error state — either mislabeled shot or bogus-token error is silently swallowed by the UI. Verification gap.

### 05 QA full default
- Deltas: armed strip promoted from cramped header chip to full-width row (better); metadata lines moved out of chip flow onto own lines (better); "4v·2m"→"4 verify · 2 merge" (better); CLEO header caps; chips gained internal "·" separator.
- Verdict: incremental polish, not a redesign. Same 3-panel skeleton, same chip soup density. NEEDS ME NOW chip carries a leftover focus ring in the "default" shot (screenshot hygiene).

### 06 forged lane hover
- Before: no visible hover affordance. After: clear teal ring around the lane group. Real improvement; ring encloses a reasonable lane segment.

### 07/08 launch panel + armed prompt
- After adds: esc hint, MANIFEST section grouping, status chip (forged) in panel. Good.
- BUG: in after, the full-width armed strip renders OVER the slide-over panel and clips its first field row (label fragment "…NE" visible under strip edge). Before had no overlap (strip lived in header).
- Armed accent color changed orange→blue. Blue reads informational; orange read "pending action". Debatable, lean orange was better for armed.
- LAUNCH button still neutral gray — weak affordance for the primary/most destructive action; big empty area under it.

### 09 armed goal
- Panel "pending:" now correctly shows goal-armed (before showed stale prompt-armed during goal state). Real state-sync fix (or fixture fix).
- Strip drops re-copy for goal state (matches before).

### 10/10b armed cleared + collapsed
- Before: tiny "✓ cleared" chip. After: full-width "✓ cleared" strip w/ × — heavier than needed for a transient ack but consistent.
- Panel "pending: cleared" correct both.
- 10b: focus ring persists on NEEDS ME NOW chip (QA driver artifact).

### 11 idle expanded
- Same content both; after adds caps CLEO, focus ring on idle toggler (artifact). Marginal.

### 12 banner dismissed
- Both fine. Note: UNPARSED chips changed red (before) → amber hatch (after). Amber = "waiting on CLI" is defensible; "UNPARSED:(empty status)" as amber may under-signal a parse failure. Semantic shift worth a deliberate decision.
- Before: 3 top boxes + unmapped(19) raw rows, ~60% of viewport is dead dark space below. No copy anywhere.
- After: same 3-column skeleton, but real empty-state sentences, "show all" on UNMAPPED(25), shortcut legend bottom-left, header gains "nothing owed — updates every 5 s".
- GOOD: empty-state copy, legend, show-all, clearer header counts ("0 verify · 0 merge").
- BAD: layout still top-heavy — ~580px of void under content; header text cramped/mixed semantics; unmapped rows still expose raw session UUIDs + full paths; muted text contrast borderline.

### 01b after-only boot
- All 3 panels "waiting for first state"; middle column renders TWO identical placeholder boxes (verify-queue + owed look like a double render, unlabeled).
- LIVE dot is RED during boot (red=error connotation; boot should be amber/neutral). Static text, no pulse/spinner. Header right "state age unknown".

### 01c after-only row copy note
- Clicked-copy state: dashed highlight + "· id copied" suffix on the row — good affordance.
- BUG: header now reads "nothing owed — updates every 5 s nothing owed" — duplicated fragment after copy click.
