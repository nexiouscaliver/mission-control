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
SELECT payload FROM session_input
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
    except ValueError:
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

    def find_duplicate_paste(
        self, session_id: str, prompt_sha256: str, since_ms: int
    ) -> bool:
        rows = self._query(
            _DUPLICATE_SQL, {"sid": session_id, "since": since_ms}
        )
        for row in rows:
            text = _payload_text(row["payload"])
            if text is None:
                continue
            if hashlib.sha256(text.encode("utf-8")).hexdigest() == prompt_sha256:
                return True
        return False

    def has_target_since(self, session_id: str, since_ms: int) -> bool:
        rows = self._query(_TARGET_SQL, {"sid": session_id, "since": since_ms})
        return len(rows) > 0
