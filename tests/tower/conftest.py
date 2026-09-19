"""Shared mc_wall tower test helpers (fixtures-only world; zero live reads).

Helpers are prefixed ``mcwallt_`` per the plan's naming rule; later tasks grow
this file (T-4 the goal tree, T-5 the fake command runner, T-6 the composed
fixture world). ``mcwallt_make_db`` builds temp dbs with the live schema shape
(default DELETE journal, closed cleanly — no ``-wal``/``-shm`` side files);
fixture timestamps are ms-magnitude unless a test proves the unit probe with
seconds-magnitude values.
"""

import json
import sqlite3
import time

import pytest


def mcwallt_make_note(tmp_path, name, lines):
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def mcwallt_make_db(tmp_path, name="mcwallt_sessions.db", sessions=(), inputs=(), drop_input=False):
    """Temp zcode session db (live column shape). sessions: dicts with id,
    time_updated, time_created and optional title/directory/time_archived;
    inputs: dicts with session_id, payload, time_created (+ optional kind)."""
    p = tmp_path / name
    con = sqlite3.connect(p)
    con.executescript("""
      CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT, title TEXT, title_source TEXT,
        directory TEXT, task_type TEXT, time_updated INTEGER, time_created INTEGER, time_archived INTEGER);
      CREATE TABLE session_input (session_id TEXT, kind TEXT, payload TEXT, delivery TEXT,
        status TEXT, time_created INTEGER);""")
    con.executemany("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?)",
        [(s["id"], None, s.get("title"), None, s.get("directory", ""), None,
          s["time_updated"], s["time_created"], s.get("time_archived")) for s in sessions])
    con.executemany("INSERT INTO session_input VALUES (?,?,?,?,?,?)",
        [(i["session_id"], i.get("kind", "sendText"), i["payload"], None, None, i["time_created"]) for i in inputs])
    if drop_input:
        con.execute("DROP TABLE session_input")
    con.commit()
    con.close()
    return str(p)


def mcwallt_tag_input(sid, tags, t, extra=""):
    return {"session_id": sid, "payload": json.dumps(
        {"text": extra + "".join(f"Session title: [{t}]\n" for t in tags)}), "time_created": t}


def mcwallt_make_session_db(tmp_path, name="mcwallt_sessions.db"):
    """Minimal db satisfying the session-store schema check (both required tables)."""
    return mcwallt_make_db(tmp_path, name)


def mcwallt_clock(start: float, step: float = 0.0):
    t = {"v": start}
    def _now(): t["v"] += step; return t["v"]
    return _now
