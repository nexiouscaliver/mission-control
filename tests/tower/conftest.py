"""Shared mc_wall tower test helpers (fixtures-only world; zero live reads).

Helpers are prefixed ``mcwallt_`` per the plan's naming rule; later tasks grow
this file (T-4 the goal tree, T-5 the fake command runner, T-6 the composed
fixture world). ``mcwallt_make_db`` builds temp dbs with the live schema shape
(default DELETE journal, closed cleanly — no ``-wal``/``-shm`` side files);
fixture timestamps are ms-magnitude unless a test proves the unit probe with
seconds-magnitude values.
"""

import json
import os
import sqlite3
import threading
import time

import pytest

from mc_wall.tower import NetCache, ProgramConfig, RepoConfig, TowerConfig
from mc_wall.tower import netcache as netcache_module


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


def mcwallt_fake_cmd(handler):
    """Fake ``netcache._run_cmd`` seam (T-5): handler(argv, cwd) -> (rc, stdout);
    counts spawns thread-safely so tests can pin single-flight/backoff with
    ZERO real subprocess/git/glab/gh execution. Returns (fake, calls) where
    calls = {"n": <spawn count>, "argv": [argv, ...]}."""
    calls = {"n": 0, "argv": []}
    lock = threading.Lock()

    def _fake(argv, cwd, timeout_s=10.0):
        with lock:
            calls["n"] += 1
            calls["argv"].append(list(argv))
        rc, out = handler(argv, cwd)
        return rc, out, ""

    return _fake, calls


def mcwallt_settable_clock(start: float):
    """(now_s, set_now) — a controllable injected clock for pinning the EXACT
    TTL/backoff boundaries on both sides (age == ttl is fresh, age > ttl
    expired; elapsed == wait may attempt, elapsed < wait short-circuits)."""
    box = {"v": float(start)}

    def _now():
        return box["v"]

    def _set(v):
        box["v"] = float(v)

    return _now, _set


def mcwallt_make_goal_tree(tmp_path, slug="mcwallt-slug", queue_lines=None, budget=None,
                           manifest=None, archive_slugs=(), root_name="regenloop/local/orchestrator/goals"):
    """Temp regenloop goal tree (spec §4.3 layout) under tmp_path/mcwallt_repo.
    Always creates the goal dir + prompt.md; queue.md/budget.json/manifest.json
    only when the corresponding arg is not None; _archive/INDEX.md (comma-joined
    slug list) only when archive_slugs is non-empty. Returns (repo_path, goal_dir)."""
    repo = tmp_path / "mcwallt_repo"
    root = repo / root_name
    gd = root / slug
    gd.mkdir(parents=True)
    (gd / "prompt.md").write_text("mcwallt prompt\n", encoding="utf-8")
    if queue_lines is not None: (gd / "queue.md").write_text("\n".join(queue_lines) + "\n", encoding="utf-8")
    if budget is not None: (gd / "budget.json").write_text(json.dumps(budget), encoding="utf-8")
    if manifest is not None: (gd / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if archive_slugs:
        idx = root / "_archive"; idx.mkdir()
        (idx / "INDEX.md").write_text("archived: " + ", ".join(archive_slugs) + "\n", encoding="utf-8")
    return str(repo), str(gd)


# --- T-6: the composed fixture world (plan mcwallt_world) -------------------

MCWALLT_WORLD_NOW = 2_000_000_050.0
MCWALLT_HEADER_A = "| id | wave | lane | repo/branch | slug | base | session/MR artifacts | status |"
MCWALLT_SEP = "|---|---|---|---|---|---|---|---|"

# Canonical world identities (full ids in the VERIFIED sess_<uuid4> shape; the
# lane token sess_9a690ab2 is the live-verified 8-hex shorthand of its id).
MCWALLT_WORLD_MASTER = "sess_33333333-3333-4333-8333-333333333333"
MCWALLT_WORLD_LANE = "sess_9a690ab2-cde8-4e9a-bc4e-177fcc68545f"
MCWALLT_WORLD_UNMAPPED = "sess_22222222-2222-4222-8222-222222222222"
MCWALLT_WORLD_SUBAGENT = "sess_subagent_agent_7837f21b-1f0c-4cda-9211-ac529a4cd3c4"


def mcwallt_world_default_handler(argv, cwd):
    """The canonical world's scripted network (tests compose overrides on top
    by dispatching to this for every argv they do not override):

    - git ls-remote loop/mcwall-tower -> rc 0 HIT; any other branch -> rc 0
      miss (a VALUE, not a failure);
    - glab mr view 5 -> !5 open/green; view 7 -> merged; any other ref
      (incl. !8) -> rc 1 lookup FAILURE (the precondition-unknown path);
    - every by-branch list -> rc 0 empty list (a valid empty result).
    """
    if argv[:2] == ["git", "ls-remote"] and argv[3] == "loop/mcwall-tower":
        return (0, "sha000\trefs/heads/loop/mcwall-tower\n")
    if argv[:2] == ["git", "ls-remote"]:
        return (0, "sha111\trefs/heads/other-branch\n")
    if argv[0] == "glab" and argv[2] == "view":
        if argv[3] == "5":
            return (0, json.dumps({"iid": 5, "state": "opened", "title": "mcwallt MR five",
                                   "created_at": "2026-09-19T09:00:00Z",
                                   "head_pipeline": {"status": "success"}}))
        if argv[3] == "7":
            return (0, json.dumps({"iid": 7, "state": "merged", "title": "mcwallt MR seven",
                                   "created_at": "2026-09-19T08:00:00Z"}))
        return (1, "")
    return (0, "[]")


def mcwallt_world(tmp_path, monkeypatch, *, include=("db", "notes", "goals", "net", "launch"),
                  lane1_age_s=400.0, queue_age_s=3600.0, manifest=None,
                  master_tag="secfix-master", rows=None, extra_sessions=(),
                  second_program_done_lane=False, handler=None, name="mcwallt_world"):
    """Full §10 AC-E2E-1 fixture world: 1 program (tag secfix, master_tag
    secfix-master), 1 repo (gitlab, name mcwallt-repo), a variant-A note with
    lanes W1-L1 (done, configured repo+branch, !5 artifacts ref, sess_9a690ab2
    token join), W1-L2 (launched, unconfigured repo), W1-L3 (done, configured
    repo, no token, slug without a goal dir), a goal dir (queue/budget/
    manifest.json stall 6h + preconditions ["!7","!8"]), an ms-magnitude db
    (master + lane + unmapped + subagent sessions), a pending-launch file, and
    a monkeypatched ``_run_cmd`` (default: ``mcwallt_world_default_handler``).
    Returns ``(config, set_now)`` — set_now scripts the injected clock (fixed
    at MCWALLT_WORLD_NOW otherwise).

    Knobs: ``include`` drops source families (absent db/note/goal-tree/launch
    file; excluded "net" installs NO monkeypatch); ``lane1_age_s`` sets the
    lane session's last-active age; ``queue_age_s`` sets queue.md's mtime (the
    §6.1 non-session activity epoch); ``manifest`` overrides manifest.json;
    ``master_tag``/``rows``/``extra_sessions``/``second_program_done_lane``/
    ``handler`` reshape the join/derivation surface. Re-calling with the same
    ``name`` in one test is safe (files are overwritten, the db re-created).
    """
    NOW = MCWALLT_WORLD_NOW

    def ms(age):
        return int((NOW - age) * 1000)

    repo_path = tmp_path / (name + "_repo")
    if "goals" in include:
        gd = repo_path / "regenloop" / "local" / "orchestrator" / "goals" / "mcwallt-slug"
        gd.mkdir(parents=True, exist_ok=True)
        (gd / "prompt.md").write_text("mcwallt prompt\n", encoding="utf-8")
        (gd / "goal.md").write_text("mcwallt goal\n", encoding="utf-8")
        (gd / "queue.md").write_text("q1\n\nq-last\n", encoding="utf-8")
        (gd / "budget.json").write_text(json.dumps(
            {"slug": "mcwallt-slug", "whole_run": 3, "gates": ["python-test"]}),
            encoding="utf-8")
        (gd / "manifest.json").write_text(json.dumps(
            manifest if manifest is not None
            else {"stall_t_hours": 6, "precondition_mrs": ["!7", "!8"]}), encoding="utf-8")
        q_at = NOW - queue_age_s
        os.utime(gd / "queue.md", (q_at, q_at))

    sessions = [
        {"id": MCWALLT_WORLD_MASTER, "title": "mcwallt master", "directory": "/mcwallt/m",
         "time_updated": ms(100), "time_created": ms(100)},
        {"id": MCWALLT_WORLD_LANE, "title": "mcwallt lane", "directory": "/mcwallt/l",
         "time_updated": ms(lane1_age_s), "time_created": ms(lane1_age_s)},
        {"id": MCWALLT_WORLD_UNMAPPED, "title": "mcwallt unmapped", "directory": "/mcwallt/u",
         "time_updated": ms(50), "time_created": ms(50)},
        {"id": MCWALLT_WORLD_SUBAGENT, "title": "mcwallt subagent", "directory": "/mcwallt/s",
         "time_updated": ms(30), "time_created": ms(30)},
    ] + list(extra_sessions)
    if "db" in include:
        db_file = tmp_path / (name + ".db")
        if db_file.exists():
            db_file.unlink()  # same-name rebuilds within one test stay fixture-safe
        db_path = mcwallt_make_db(tmp_path, name=name + ".db", sessions=sessions,
                                  inputs=[mcwallt_tag_input(MCWALLT_WORLD_MASTER,
                                                            ["secfix-master"], ms(100))])
    else:
        db_path = str(tmp_path / (name + "_missing.db"))

    if rows is None:
        rows = [
            f"| W1-L1 | W1 | L1 | {repo_path} loop/mcwall-tower | mcwallt-slug | n/a | !5; sess_9a690ab2 | done |",
            "| W1-L2 | W1 | L2 | plugin cache 1.3.0 (plain lane) | n/a | n/a | n/a | launched |",
            f"| W1-L3 | W1 | L3 | {repo_path} main | mcwallt-slug-3 | n/a | n/a | done |",
        ]
    note_glob = (mcwallt_make_note(tmp_path, name + "_note.md",
                                   ["objective: mcwallt e2e objective",
                                    MCWALLT_HEADER_A, MCWALLT_SEP, *rows])
                 if "notes" in include else str(tmp_path / (name + "_none_*.md")))

    programs = [ProgramConfig(program="secfix", tag="secfix", note_glob=note_glob,
                              master_tag=master_tag)]
    if second_program_done_lane:
        p2_note = mcwallt_make_note(tmp_path, name + "_note2.md", [
            MCWALLT_HEADER_A, MCWALLT_SEP,
            "| A9-L1 | A9 | L1 | n/a | n/a | n/a | n/a | done |"])
        programs.append(ProgramConfig(program="secfix2", tag="secfix2", note_glob=p2_note))

    launch_path = tmp_path / (name + "_launch.json")
    if "launch" in include:
        launch_path.write_text(json.dumps({"slug": "mcwall-tower", "n": 3}), encoding="utf-8")

    if "net" in include:
        fake, _calls = mcwallt_fake_cmd(handler or mcwallt_world_default_handler)
        monkeypatch.setattr(netcache_module, "_run_cmd", fake)

    now_s, set_now = mcwallt_settable_clock(NOW)
    cfg = TowerConfig(
        db_path=db_path,
        programs=tuple(programs),
        repos=(RepoConfig(name="mcwallt-repo", path=str(repo_path), host="gitlab"),),
        pending_launch_path=str(launch_path),
        now_s=now_s,
        network_cache=NetCache())
    return cfg, set_now
