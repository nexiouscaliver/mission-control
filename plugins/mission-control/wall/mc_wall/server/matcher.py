"""Matcher: read-only session-db queries for the launch handshake.

Every connection is opened through a ``file:...?mode=ro`` URI (never a
write), a fresh connection per call (never shared across threads), and any
``sqlite3.Error`` degrades to one module-logger WARNING plus a safe empty
result — the session db is a sibling's live database, not ours to touch.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import sqlite3
import typing
import urllib.parse

_LOGGER = logging.getLogger(__name__)

# Raw strings: the ESCAPE '\' backslash must reach sqlite verbatim (a normal
# string would eat the backslash and hand sqlite an empty escape character).
_CANDIDATE_SQL = r"""
SELECT si.id, si.session_id, si.payload, si.time_created,
       s.directory, s.time_created AS session_created_ms
FROM session_input si JOIN session s ON s.id = si.session_id
WHERE si.kind = 'sendText'
  AND si.time_created > :launch_click_ms
  AND s.time_created  > :launch_click_ms
  AND si.payload LIKE :pattern ESCAPE '\'
"""

_DUPLICATE_SQL = """
SELECT payload, time_created FROM session_input
WHERE session_id = :sid AND kind = 'sendText' AND time_created > :since
"""

_SESSION_CURSOR_SQL = """
SELECT COALESCE(MAX(time_created), 0) AS cursor_ms FROM session_input
WHERE session_id = :sid AND kind = 'sendText' AND time_created > :since
"""

_TARGET_SQL = """
SELECT 1 FROM session_target
WHERE session_id = :sid AND time_created > :since LIMIT 1
"""


def _like_escape(tag: str) -> str:
    # ESCAPE '\' in the SQL above pairs with these backslash escapes.
    return tag.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _payload_text(payload_json: typing.Any) -> typing.Optional[str]:
    """JSON-text confirmation: payload must be a dict carrying a str "text"."""
    try:
        payload = json.loads(payload_json)
    except (ValueError, TypeError):  # TypeError: SQL NULL payload (json.loads(None))
        return None
    if not isinstance(payload, dict):
        return None
    text = payload.get("text")
    if not isinstance(text, str):
        return None
    return text


class Matcher:
    def __init__(self, db_path: pathlib.Path):
        self.db_path = pathlib.Path(db_path)

    def _uri(self) -> str:
        return "file:" + urllib.parse.quote(str(self.db_path)) + "?mode=ro"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._uri(), uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _query(self, sql: str, params: dict) -> typing.List[sqlite3.Row]:
        # Fresh read-only connection per call; any sqlite failure is a
        # WARNING (exception class only) + empty result, never a raise.
        try:
            conn = self._connect()
        except sqlite3.Error as exc:
            _LOGGER.warning("session-db-unavailable error=%s", type(exc).__name__)
            return []
        try:
            return conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            _LOGGER.warning("session-db-unavailable error=%s", type(exc).__name__)
            return []
        finally:
            conn.close()

    def find_candidates(
        self, tag: str, launch_click_ms: int
    ) -> typing.List[typing.Dict[str, typing.Any]]:
        """Confirmed matches only: LIKE is a PREFILTER, the JSON-text check is
        the authority (a tag under any other payload key never matches)."""
        if not tag:
            return []  # "" would make the LIKE pattern "%%" match every row
        rows = self._query(
            _CANDIDATE_SQL,
            {
                "launch_click_ms": launch_click_ms,
                "pattern": "%" + _like_escape(tag) + "%",
            },
        )
        candidates = []
        for row in rows:
            text = _payload_text(row["payload"])
            if text is not None and tag in text:
                candidates.append(
                    {
                        "session_id": row["session_id"],
                        "directory": row["directory"],
                        "input_id": row["id"],
                        "text": text,
                    }
                )
        return candidates

    def find_duplicate_paste_ms(
        self, session_id: str, prompt_sha256: str, since_ms: int
    ) -> int:
        """The LATEST time_created of a confirmed duplicate paste of the
        prompt in the session after since_ms — 0 when there is none. The
        monitor uses that timestamp as its data-derived evaluation cursor:
        advancing last_eval_ms to it guarantees the SAME paste is never
        re-detected, whatever the wall clock said when the query ran (a
        clock read taken before the query would leave the cursor BEHIND a
        paste committed in between — the false-duplicate race)."""
        rows = self._query(_DUPLICATE_SQL, {"sid": session_id, "since": since_ms})
        latest = 0
        for row in rows:
            text = _payload_text(row["payload"])
            if text is None:
                continue
            if hashlib.sha256(text.encode("utf-8")).hexdigest() == prompt_sha256:
                latest = max(latest, row["time_created"])
        return latest

    def find_duplicate_paste(
        self, session_id: str, prompt_sha256: str, since_ms: int
    ) -> bool:
        return self.find_duplicate_paste_ms(session_id, prompt_sha256, since_ms) > 0

    def session_input_cursor(self, session_id: str, since_ms: int) -> int:
        """MAX(time_created) over the session's sendText rows after since_ms
        (0 when none) — everything already said in the session. The safe
        data-derived starting cursor at goal-arm: it can never sit BEFORE a
        row the matching query already saw."""
        rows = self._query(
            _SESSION_CURSOR_SQL, {"sid": session_id, "since": since_ms}
        )
        return rows[0]["cursor_ms"] if rows else 0

    def has_target_since(self, session_id: str, since_ms: int) -> bool:
        rows = self._query(_TARGET_SQL, {"sid": session_id, "since": since_ms})
        return len(rows) > 0
