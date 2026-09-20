"""F-3b joined E2E: a REAL tower collect_state behind the running server.

POST /needs-me-now must return the owed action (RED today: the picker reads a
phantom owed_actions key -> action null; the tower emits verify_queue /
human_actions instead). Self-contained by design (merge-disjointness with
loop/mcwall-integrate): no __init__.py, no shared fixtures file, unique
mcwallf_ basename; fixtures built in-file (tower live-schema db +
TowerConfig, patterns from tests/tower/conftest.py); imports only modules
present on the fixes branch."""

import json
import pathlib
import sqlite3
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))  # standalone runs need mc_wall + tests importable

NOTE_HEADER = "| id | wave | lane | repo/branch | slug | base | session/MR artifacts | status |"
NOTE_SEP = "|---|---|---|---|---|---|---|---|"
VERIFY_SID = "sess_aaaa1111bbbb2221"  # hex-only: SESS_RE-safe, prefix-join exact
MR_REF = "!5"


def _mcwallf_make_tower_db(path, sessions=(), inputs=()):
    """Tower LIVE schema (mcwallt_make_db pattern; time_updated/time_archived
    included) — the harness's simplified builder is NOT acceptable here."""
    con = sqlite3.connect(path)
    con.executescript("""
      CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT, title TEXT,
        title_source TEXT, directory TEXT, task_type TEXT, time_updated INTEGER,
        time_created INTEGER, time_archived INTEGER);
      CREATE TABLE session_input (session_id TEXT, kind TEXT, payload TEXT,
        delivery TEXT, status TEXT, time_created INTEGER);""")
    con.executemany("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?)",
                    [(s["id"], None, s.get("title"), None, s.get("directory", ""),
                      None, s["time_updated"], s["time_created"],
                      s.get("time_archived")) for s in sessions])
    con.executemany("INSERT INTO session_input VALUES (?,?,?,?,?,?)",
                    [(i["session_id"], i.get("kind", "sendText"), i["payload"],
                      None, None, i["time_created"]) for i in inputs])
    con.commit()
    con.close()


def _mcwallf_write_note(tmp, rows):
    note = tmp / "mcwallf_note.md"
    note.write_text("\n".join(["objective: mcwallf joined objective",
                               NOTE_HEADER, NOTE_SEP, *rows]) + "\n",
                    encoding="utf-8")
    return str(note)


def _mcwallf_verify_world(tmp_path):
    """One program, NO repos (zero network spawns), one done lane whose session
    is token-joined and aged 400 s >= verify_grace_s (300)."""
    from mc_wall.tower.config import ProgramConfig, TowerConfig

    now = time.time()
    db = tmp_path / "mcwallf_verify.db"
    _mcwallf_make_tower_db(
        db,
        sessions=[{"id": VERIFY_SID, "title": "mcwallf joined lane",
                   "directory": "/mcwallf/l",
                   "time_updated": int((now - 400) * 1000),
                   "time_created": int((now - 400) * 1000)}])
    note = _mcwallf_write_note(
        tmp_path,
        [f"| mcwallf-l1 | W1 | L1 | n/a | n/a | n/a | {VERIFY_SID} | done |"])
    return TowerConfig(
        db_path=str(db),
        programs=(ProgramConfig(program="mcwallf-prog", tag="mcwallf",
                                note_glob=note),))


def _mcwallf_merge_world(tmp_path, monkeypatch):
    """One program + one repo; lane 'launched' (no verify owed) with a !5
    artifacts ref; git/glab scripted through the netcache _run_cmd seam."""
    from mc_wall.tower import netcache as netcache_module
    from mc_wall.tower.config import ProgramConfig, RepoConfig, TowerConfig

    def _fake_run_cmd(argv, cwd, timeout_s=10.0):
        if argv[:2] == ["git", "ls-remote"]:
            return (0, "sha000\trefs/heads/loop/mcwallf-mr\n", "")
        if argv[:3] == ["glab", "mr", "view"]:
            return (0, json.dumps({"iid": 5, "state": "opened",
                                   "title": "mcwallf open MR",
                                   "created_at": "2026-09-19T09:00:00Z",
                                   "head_pipeline": {"status": "success"}}), "")
        return (0, "[]", "")

    monkeypatch.setattr(netcache_module, "_run_cmd", _fake_run_cmd)
    repo = tmp_path / "mcwallf_repo"
    repo.mkdir()
    note = _mcwallf_write_note(
        tmp_path,
        [f"| mcwallf-m1 | W1 | L1 | {repo} loop/mcwallf-mr | n/a | n/a | {MR_REF} | launched |"])
    db = tmp_path / "mcwallf_merge.db"
    _mcwallf_make_tower_db(db)  # empty healthy store: no degraded entries
    return TowerConfig(
        db_path=str(db),
        programs=(ProgramConfig(program="mcwallf-prog", tag="mcwallf",
                                note_glob=note),),
        repos=(RepoConfig(name="mcwallf-repo", path=str(repo), host="gitlab"),))


def test_mcwallf_joined_state_shows_verify(tmp_path):
    from mc_wall.tower.collect import collect_state as tower_collect
    from tests.server.mcwalls_harness import FakeRunner, serve

    cfg = _mcwallf_verify_world(tmp_path)
    with serve(collect_state=lambda: tower_collect(cfg), runner=FakeRunner()) as h:
        r = h.http("GET", f"/{h.token}/state")
        assert r.status == 200
        rows = [x for x in r.json()["verify_queue"] if x["row_id"] == "mcwallf-l1"]
        assert rows, "the board the user sees must show the owed verify"
        assert rows[0]["verify_cmd"] == f"/mission-control-verify {VERIFY_SID}"


def test_mcwallf_joined_needs_me_now_verify(tmp_path):
    from mc_wall.tower.collect import collect_state as tower_collect
    from tests.server.mcwalls_harness import FakeRunner, serve

    cfg = _mcwallf_verify_world(tmp_path)
    runner = FakeRunner()
    with serve(collect_state=lambda: tower_collect(cfg), runner=runner) as h:
        r = h.http("POST", f"/{h.token}/needs-me-now", {})
        assert r.status == 200
        action = r.json()["action"]
        assert action is not None, "F-3b: the picker must see the tower's verify row"
        assert action["kind"] == "verify"
        assert action["row_id"] == "mcwallf-l1"
        assert action["copied"].startswith("/mission-control-verify")
        assert [c for c in runner.calls if c[0] == "copy"] != []   # server copied
        assert [c for c in runner.calls if c[0] == "open_app"] != []  # verify raises ZCode


def test_mcwallf_joined_needs_me_now_merge(tmp_path, monkeypatch):
    from mc_wall.tower.collect import collect_state as tower_collect
    from tests.server.mcwalls_harness import FakeRunner, serve

    cfg = _mcwallf_merge_world(tmp_path, monkeypatch)
    runner = FakeRunner()
    with serve(collect_state=lambda: tower_collect(cfg), runner=runner) as h:
        r = h.http("POST", f"/{h.token}/needs-me-now", {})
        assert r.status == 200
        action = r.json()["action"]
        assert action is not None and action["kind"] == "merge"
        assert action["row_id"] == MR_REF
        assert action["mr_link"] == MR_REF
        assert action["copied"] == MR_REF
