"""T7 — Matcher: read-only session db, LIKE-escape + JSON confirm, duplicate, canary.

Every mc_wall / harness import is INSIDE the test functions, mirroring
test_mcwalls_launch.py: at RED time each test must FAIL individually (exit 1)
instead of erroring at collection (exit 2).
"""


def test_connection_is_read_only():
    import sqlite3  # noqa: F401  (pylint: fixture import used in pytest.raises)

    import pytest

    from mc_wall.server.matcher import Matcher
    from tests.server.mcwalls_harness import make_fixture_db

    db, _anchor = make_fixture_db()
    m = Matcher(db)
    assert "mode=ro" in m._uri()
    conn = m._connect()
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO session VALUES ('x','x',0,'x')")
    finally:
        conn.close()


def test_wal_bytes_untouched_through_full_cycle():
    import hashlib

    from mc_wall.server.matcher import Matcher
    from tests.server.mcwalls_harness import make_fixture_db

    db, anchor = make_fixture_db(
        keep_wal=True,
        sessions=[("s1", "/tmp/mcwalls-repo", 2000)],
        inputs=[(1, "s1", {"text": "hi [tag-x] go"}, 3000)],
        targets=[("s1", "t1", 4000)],
    )
    try:
        def digest(path):
            return hashlib.sha256(path.read_bytes()).hexdigest()

        wal = db.with_name(db.name + "-wal")
        assert wal.exists()  # anchor holds the WAL alive
        before_db, before_wal = digest(db), digest(wal)
        # -shm is deliberately NOT hashed: it is a shared-memory index whose
        # bytes may legitimately change when readers map it (spec oracle note).

        m = Matcher(db)
        assert m.find_candidates("[tag-x]", 1000) == [
            {
                "session_id": "s1",
                "directory": "/tmp/mcwalls-repo",
                "input_id": 1,
                "text": "hi [tag-x] go",
            }
        ]
        assert (
            m.find_duplicate_paste(
                "s1", hashlib.sha256(b"hi [tag-x] go").hexdigest(), 1000
            )
            is True
        )
        assert m.has_target_since("s1", 1000) is True

        # AC-46: byte-identical db AND -wal after the full read cycle.
        assert digest(db) == before_db
        assert digest(wal) == before_wal
    finally:
        anchor.close()


def test_like_escape_and_json_confirmation():
    from mc_wall.server.matcher import Matcher
    from tests.server.mcwalls_harness import make_fixture_db

    # (a) LIKE is a prefilter: tag 50%_off must match LITERALLY, never as
    # wildcards — "50 percent off" is not a textual containment.
    db, _ = make_fixture_db(
        sessions=[("sa", "/r", 2000)],
        inputs=[(1, "sa", {"text": "50 percent off"}, 3000)],
    )
    assert Matcher(db).find_candidates("50%_off", 1000) == []

    # (b) literal tag inside "text" -> one confirmed hit.
    db, _ = make_fixture_db(
        sessions=[("sb", "/r", 2000)],
        inputs=[(1, "sb", {"text": "do 50%_off now"}, 3000)],
    )
    hits = Matcher(db).find_candidates("50%_off", 1000)
    assert [h["text"] for h in hits] == ["do 50%_off now"]

    # (c) tag under a DIFFERENT json key -> LIKE passes, JSON confirm discards.
    db, _ = make_fixture_db(
        sessions=[("sc", "/r", 2000)],
        inputs=[(1, "sc", {"other": "50%_off", "text": "unrelated"}, 3000)],
    )
    assert Matcher(db).find_candidates("50%_off", 1000) == []

    # (d) underscore wildcard trap: tag _u_ outside "text" -> [].
    db, _ = make_fixture_db(
        sessions=[("sd", "/r", 2000)],
        inputs=[(1, "sd", {"other": "x_u_", "text": "bu u"}, 3000)],
    )
    assert Matcher(db).find_candidates("_u_", 1000) == []


def test_time_window_excludes_old_sessions():
    from mc_wall.server.matcher import Matcher
    from tests.server.mcwalls_harness import make_fixture_db

    # Matching tag, but session AND input were both created BEFORE the
    # launch click -> invisible to the matcher (AC-31 matcher half).
    db, _ = make_fixture_db(
        sessions=[("s-old", "/r", 500)],
        inputs=[(1, "s-old", {"text": "has [tag] inside"}, 600)],
    )
    assert Matcher(db).find_candidates("[tag]", 1000) == []


def test_duplicate_paste_detection():
    import hashlib

    from mc_wall.server.matcher import Matcher
    from tests.server.mcwalls_harness import make_fixture_db

    text = "PROMPT TEXT 42"
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    db, _ = make_fixture_db(
        sessions=[("s1", "/r", 100)],
        inputs=[
            (1, "s1", {"text": text}, 200),  # after cursor, same text
            (2, "s1", {"text": "different"}, 300),  # after cursor, other text
            (3, "s1", {"text": text}, 50),  # identical, but BEFORE the cursor
        ],
    )
    m = Matcher(db)
    assert m.find_duplicate_paste("s1", sha, 100) is True
    assert (
        m.find_duplicate_paste("s1", hashlib.sha256(b"not-the-prompt").hexdigest(), 100)
        is False
    )

    db2, _ = make_fixture_db(
        sessions=[("s1", "/r", 100)],
        inputs=[(1, "s1", {"text": text}, 50)],  # only the pre-cursor paste
    )
    assert Matcher(db2).find_duplicate_paste("s1", sha, 100) is False


def test_target_since():
    from mc_wall.server.matcher import Matcher
    from tests.server.mcwalls_harness import make_fixture_db

    db, _ = make_fixture_db(
        sessions=[("s1", "/r", 100), ("s2", "/r", 100)],
        targets=[("s1", "t1", 500)],
    )
    m = Matcher(db)
    assert m.has_target_since("s1", 100) is True  # target row after since
    assert m.has_target_since("s1", 900) is False  # row lies before the cursor
    assert m.has_target_since("s2", 100) is False  # no target at all


def test_missing_db_logs_and_returns_empty():
    from pathlib import Path

    from mc_wall.server.matcher import Matcher

    m = Matcher(Path("/nonexistent/mcwalls-db.sqlite"))
    assert m.find_candidates("[t]", 0) == []
    assert m.find_duplicate_paste("s1", "x" * 64, 0) is False
    assert m.has_target_since("s1", 0) is False
