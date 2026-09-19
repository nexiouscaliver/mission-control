"""T-4 goal-state tests: AC-GOAL-1..5, AC-FAIL-5.

Every test drives ``collect_state`` (never the goals module directly) over a
fixture goal tree under ``tmp_path`` built by ``mcwallt_make_goal_tree`` —
fixtures only, zero live-system reads. The minimal world: one program whose
variant-A note row maps one lane to the fixture repo with slug
``mcwallt-slug``; sequential tree mutation + re-collect covers the state
transitions within one test.
"""

import itertools
import json
import os
import shutil

from mc_wall.tower import ProgramConfig, RepoConfig, TowerConfig, collect_state
from mc_wall.tower import contract
from tests.tower.conftest import (mcwallt_make_goal_tree, mcwallt_make_note,
                                  mcwallt_make_session_db)

HEADER_A_LINE = "| id | wave | lane | repo/branch | slug | base | session/MR artifacts | status |"
SEP_LINE = "|---|---|---|---|---|---|---|---|"

_db_seq = itertools.count()  # one fresh session db per collect call (a db file
                             # cannot be created twice in the same tmp_path)


def _goal_collect(tmp_path, repo, slug="mcwallt-slug", rows=None,
                  name="mcwallt_goals_note.md"):
    """collect_state over one program whose note carries ``rows`` (default: one
    variant-A row mapping to ``repo`` with slug ``slug``) and one RepoConfig
    named mcwallt-repo at ``repo``."""
    if rows is None:
        rows = [f"| W1-L1 | W1 | L0 | {repo} | {slug} | n/a | sess_00000000 | launched |"]
    note = mcwallt_make_note(tmp_path, name, [HEADER_A_LINE, SEP_LINE, *rows])
    cfg = TowerConfig(
        db_path=mcwallt_make_session_db(tmp_path, name=f"mcwallt_goals_{next(_db_seq)}.db"),
        programs=(ProgramConfig(program="mcwallt-prog", tag="t", note_glob=note),),
        repos=(RepoConfig(name="mcwallt-repo", path=repo, host="gitlab"),),
    )
    return collect_state(cfg)


def _lane0(state):
    return state["programs"][0]["lanes"][0]


def test_mcwallt_goal_states(tmp_path):
    rows = [
        f"| W1-L1 | W1 | L0 | {tmp_path}/mcwallt_repo | mcwallt-slug | n/a | sess_00000000 | launched |",
        f"| W1-L2 | W1 | L0 | {tmp_path}/mcwallt_repo | n/a | n/a | n/a | forged |",
    ]

    # (a) active: the goal dir exists — even when the slug ALSO sits in the
    # archive INDEX (active wins).
    repo, gd = mcwallt_make_goal_tree(
        tmp_path, queue_lines=["q1"],
        budget={"slug": "mcwallt-slug", "whole_run": 1, "gates": ["python-test"]},
        archive_slugs=("mcwallt-slug", "other-slug"))
    state = _goal_collect(tmp_path, repo, rows=rows)
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    assert lanes["W1-L1"]["goal"] == {"state": "active", "queue_tail": "q1",
                                      "budget": {"slug": "mcwallt-slug", "whole_run": 1,
                                                 "gates": ["python-test"]}}
    assert lanes["W1-L1"]["manifest"] is not None
    assert lanes["W1-L2"]["goal"] is None       # §4.3: slug null -> goal null
    assert lanes["W1-L2"]["manifest"] is None
    assert state["server"]["degraded"] == []    # absence is not failure
    contract.assert_shape(state)

    # (b) archived: dir gone, whole-token INDEX hit.
    shutil.rmtree(gd)
    state = _goal_collect(tmp_path, repo, rows=rows)
    assert _lane0(state)["goal"] == {"state": "archived", "queue_tail": "", "budget": {}}
    assert _lane0(state)["manifest"] is None    # no dir -> manifest null
    assert state["server"]["degraded"] == []

    # (c) absent: existing root, slug not in INDEX, NO degraded entry.
    shutil.rmtree(os.path.join(repo, "regenloop", "local", "orchestrator", "goals", "_archive"))
    state = _goal_collect(tmp_path, repo, rows=rows)
    assert _lane0(state)["goal"] == {"state": "absent", "queue_tail": "", "budget": {}}
    assert _lane0(state)["manifest"] is None
    assert state["server"]["degraded"] == []

    # (d) substring trap: INDEX lists mcwallt-slug-2 ONLY — mcwallt-slug is a
    # substring but never a whole token -> absent.
    idx = os.path.join(repo, "regenloop", "local", "orchestrator", "goals", "_archive")
    os.makedirs(idx)
    with open(os.path.join(idx, "INDEX.md"), "w", encoding="utf-8") as fh:
        fh.write("archived: mcwallt-slug-2\n")
    state = _goal_collect(tmp_path, repo, rows=rows)
    assert _lane0(state)["goal"]["state"] == "absent"
    assert state["server"]["degraded"] == []

    # (e) unreadable (permission-denied) INDEX.md -> no archive match ->
    # absent, never a raise; readable again -> archived (the pair pins that
    # the chmod, not the content, made the difference).
    with open(os.path.join(idx, "INDEX.md"), "w", encoding="utf-8") as fh:
        fh.write("archived: mcwallt-slug\n")
    os.chmod(os.path.join(idx, "INDEX.md"), 0o000)
    try:
        state = _goal_collect(tmp_path, repo, rows=rows)
        assert _lane0(state)["goal"]["state"] == "absent"
    finally:
        os.chmod(os.path.join(idx, "INDEX.md"), 0o644)
    state = _goal_collect(tmp_path, repo, rows=rows)
    assert _lane0(state)["goal"]["state"] == "archived"


def test_mcwallt_goal_queue_tail(tmp_path):
    # "l1\n\nl2  \n\n": last NON-EMPTY line wins.
    repo, gd = mcwallt_make_goal_tree(tmp_path, queue_lines=["l1", "", "l2  ", ""])
    state = _goal_collect(tmp_path, repo)
    assert _lane0(state)["goal"]["queue_tail"] == "l2"

    # File missing -> "".
    os.remove(os.path.join(gd, "queue.md"))
    state = _goal_collect(tmp_path, repo)
    assert _lane0(state)["goal"]["queue_tail"] == ""


def test_mcwallt_goal_budget_passthrough(tmp_path):
    # The verified real shape, passed through AS-IS: equal dict, same keys,
    # no renames, no reordering.
    budget = {"slug": "mcwallt-slug", "whole_run": 3, "gates": ["python-test"]}
    repo, gd = mcwallt_make_goal_tree(tmp_path, budget=budget)
    state = _goal_collect(tmp_path, repo)
    goal = _lane0(state)["goal"]
    assert goal["budget"] == budget
    assert list(goal["budget"].keys()) == ["slug", "whole_run", "gates"]
    assert state["server"]["degraded"] == []

    # Missing file -> {}.
    os.remove(os.path.join(gd, "budget.json"))
    state = _goal_collect(tmp_path, repo)
    assert _lane0(state)["goal"]["budget"] == {}

    # Unparseable -> {} (never a raise, never an invented key).
    with open(os.path.join(gd, "budget.json"), "w", encoding="utf-8") as fh:
        fh.write("{bad")
    state = _goal_collect(tmp_path, repo)
    assert _lane0(state)["goal"]["budget"] == {}
    assert state["server"]["degraded"] == []

    # Valid JSON but not a dict -> {} (the budget shape is object-or-empty).
    with open(os.path.join(gd, "budget.json"), "w", encoding="utf-8") as fh:
        fh.write("[1]")
    state = _goal_collect(tmp_path, repo)
    assert _lane0(state)["goal"]["budget"] == {}


def test_mcwallt_goal_manifest_defaults(tmp_path):
    # Goal dir WITHOUT manifest.json (pre-P4 normal): per-shape defaults, path
    # fields from the filesystem, NO degraded entry.
    repo, gd = mcwallt_make_goal_tree(tmp_path)
    with open(os.path.join(gd, "goal.md"), "w", encoding="utf-8") as fh:
        fh.write("mcwallt goal\n")
    state = _goal_collect(tmp_path, repo)
    assert _lane0(state)["manifest"] == {"path": gd,
                                         "prompt_md": os.path.join(gd, "prompt.md"),
                                         "goal_md": os.path.join(gd, "goal.md"),
                                         "precondition_mrs": [],
                                         "stall_t_hours": 0}
    assert state["server"]["degraded"] == []
    contract.assert_shape(state)

    # Wrong-typed manifest values -> the same per-shape defaults.
    with open(os.path.join(gd, "manifest.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"stall_t_hours": "x", "precondition_mrs": "no"}))
    state = _goal_collect(tmp_path, repo)
    manifest = _lane0(state)["manifest"]
    assert (manifest["stall_t_hours"], manifest["precondition_mrs"]) == (0, [])
    assert state["server"]["degraded"] == []

    # No goal dir -> manifest null.
    shutil.rmtree(gd)
    state = _goal_collect(tmp_path, repo)
    assert _lane0(state)["manifest"] is None


def test_mcwallt_goal_manifest_json_present(tmp_path):
    # Present + parseable: values read through from its keys.
    repo, gd = mcwallt_make_goal_tree(tmp_path, manifest={"stall_t_hours": 6,
                                                          "precondition_mrs": ["!7", "!8"]})
    state = _goal_collect(tmp_path, repo)
    manifest = _lane0(state)["manifest"]
    assert manifest["stall_t_hours"] == 6                 # VALUE equality...
    assert isinstance(manifest["stall_t_hours"], int)     # ...and 6 stays int
    assert manifest["precondition_mrs"] == ["!7", "!8"]
    assert state["server"]["degraded"] == []

    # Float stall_t read through by value.
    with open(os.path.join(gd, "manifest.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"stall_t_hours": 6.5, "precondition_mrs": []}))
    state = _goal_collect(tmp_path, repo)
    assert _lane0(state)["manifest"]["stall_t_hours"] == 6.5

    # Unparseable manifest.json -> per-shape defaults, NO degraded entry.
    with open(os.path.join(gd, "manifest.json"), "w", encoding="utf-8") as fh:
        fh.write("{bad")
    state = _goal_collect(tmp_path, repo)
    manifest = _lane0(state)["manifest"]
    assert (manifest["stall_t_hours"], manifest["precondition_mrs"]) == (0, [])
    assert manifest["path"] == gd
    assert state["server"]["degraded"] == []


def test_mcwallt_failopen_goals_missing(tmp_path):
    # AC-FAIL-5: the configured repo path does not exist -> every lane of that
    # repo keeps goal/manifest null + entry 4, once per repo.
    missing = str(tmp_path / "mcwallt_missing_repo")
    rows = [f"| W1-L1 | W1 | L0 | {missing} | mcwallt-slug | n/a | sess_00000000 | launched |",
            f"| W1-L2 | W1 | L0 | {missing} | mcwallt-slug-2 | n/a | n/a | forged |"]
    state = _goal_collect(tmp_path, missing, rows=rows)
    lanes = state["programs"][0]["lanes"]
    assert all(l["goal"] is None for l in lanes)
    assert all(l["manifest"] is None for l in lanes)
    assert state["server"]["degraded"] == ["goals degraded: mcwallt-repo"]
    contract.assert_shape(state)
