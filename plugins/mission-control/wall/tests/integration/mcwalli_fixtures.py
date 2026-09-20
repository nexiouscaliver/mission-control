"""Composed fixture world for the mcwall integration suite (tests-as-product).

One world serves BOTH sides of the stack: the real tower
(``mc_wall.tower.collect_state`` over ``world.cfg``) and the real server
(``create_server`` via the harness, with ``world.db_path`` feeding the
HandshakeMonitor's Matcher). The db is a UNION schema — the tower's 9-column
``session`` shape plus the matcher's ``session_input.id`` /
``session_target`` needs — so one file backs tower SQL and matcher SQL alike.

Zero real subprocesses: ``netcache._run_cmd`` is monkeypatched onto the tower
conftest's scripted handler (ls-remote MISS for loop/mcwalli is a value, not a
degradation; ``glab mr view 5`` hits the scripted open/green MR). The tower
clock is frozen at NOW_S; tests freeze the server clock at CLICK_MS via the
harness FakeClock, so the advisory-120s / canary deltas are 0 and never fire.
"""

import dataclasses
import json
import sqlite3
from pathlib import Path

from mc_wall.server import state_contract
from mc_wall.tower import NetCache, ProgramConfig, RepoConfig, TowerConfig
from mc_wall.tower import netcache as netcache_module
from tests.tower.conftest import (
    MCWALLT_HEADER_A,
    MCWALLT_SEP,
    mcwallt_fake_cmd,
    mcwallt_make_goal_tree,
    mcwallt_make_note,
    mcwallt_settable_clock,
    mcwallt_tag_input,
    mcwallt_world_default_handler,
)

# Pinned clock: ms magnitude for the stored/db side, seconds for the tower's
# injected now_s (both frozen — determinism, never wall time).
CLICK_MS = 1_900_000_123_456
NOW_S = CLICK_MS // 1000

# World identities (sess_<uuid4> shape; LANE_TOKEN is the 8-hex join prefix).
MASTER_ID = "sess_3a3a3a3a-3a3a-4a3a-8a3a-3a3a3a3a3a3a"
BIRTH_ID = "sess_b17e4d2a-9c1d-4e2f-8a3b-7c9d0e1f2a3b"
LANE_TOKEN = BIRTH_ID[:13]
UNMAPPED_ID = "sess_77777777-7777-4777-8777-777777777777"

# Master seeded 1h before the frozen now; the birth session lands 1s AFTER the
# frozen launch click so the matcher cutoffs (strictly >) hit deterministically.
T_MASTER = CLICK_MS - 3_600_000
T_BIRTH = CLICK_MS + 1000

_SESSION_COLS = ("id", "parent_id", "title", "title_source", "directory",
                 "task_type", "time_updated", "time_created", "time_archived")
_INPUT_COLS = ("id", "session_id", "kind", "payload", "delivery", "status",
               "time_created")
_TARGET_COLS = ("session_id", "target_id", "objective", "status", "time_created")

_UNION_SCHEMA = """
  CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT, title TEXT, title_source TEXT,
    directory TEXT, task_type TEXT, time_updated INTEGER, time_created INTEGER, time_archived INTEGER);
  CREATE TABLE session_input (id INTEGER PRIMARY KEY, session_id TEXT, kind TEXT, payload TEXT,
    delivery TEXT, status TEXT, time_created INTEGER);
  CREATE TABLE session_target (session_id TEXT, target_id TEXT, objective TEXT, status TEXT, time_created INTEGER);
"""


def _insert(conn, table: str, columns: tuple, rows) -> None:
    """INSERT by EXPLICIT column list only (never positional across the 9-col
    session table); unknown keys raise so a fixture typo cannot silently NULL."""
    for row in rows:
        unknown = set(row) - set(columns)
        if unknown:
            raise ValueError(f"unknown {table} column(s): {sorted(unknown)}")
        cols = [c for c in columns if c in row]
        conn.execute(
            f"INSERT INTO {table} ({', '.join(cols)})"
            f" VALUES ({', '.join('?' for _ in cols)})",
            [row[c] for c in cols])


def mcwalli_union_db(tmp_path, sessions=(), inputs=(), targets=()) -> str:
    """Union-schema session db (tower SQL + matcher SQL) at
    tmp_path/mcwalli_union.db. Rows are dicts keyed by column name; input rows
    default kind to 'sendText' (so ``mcwallt_tag_input`` dicts pass through).
    Default (DELETE) journal + clean close — no ``-wal``/``-shm`` side files."""
    db = Path(tmp_path) / "mcwalli_union.db"
    if db.exists():
        db.unlink()  # same-name rebuilds within one test stay fixture-safe
    conn = sqlite3.connect(db)
    try:
        conn.executescript(_UNION_SCHEMA)
        _insert(conn, "session", _SESSION_COLS, sessions)
        _insert(conn, "session_input", _INPUT_COLS,
                [dict(r, kind=r.get("kind", "sendText")) for r in inputs])
        _insert(conn, "session_target", _TARGET_COLS, targets)
        conn.commit()
    finally:
        conn.close()
    return str(db)


def mcwalli_row(repo_root: str) -> dict:
    """The B3 rows-view row for the handshake bridge (key names spelled via
    ``mc_wall.server.state_contract`` — the reconciliation point)."""
    return {
        state_contract.ROW_ID_KEY: "W1-L1",
        state_contract.LANE_TAG_KEY: "mcwalli-lane",
        state_contract.PROMPT_TEXT_KEY: "MCWALLI PROMPT",
        state_contract.GOAL_TEXT_KEY: "MCWALLI GOAL",
        state_contract.ROW_REPO_ROOT_KEY: repo_root,
    }


@dataclasses.dataclass
class McwalliWorld:
    cfg: TowerConfig
    db_path: str
    repo: str
    note_path: str
    launch_path: str
    goal_dir: str


def mcwalli_world(tmp_path, monkeypatch) -> McwalliWorld:
    """One fixture world: goal tree, vault note (one launched lane W1-L1 with
    the LANE_TOKEN join + !5 artifacts ref), pending-launch file, union db
    (master session + its tag input, one untagged unmapped session — the birth
    session is NOT here; tests insert it via ``mcwalli_insert_birth``), frozen
    tower clock, and the scripted no-spawn network."""
    repo, goal_dir = mcwallt_make_goal_tree(
        tmp_path, slug="mcwalli-slug", queue_lines=["q1", "", "q-last"])
    note_path = mcwallt_make_note(tmp_path, "mcwalli_note.md", [
        "objective: mcwalli objective",
        MCWALLT_HEADER_A,
        MCWALLT_SEP,
        f"| W1-L1 | W1 | L1 | {repo} loop/mcwalli | mcwalli-slug | n/a"
        f" | !5; {LANE_TOKEN} | launched |",
    ])
    launch_path = Path(tmp_path) / "mcwalli_launch.json"
    launch_path.write_text(json.dumps({"slug": "mcwalli", "n": 1}), encoding="utf-8")
    db_path = mcwalli_union_db(
        tmp_path,
        sessions=[
            {"id": MASTER_ID, "title": "mcwalli master",
             "directory": str(Path(repo).resolve()),
             "time_created": T_MASTER, "time_updated": T_MASTER},
            {"id": UNMAPPED_ID, "title": "mcwalli unmapped",
             "directory": str(Path(tmp_path) / "mcwalli-unmapped-dir"),
             "time_created": CLICK_MS - 60_000, "time_updated": CLICK_MS - 60_000},
        ],
        inputs=[mcwallt_tag_input(MASTER_ID, ["mcwalli-master"], T_MASTER)],
    )
    fake, _calls = mcwallt_fake_cmd(mcwallt_world_default_handler)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    now_s, _set_now = mcwallt_settable_clock(NOW_S)
    cfg = TowerConfig(
        db_path=db_path,
        programs=(ProgramConfig(program="mcwalli", tag="mcwalli-lane",
                                note_glob=note_path,
                                master_tag="mcwalli-master"),),
        repos=(RepoConfig(name="mcwalli-repo", path=repo, host="gitlab"),),
        pending_launch_path=str(launch_path),
        now_s=now_s,
        network_cache=NetCache(),
    )
    return McwalliWorld(cfg=cfg, db_path=db_path, repo=repo, note_path=note_path,
                        launch_path=str(launch_path), goal_dir=goal_dir)


def mcwalli_insert_birth(db_path: str, repo: str) -> None:
    """The birth session, inserted by TESTS after the launch POST: one
    sendText payload ``Session title: [mcwalli-lane]`` serves BOTH the tower's
    tag scan and the matcher's payload-text confirmation. directory is the
    RESOLVED repo path (macOS /var -> /private/var), matching the POSTed
    repo_root under the monitor's realpath confirmation."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO session (id, title, directory, time_created, time_updated,"
            " time_archived) VALUES (?,?,?,?,?,?)",
            (BIRTH_ID, "mcwalli birth", str(Path(repo).resolve()),
             T_BIRTH, T_BIRTH, None))
        conn.execute(
            "INSERT INTO session_input (id, session_id, kind, payload, time_created)"
            " VALUES (?,?,?,?,?)",
            # id NULL -> the next auto rowid: never collides with the world's
            # seeded inputs (which took their own auto rowids).
            (None, BIRTH_ID, "sendText",
             json.dumps({"text": "Session title: [mcwalli-lane]\n"}), T_BIRTH))
        conn.commit()
    finally:
        conn.close()


def mcwalli_tower_json(world: McwalliWorld, path=None) -> str:
    """The tower's JSON config file for this world (consumed by the later
    scripts task; ``pending_launch_path`` is null there by contract)."""
    p = (Path(path) if path is not None
         else Path(world.note_path).parent / "mcwalli_tower.json")
    p.write_text(json.dumps({
        "programs": [{"program": "mcwalli", "tag": "mcwalli-lane",
                      "note_glob": world.note_path,
                      "master_tag": "mcwalli-master"}],
        "repos": [{"name": "mcwalli-repo", "path": world.repo, "host": "gitlab"}],
        "pending_launch_path": None,
    }), encoding="utf-8")
    return str(p)
