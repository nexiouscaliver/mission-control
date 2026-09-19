"""Read-only zcode session-db access (spec §4.1, §5, §8): ro-open, schema
check, the per-collect unit probe, the windowed LIKE-prefiltered tag scan, the
lane-session prefix join, newest-wins master selection, the §6.5 unmapped
enumeration, and the pure drift guard. The tower NEVER writes the db: the only
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

# §5 tag grammar: literal "Session title: [", tag = any chars except ] and
# newline, literal "]"; anchored, applied to line.strip().
TAG_LINE_RE = re.compile(r"^Session title: \[(?P<tag>[^\]\n]+)\]$")


def open_db_ro(path: str) -> sqlite3.Connection:
    """The ONLY connection form: read-only URI. sqlite connects lazily — a
    corrupt db raises at the FIRST execute, so callers' degradation wrappers
    must cover connect AND the first schema query (they do)."""
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


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


def to_seconds(v) -> float:
    """Per-VALUE output normalization: ms magnitudes divide by 1000."""
    return v / 1000 if v > 1e11 else float(v)


def parse_tags(text: str) -> set[str]:
    """§5 tag grammar over split('\\n') lines (each line stripped first)."""
    tags = set()
    for line in text.split("\n"):
        m = TAG_LINE_RE.match(line.strip())
        if m:
            tags.add(m.group("tag"))
    return tags


def scan_tags(cur, cutoff: int) -> dict[str, set[str]]:
    """Windowed, LIKE-prefiltered sendText tag scan -> session_id -> tag set.
    Never full-scans session_input; never touches message; unparseable payloads
    and non-str .text values are skipped, never fatal."""
    result: dict[str, set[str]] = {}
    rows = cur.execute(
        "SELECT session_id, payload FROM session_input"
        " WHERE kind='sendText' AND time_created > ? AND payload LIKE ?",
        (cutoff, TAG_PREFILTER)).fetchall()
    for sid, payload in rows:
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        text = obj.get("text")
        if not isinstance(text, str):
            continue
        tags = parse_tags(text)
        if tags:
            result.setdefault(sid, set()).update(tags)
    return result


def session_rows(cur, ids: list[str]) -> dict[str, dict]:
    """By-id parameterized session rows (id/title/directory + raw timestamps)."""
    out: dict[str, dict] = {}
    for sid in ids:
        row = cur.execute(
            "SELECT id, title, directory, time_updated, time_created"
            " FROM session WHERE id = ?", (sid,)).fetchone()
        if row is not None:
            out[row[0]] = {"id": row[0], "title": row[1], "directory": row[2],
                           "time_updated": row[3], "time_created": row[4]}
    return out


def lane_join(cur, token: str) -> tuple[dict | None, bool]:
    """Token-prefix join (§5): exactly 1 hit -> ({id, title, dir,
    time_updated_epoch_s}, False); 0 hits -> (None, False); >1 -> (None, True).
    The contract session object (title_pending / last_active_ago_s) is
    assembled by collect, which owns `now`. Token membership is never consulted
    here and the windowed scan never invalidates a join."""
    rows = cur.execute(
        "SELECT id, title, directory, time_updated FROM session WHERE id LIKE ?",
        (token + "%",)).fetchall()
    if len(rows) > 1:
        return (None, True)
    if not rows:
        return (None, False)
    sid, title, directory, time_updated = rows[0]
    return ({"id": sid, "title": title, "dir": directory,
             "time_updated_epoch_s": to_seconds(time_updated)}, False)


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
        "SELECT id, title, directory, time_updated FROM session"
        " WHERE time_archived IS NULL AND time_created > ?", (cutoff,)).fetchall()
    out = []
    for sid, title, directory, time_updated in rows:
        if sid.startswith("sess_subagent_"):
            continue
        if sid in joined_ids:
            continue
        tags = tag_map.get(sid, set())
        if len(tags) == 1 and next(iter(tags)) in configured_tags:
            continue
        out.append({"id": sid,
                    "title": title if title is not None else "",
                    "dir": directory if directory is not None else "",
                    "last_active_ago_s": max(0, int(now_s - to_seconds(time_updated)))})
    out.sort(key=lambda r: (r["last_active_ago_s"], r["id"]))
    return out


def check_drift(config: TowerConfig) -> list[str]:
    """§8 drift guard — PURE (no writes, no module state; identical on repeat
    calls). The caller (server lane) schedules it at startup and hourly; the
    tower itself starts no threads/timers. Warnings 3/4 are guard-only:
    collect_state never emits them."""
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
            try:
                obj = json.loads(row[0]) if row else None
            except ValueError:
                obj = None
            if not (isinstance(obj, dict) and isinstance(obj.get("text"), str)):
                return [DRIFT_PAYLOAD]
            return []
        finally:
            con.close()
    except Exception:
        return [DEGRADED_UNREADABLE]
