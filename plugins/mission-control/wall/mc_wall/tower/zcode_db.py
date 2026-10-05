"""Read-only zcode session-db access (spec §4.1, §5, §8): ro-open, schema
check, the per-collect unit probe, the windowed LIKE-prefiltered tag scan, the
title-fallback binding scan, the lane-session prefix join, newest-wins master
selection, the §6.5 unmapped enumeration, and the pure drift guard. The tower
NEVER writes the db: the only
connection form is ``sqlite3.connect("file:...?mode=ro", uri=True)``. The
``message`` table is NEVER read (spec §1/§4.1; titles come from
``session.title``).

Timestamp units. The stored unit is resolved ONCE per collect by the unit probe
(``probe_factor``: MAX(time_created) per table; ms if the magnitude > 1e11, else
seconds). ALL SQL cutoffs are computed in STORED units via ``cutoff_stored`` —
a stored value is never compared against a seconds literal — and OUTPUT values
normalize per value via ``to_seconds`` (magnitude check per value, independent
of the probe factor, so mixed magnitudes still resolve).

Session-id formats (VERIFIED live read-only 2026-09-19; re-recorded here
because a grep over the binary db is blind to this): ids are ``sess_<uuid4>``
and ``sess_subagent_agent_<uuid4>``; vault shorthands like ``sess_9a690ab2``
are 8-hex prefixes joined via ``WHERE id LIKE :token || '%'``.

LIVE DB EVIDENCE (verified read-only 2026-09-19, embedded verbatim per plan):

- `select id from session order by time_created desc limit 3` → `sess_subagent_agent_7837f21b-1f0c-4cda-9211-ac529a4cd3c4`, `sess_subagent_agent_cee54cd1-0623-4944-aad0-ace5c74d9622`, `sess_subagent_agent_077dbbd1-f1a2-4611-9232-609679beed37`
- `select id, title from session where id like 'sess_9a690ab2%'` → exactly one row: `sess_9a690ab2-cde8-4e9a-bc4e-177fcc68545f` | `MC - Managing multiple mission control sessions workflow`
- `select id from session where id like 'sess_2243e9a1%'` → exactly one row: `sess_2243e9a1-ef61-4f35-a05a-a4cd422abec6`
- `session.time_created` min/max = 1787595164004..1789839833057 (1677 rows) → MILLISECONDS magnitude; 24h sendText count at that scale = 69 rows
- Tables: `session(id, parent_id, title, title_source, directory, task_type, time_updated, time_created, time_archived)`, `session_input(session_id, kind, payload, delivery, status, time_created)` with kind `sendText`/`sendGoalCommand`, payload JSON with `.text`, `message(session_id, data, sequence)` (NEVER read per spec).

Join windows (§4.1 "by-id, unwindowed" rule): lane joins are purely
token-prefix based — the windowed tag scan NEVER invalidates a join, so an
older lane session keeps its join (``unmapped_rows`` excludes ``joined_ids``
for the same reason). No v1 output consumes a joined session's tag membership;
were one to, it would use a by-id, LIKE-prefiltered, UNWINDOWED
``session_input`` query — no such helper exists in v1 (plan ambiguity
resolution 3), so none is added.
"""

import json
import re
import sqlite3
import urllib.parse

from .config import TowerConfig

DEGRADED_UNREADABLE = "tracking degraded: session store unreadable"
DEGRADED_SCHEMA_DRIFT = "tracking degraded: session store schema drift"
DRIFT_NO_SENTEXT = "drift warning: no sendText rows in 24h"
DRIFT_PAYLOAD = "drift warning: session_input.payload not extractable"

# §4.1 required columns (the live schema has more; only these are load-bearing).
REQUIRED = {
    "session": ("id", "title", "directory", "time_updated", "time_created", "time_archived"),
    "session_input": ("session_id", "kind", "payload", "time_created"),
}

# SQL LIKE prefilter applied BEFORE any payload JSON parsing / content hashing.
TAG_PREFILTER = "%Session title:%"

# §5 tag grammar (widened, goal mcwall-autonomy): literal "Session title: [",
# bracket = any chars except ] and newline, literal "]", then an optional
# trailing name tail. Anchored, applied to line.strip(); the tag-line prefix
# itself keeps EXACT single-space matching — whitespace tolerance comes from
# str.split() INSIDE the bracket only.
TAG_LINE_RE = re.compile(r"^Session title: \[(?P<bracket>[^\]\n]+)\](?P<name>.*)$")

# Title-fallback grammar (goal mcwall-autonomy, SC-3): the SAME bracket shape
# as TAG_LINE_RE over a session TITLE, with an OPTIONAL literal
# "Session title: " prefix (the app plausibly stores either the full line or
# the bare bracket form — Q1) and NO name group: the match is unanchored-right,
# any tail after "]" is ignored. The bracket is the PREFERRED arm; the
# unbracketed live form (first two whitespace tokens, no leading "[") is the
# fallback arm inside scan_title_bindings (live dogfood 2026-09-24).
TITLE_TAG_RE = re.compile(r"^(?:Session title: )?\[(?P<bracket>[^\]\n]+)\]")


def open_db_ro(path: str) -> sqlite3.Connection:
    """The ONLY connection form: read-only URI. The path segment is
    percent-encoded (urllib.parse.quote; "/" stays literal) so '?', '#', '%',
    or spaces in db_path cannot truncate the URI or flip mode=ro — for
    ordinary paths quote() is a no-op. sqlite connects lazily — a corrupt db
    raises at the FIRST execute, so callers' degradation wrappers must cover
    connect AND the first schema query (they do)."""
    return sqlite3.connect(f"file:{urllib.parse.quote(path)}?mode=ro", uri=True)


def check_schema(cur) -> list[str]:
    """[] when every required table/column exists; else the missing
    table/column names (a missing table is recorded by its bare name)."""
    missing = []
    for table, columns in REQUIRED.items():
        rows = cur.execute(f"PRAGMA table_info({table})").fetchall()
        if not rows:
            missing.append(table)
            continue
        present = {r[1] for r in rows}
        missing.extend(f"{table}.{col}" for col in columns if col not in present)
    return missing


def probe_factor(cur) -> int:
    """The ONE-per-collect unit probe: MAX(time_created) of both tables
    (NULLs ignored); 1000 when the max magnitude > 1e11 (ms), else 1 (s)."""
    m = None
    for table in ("session", "session_input"):
        row = cur.execute(f"SELECT MAX(time_created) FROM {table}").fetchone()
        v = row[0] if row else None
        if v is not None and (m is None or v > m):
            m = v
    return 1000 if (m is not None and m > 1e11) else 1


def cutoff_stored(now_s: float, window_s: int, factor: int) -> int:
    """A SQL cutoff in STORED units: (now - window) scaled by the probe factor."""
    return int(now_s * factor - window_s * factor)


def to_seconds(v) -> float | None:
    """Per-VALUE output normalization: ms magnitudes divide by 1000. None (a
    SQL-NULL timestamp) stays None — callers handle the unknown age; a stored
    value is never fabricated into one."""
    if v is None:
        return None
    return v / 1000 if v > 1e11 else float(v)


def parse_tag_line(line: str) -> tuple[str, str | None] | None:
    """(program_tag, row_id | None) for a tag line; None for a non-tag line.

    Applied to line.strip(). Bare form (empty name tail AND at most one
    bracket token) is bit-for-bit the v1 grammar: the RAW bracket string with
    row None (covers ``[secfix]`` and the pathological ``[ ]`` alike). Titled
    form (everything else): ``tokens[0]`` is the program tag, ``tokens[1]``
    the row id when present; further bracket tokens and the entire name tail
    are IGNORED. Degenerate titled (whitespace-only bracket + non-empty name
    → ``tokens == []``) yields no product. A multi-token bracket with NO
    trailing name (one opaque tag under the v1 grammar) is DELIBERATELY a
    titled form: the app may trim the name, so binding cannot depend on it."""
    m = TAG_LINE_RE.match(line.strip())
    if m is None:
        return None
    tokens = m.group("bracket").split()
    if m.group("name").strip() == "" and len(tokens) <= 1:
        return (m.group("bracket"), None)
    if not tokens:
        return None  # degenerate titled: whitespace-only bracket + non-empty name
    return (tokens[0], tokens[1] if len(tokens) >= 2 else None)


def parse_tags(text: str) -> set[str]:
    """§5 tag grammar over split('\\n') lines (each line stripped first):
    PROGRAM tags only — bare lines contribute the raw bracket, titled lines
    their first bracket token."""
    tags = set()
    for line in text.split("\n"):
        product = parse_tag_line(line)
        if product is not None:
            tags.add(product[0])
    return tags


def scan_tag_products(cur, cutoff: int) -> dict[str, list[tuple[str, str | None]]]:
    """ONE windowed, LIKE-prefiltered sendText scan -> session_id -> ordered
    (program_tag, row_id | None) products (union across the sid's rows, SQL
    fetch order; no ORDER BY — every consumer is a set, order is
    non-load-bearing). Never full-scans session_input; never touches message;
    unparseable payloads and non-str .text values are skipped, never fatal."""
    result: dict[str, list[tuple[str, str | None]]] = {}
    rows = cur.execute(
        "SELECT session_id, payload FROM session_input"
        " WHERE kind='sendText' AND time_created > ? AND payload LIKE ?",
        (cutoff, TAG_PREFILTER)).fetchall()
    for sid, payload in rows:
        if not isinstance(payload, str):
            continue  # SQL-NULL/odd-typed payload: skipped, never fatal
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        text = obj.get("text")
        if not isinstance(text, str):
            continue
        for line in text.split("\n"):
            product = parse_tag_line(line)
            if product is not None:
                result.setdefault(sid, []).append(product)
    return result


def products_to_tags(products: list[tuple[str, str | None]]) -> set[str]:
    """{p[0] for p in products} — the scan's tag view: bare contributes the
    raw bracket, titled its first token (masters/entry-7 operate on this)."""
    return {p[0] for p in products}


def products_to_bindings(products: list[tuple[str, str | None]]) -> set[tuple[str, str]]:
    """The scan's binding view: titled products only — {(tag, row_id)} for
    products carrying a row id; bare and no-row titled products contribute
    nothing."""
    return {(t, r) for t, r in products if r is not None}


def scan_tags(cur, cutoff: int) -> dict[str, set[str]]:
    """Windowed, LIKE-prefiltered sendText tag scan -> session_id -> PROGRAM
    tag set (the products_to_tags view of the one scan; a sid appears only
    with a non-empty set)."""
    return {sid: products_to_tags(p)
            for sid, p in scan_tag_products(cur, cutoff).items()}


def scan_tag_bindings(cur, cutoff: int) -> dict[str, set[tuple[str, str]]]:
    """The products_to_bindings view of the one scan: session_id -> titled
    (program_tag, row_id) pairs (bare products never appear)."""
    return {sid: products_to_bindings(p)
            for sid, p in scan_tag_products(cur, cutoff).items()}


def scan_title_bindings(cur, now_s: float, factor: int, session_window_s: int,
                        skip_ids: set[str]) -> dict[str, tuple[str, str]]:
    """Title-fallback binding scan (SC-3): non-archived sessions created inside
    session_window_s (the unmapped_rows stored-unit cutoff) -> session_id ->
    (program_tag, row_id) when the TITLE carries a titled bracket with >= 2
    tokens (the sendGoalCommand path: the goal block's title line never hits a
    sendText row). BOTH live title forms bind (live read-only probe 2026-09-24):
    the bracketed "[tag row] name" AND the unbracketed "tag row name" the app
    itself writes — the real stored title "wall-signal-panel W3-L4 lane
    binding MR to v1.9.0" is the bracket CONTENT without brackets. The
    bracket arm (TITLE_TAG_RE, optional "Session title: " prefix) is tried
    FIRST and alone: a title it matches — even to a single-token bracket —
    never falls through to the fallback, which additionally refuses a
    tokens[0] starting with "[" (a malformed bracket opener is never read as
    an unbracketed pair); with NO bracket match, the fallback pairs the first
    two whitespace tokens of the whole stripped title. A FOREIGN pair from a
    loose two-word title is inert: the binding loop looks pairs up by the
    exact (configured-tag, lane-row-id) key, so it can never bind — absence,
    not failure. The sess_subagent_ and skip_ids exclusions are PYTHON-side
    (avoids LIKE '_' wildcard semantics, consistent with unmapped_rows;
    skip_ids carries the paste-primary rule — a session with ANY pasted pair
    never consults its title); a non-str title yields no pair; a session
    contributes at most ONE pair (titles are single-line). Read-only; never
    touches session_input or message."""
    cutoff = cutoff_stored(now_s, session_window_s, factor)
    rows = cur.execute(
        "SELECT id, title FROM session"
        " WHERE time_archived IS NULL AND time_created > ?", (cutoff,)).fetchall()
    out: dict[str, tuple[str, str]] = {}
    for sid, title in rows:
        if sid.startswith("sess_subagent_"):
            continue
        if sid in skip_ids:
            continue
        if not isinstance(title, str):
            continue
        stripped = title.strip()
        m = TITLE_TAG_RE.match(stripped)
        if m is not None:
            tokens = m.group("bracket").split()
            if len(tokens) >= 2:
                out[sid] = (tokens[0], tokens[1])
            continue  # bracket preferred: a bracket match never reaches the fallback
        tokens = stripped.split()
        if len(tokens) >= 2 and not tokens[0].startswith("["):
            out[sid] = (tokens[0], tokens[1])
    return out


def _has_parent_id(cur) -> bool:
    """session.parent_id presence (verified live read-only 2026-09-22: the
    column EXISTS and is POPULATED — workflow actors ``sess_dwf-dwfrun-…``
    and side chats carry their spawning chat's id). The column is OPTIONAL:
    unlike REQUIRED, an older db without it reads every parent as NULL —
    never a store-wide schema-drift degradation."""
    rows = cur.execute("PRAGMA table_info(session)").fetchall()
    return any(r[1] == "parent_id" for r in rows)


def _parent_expr(cur) -> str:
    """The SELECT expression for the parent column: the real column when
    present, SQL NULL when absent — the fetched row shape stays uniform
    either way, so callers unpack one fixed column list."""
    return "parent_id" if _has_parent_id(cur) else "NULL"


def _as_parent_id(v) -> str | None:
    """A parent id is a non-empty str; SQL-NULL / empty / odd-typed -> None
    (an absent parent and a blank parent are the same fact)."""
    return v if isinstance(v, str) and v != "" else None


def session_rows(cur, ids: list[str]) -> dict[str, dict]:
    """By-id parameterized session rows (id/title/directory + raw timestamps
    + parent_session_id)."""
    parent = _parent_expr(cur)
    out: dict[str, dict] = {}
    for sid in ids:
        row = cur.execute(
            f"SELECT id, title, directory, time_updated, time_created, {parent}"
            " FROM session WHERE id = ?", (sid,)).fetchone()
        if row is not None:
            out[row[0]] = {"id": row[0], "title": row[1], "directory": row[2],
                           "time_updated": row[3], "time_created": row[4],
                           "parent_session_id": _as_parent_id(row[5])}
    return out


def parent_titles(cur, ids) -> dict[str, str | None]:
    """By-id, UNWINDOWED title resolution for parent sessions (the session_rows
    pattern, one column): the parent of an in-window child is often itself
    older than the session window, so its title must come from the db, not the
    doc. A missing parent row, a SQL-NULL title, or an empty one maps to None —
    the client falls back to its in-doc index, then the short id."""
    out: dict[str, str | None] = {}
    for pid in set(ids):
        row = cur.execute("SELECT title FROM session WHERE id = ?", (pid,)).fetchone()
        out[pid] = row[0] if row is not None and isinstance(row[0], str) and row[0] != "" else None
    return out


def lane_join(cur, token: str) -> tuple[dict | None, bool]:
    """Token-prefix join (§5): exactly 1 hit -> ({id, title, dir,
    time_updated_epoch_s, parent_session_id}, False); 0 hits -> (None, False);
    >1 -> (None, True). time_updated_epoch_s is None for a SQL-NULL timestamp
    (caller resolves the unknown age). The contract session object
    (title_pending / last_active_ago_s) is assembled by collect, which owns
    `now`. Token membership is never consulted here and the windowed scan never
    invalidates a join."""
    rows = cur.execute(
        "SELECT id, title, directory, time_updated, " + _parent_expr(cur) +
        " FROM session WHERE id LIKE ?",
        (token + "%",)).fetchall()
    if len(rows) > 1:
        return (None, True)
    if not rows:
        return (None, False)
    sid, title, directory, time_updated, parent_id = rows[0]
    return ({"id": sid, "title": title, "dir": directory,
             "time_updated_epoch_s": to_seconds(time_updated),
             "parent_session_id": _as_parent_id(parent_id)}, False)


def unmapped_rows(cur, now_s: float, factor: int, session_window_s: int,
                  joined_ids: set[str], tag_map: dict[str, set[str]],
                  configured_tags: dict[str, str]) -> list[dict]:
    """§6.5 enumeration, straight from the session table: non-archived, above
    the unit-aware session_window_s cutoff (stored units), minus sess_subagent_
    ids, minus tag-mapped sessions (tag-set size exactly 1 with a configured
    program/master tag), minus token-joined ids. Ambiguous (>=2 tags), size-0
    and unmatched-tag sessions remain. Order: last_active desc (smallest ago
    first), tie id asc."""
    cutoff = cutoff_stored(now_s, session_window_s, factor)
    rows = cur.execute(
        "SELECT id, title, directory, time_updated, " + _parent_expr(cur) +
        " FROM session WHERE time_archived IS NULL AND time_created > ?", (cutoff,)).fetchall()
    out = []
    for sid, title, directory, time_updated, parent_id in rows:
        if sid.startswith("sess_subagent_"):
            continue
        if sid in joined_ids:
            continue
        tags = tag_map.get(sid, set())
        if len(tags) == 1 and next(iter(tags)) in configured_tags:
            continue
        ts = to_seconds(time_updated)
        out.append({"id": sid,
                    "title": title if title is not None else "",
                    "dir": directory if directory is not None else "",
                    # contract requires int; 0 is the spec's unknown-age convention
                    "last_active_ago_s": max(0, int(now_s - ts)) if ts is not None else 0,
                    "parent_session_id": _as_parent_id(parent_id)})
    # The parent's NAME rides the row: an id alone told the operator nothing
    # (they do not memorize session ids), and the parent chat is usually older
    # than the window, so client-side resolution fails exactly when it matters.
    titles = parent_titles(cur, [r["parent_session_id"] for r in out
                                 if r["parent_session_id"] is not None])
    for r in out:
        r["parent_title"] = titles.get(r["parent_session_id"]) if r["parent_session_id"] is not None else None
    out.sort(key=lambda r: (r["last_active_ago_s"], r["id"]))
    return out


def check_drift(config: TowerConfig) -> list[str]:
    """§8 drift guard — pure: no writes, no module state; identical under a
    fixed injected clock (the cutoffs read config.now_s(), so a moving clock
    legitimately moves them). The caller (server lane) schedules it at startup
    and hourly; the tower itself starts no threads/timers. Warnings 3/4 are
    guard-only: collect_state never emits them."""
    try:
        con = open_db_ro(config.db_path)
        try:
            cur = con.cursor()
            if check_schema(cur):
                return [DEGRADED_SCHEMA_DRIFT]
            factor = probe_factor(cur)
            cutoff = cutoff_stored(config.now_s(), 86400, factor)
            n = cur.execute(
                "SELECT COUNT(*) FROM session_input"
                " WHERE kind='sendText' AND time_created > ?", (cutoff,)).fetchone()[0]
            if not n:
                return [DRIFT_NO_SENTEXT]
            row = cur.execute(
                "SELECT payload FROM session_input"
                " WHERE kind='sendText' AND time_created > ?"
                " ORDER BY time_created DESC LIMIT 1", (cutoff,)).fetchone()
            payload = row[0] if row else None
            obj = None
            if isinstance(payload, str):
                try:
                    obj = json.loads(payload)
                except ValueError:
                    obj = None
            # a SQL-NULL / non-str / non-dict / textless payload is exactly the
            # drift this guard detects — warning 4, never an entry-1 reaction
            if not (isinstance(obj, dict) and isinstance(obj.get("text"), str)):
                return [DRIFT_PAYLOAD]
            return []
        finally:
            con.close()
    except Exception:
        return [DEGRADED_UNREADABLE]
