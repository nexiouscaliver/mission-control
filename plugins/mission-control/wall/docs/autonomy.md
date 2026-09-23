# Wall autonomy: lane binding, ambiguity, verify

Reference for the tower's tag-driven autonomy (goal `mcwall-autonomy`): how a
token-less lane acquires a session on its own, how contention resolves, how
the verify cue clears without operator action, and the read-only guarantee
that bounds all of it. Implementation: the binding block in
`mc_wall/tower/collect.py` `_join_sessions`, over the scans in
`mc_wall/tower/zcode_db.py`.

## Binding precedence: row-token > pasted-tag > title-tag

A lane acquires at most one session, from the first source that claims it
(`mc_wall/tower/collect.py` `_join_sessions`):

1. **Row token.** A `sess_` shorthand declared in the lane's session/MR
   artifacts cell (`mc_wall/tower/notes.py` `SESS_RE` extraction ->
   `NoteRow.sess_token`) joins by id prefix (`zcode_db.py` `lane_join`) —
   the original §5 join, and it always wins. Tag binding only fires on lanes
   whose PARSED row carries NO token: eligibility is evaluated on the parsed
   row before any join outcome, so a declared-but-stale token (one matching
   no session) blocks binding too — the operator's declaration is never
   overridden by a tag.
2. **Pasted tag.** The windowed scan of `session_input` `sendText` rows
   (`zcode_db.py` `scan_tag_products`, LIKE-prefiltered, window
   `tag_scan_window_s` = 259200 s = 72 h) reads pasted
   `Session title: [<tag> <W-L>]` lines (`parse_tag_line` titled form); a
   titled pair claims lane `<W-L>` of the program whose configured tag is
   `<tag>`.
3. **Title tag.** Sessions with zero pasted pairs fall back to their own
   `session.title` (`zcode_db.py` `scan_title_bindings` over `TITLE_TAG_RE` —
   the same bracket with an optional `Session title: ` prefix), windowed by
   `session_window_s` = 86400 s = 24 h on session creation; subagent
   (`sess_subagent_`) sessions are excluded.

The title fallback is load-bearing, not redundant with the paste window:
goal-steered sessions get their title line from the `/goal` block, which
rides a `sendGoalCommand` row the `sendText` scan never sees. A title is
durable past the 72 h paste window (inside its own 24 h window) but volatile
under an operator rename, while a pasted `sendText` row never changes — so a
session with ANY pasted pair never consults its title (`_join_sessions`
passes those sids as `skip_ids` to `scan_title_bindings`).

Foreign tags and foreign rows never bind: a claim matches a lane only on the
exact `(program tag, row_id)` pair, and a claim with no matching lane is
absence, not failure — no degraded line.

## Ambiguity: newest wins, losers visible

Two kinds of ambiguity, both observable (`_join_sessions`; both lines surface
in the wall's degraded list with the session-join ambiguity family,
`DegradedLog` group 4):

- **Two or more sessions claiming one `(tag, row)`.** Orphan claims (a sid
  with inputs but no `session` row) are dropped first (`session_rows`), then
  the NEWEST session wins by the None-safe key
  `(time_updated is not None, time_updated, time_created is not None, time_created, sid)`
  — a SQL-NULL timestamp loses to any real one, ties fall to `time_created`,
  then to the lexicographically greatest sid. Exactly ONE degraded line per
  ambiguous pair:
  `tag bind degraded: <tag>/<row> newest wins, losers <sid1>,<sid2>`
  (losers sorted, comma-joined, full sids).
- **One session claiming two or more rows** (two different titled pastes).
  It binds nothing — no lane gets it — and emits
  `tag bind degraded: ambiguous session <sid>`.

Losers and ambiguous sessions are dropped from the tag view handed to
`unmapped_rows` (`_join_sessions` `invisible_drop`): their tag lookup becomes
size 0, so they render in `sessions_unmapped` — degraded, never hidden.

## The verified token: `verify:ok`

At verdict time the mission-control controller writes the literal token
`verify:ok` into the row's session/MR-artifacts cell (skill §4). The Wall is
only a consumer: `mc_wall/tower/notes.py` `VERIFY_TOKEN` is a substring test
on the artifacts cell (`NoteRow.verified`), and `mc_wall/tower/derive.py`
`derive_suggest_verify(…, verified=True)` returns None immediately — the
lane's `suggest_verify` cue clears on reading the token. `verify_queue` is
unaffected by design: a verified done/partial row keeps its queue entry (the
operator's `/mission-control-verify` entry point).

## The session store stays read-only

Every tower connection goes through `zcode_db.py` `open_db_ro` — a sqlite
`file:…?mode=ro` URI, the only connection form; any db failure degrades the
display, never a write. The tag and title scans add zero writes (SELECT-only
over `session_input` and `session`). The byte-hash pin
(`tests/tower/test_mcwallt_autonomy.py`, `wa-1-bind-readonly-db`) proves it:
the db file's bytes and its directory listing (no `-wal`/`-shm` sidecars)
are identical before and after a binding collect.
