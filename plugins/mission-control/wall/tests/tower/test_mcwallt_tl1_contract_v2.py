"""W2-L1 contract v2 tests (shared contract pinned 2026-10-06): prompt-log
header variant C (9-cell deps LAST), per-defect fail-visible parse errors
naming note path + line + defect + row_id, the fix/<name> branch form, the
declared-path objective check, schema_version 2 lane/program/state keys,
the grace/verified/unknown-age verify_queue (decision D15 reversal), the
needs_me aggregation, and the stall_t_hours default 6.

Every case is fixture-only (zero live reads); the 8-cell A/B corpus pins
live in test_mcwallt_notes.py and are deliberately NOT re-asserted here.
"""

import json

import pytest

from mc_wall.tower import ProgramConfig, RepoConfig, TowerConfig, collect_state
from mc_wall.tower import contract, derive, goals, notes
from tests.tower.conftest import (MCWALLT_WORLD_LANE, mcwallt_make_db,
                                  mcwallt_make_note, mcwallt_make_session_db,
                                  mcwallt_world)

HEADER_C_LINE = ("| id | wave | lane | repo/branch | slug | base "
                 "| session/MR artifacts | status | deps |")
SEP_C_LINE = "|---|---|---|---|---|---|---|---|---|"


# --- item 4: variant C header + deps parsing (EXPECT-1) ----------------------

def test_tl1_notes_variant_c_header_and_deps(tmp_path):
    parsed = notes.parse_note("\n".join([
        "objective: tl1 variant c world",
        HEADER_C_LINE, SEP_C_LINE,
        # deps cell: row_ids separated by spaces
        "| W2-L2 | W2 | L2 web | ~/repos/mc loop/wall-web | wall-web | b5 | n/a | forged | W1-L0 W2-L1 |",
        # deps cell: commas (with stray whitespace)
        "| W2-L1 | W2 | L1 tower | ~/repos/mc loop/wall-tower | wall-tower | b5 | sess_00000001 | forged | W1-L0, W2-L2 |",
        # deps cell: em-dash none
        "| W3-L3 | W3 | L3 skill | ~/repos/mc main | wall-skill | b5 | n/a | pending-note | — |",
    ]))
    assert parsed.header_found is True
    assert parsed.skipped == 0
    by_id = {r.row_id: r for r in parsed.rows}
    assert by_id["W2-L2"].deps == ("W1-L0", "W2-L1")
    assert by_id["W2-L1"].deps == ("W1-L0", "W2-L2")
    assert by_id["W3-L3"].deps == ()
    # The A-column grammar is unchanged under C: repo/branch/slug/status parse
    # exactly as they do under variant A.
    assert (by_id["W2-L1"].repo_token, by_id["W2-L1"].branch,
            by_id["W2-L1"].slug, by_id["W2-L1"].sess_token,
            by_id["W2-L1"].status_parsed) == \
        ("~/repos/mc", "loop/wall-tower", "wall-tower", "sess_00000001", "forged")
    assert by_id["W3-L3"].status_parsed == "UNPARSED"  # off-vocab stays off-vocab


def test_tl1_notes_deps_null_rules():
    parsed = notes.parse_note("\n".join([
        HEADER_C_LINE, SEP_C_LINE,
        "| C-1 | W1 | L | n/a | n/a | n/a | n/a | done | — |",
        "| C-2 | W1 | L | n/a | n/a | n/a | n/a | done | n/a |",
        "| C-3 | W1 | L | n/a | n/a | n/a | n/a | done |  |",
    ]))
    assert [r.deps for r in parsed.rows] == [(), (), ()]


def test_tl1_notes_variant_c_collect_threads_deps(tmp_path):
    note = mcwallt_make_note(tmp_path, "tl1_variant_c.md", [
        "objective: tl1 deps payload",
        HEADER_C_LINE, SEP_C_LINE,
        f"| W2-L1 | W2 | L1 tower | {tmp_path} loop/wall-tower | tl1-slug | b5 | n/a | forged | W1-L0 |",
    ])
    cfg = TowerConfig(
        db_path=mcwallt_make_session_db(tmp_path),
        programs=(ProgramConfig(program="tl1", tag="t", note_glob=note),),
    )
    state = collect_state(cfg)
    lane = state["programs"][0]["lanes"][0]
    assert lane["deps"] == ["W1-L0"]
    assert lane["verified"] is False
    contract.assert_shape(state)


# --- item 7: _BRANCH_RE accepts fix/<name> ------------------------------------

def test_tl1_notes_branch_fix_form():
    va = notes.VARIANT_A
    assert notes.parse_repo_branch("~/repos/mc fix/unmapped-recency-order", va) == \
        ("~/repos/mc", "fix/unmapped-recency-order")
    assert notes.parse_repo_branch("fix/solo-branch", va) == (None, "fix/solo-branch")
    # loop/ and main/master keep parsing; junk tokens keep being ignored.
    assert notes.parse_repo_branch("~/repos/mc loop/x main", va) == ("~/repos/mc", "loop/x")
    assert notes.parse_repo_branch("~/repos/mc fix/Bad_Slash!", va) == ("~/repos/mc", None)
    # General namespaced form (hsp resurrection, 2026-10-07): real fleets use
    # backport/, alarm-fixes/, hsp/, release/ ... — all lowercase-kebab
    # namespaces parse; uppercase namespaces and bare branch names do not.
    assert notes.parse_repo_branch(
        "~/work/regenai-repo/cleo backport/alarm-fixes-mr-C-main", va) == \
        ("~/work/regenai-repo/cleo", "backport/alarm-fixes-mr-C-main")
    assert notes.parse_repo_branch(
        "~/cleo alarm-fixes/mr-B (+ mr-C backports)", va) == \
        ("~/cleo", "alarm-fixes/mr-B")
    assert notes.parse_repo_branch("~/cleo hsp/phase1-ref", va) == \
        ("~/cleo", "hsp/phase1-ref")
    assert notes.parse_repo_branch("~/repos/mc release/v1.4.0", va) == \
        ("~/repos/mc", "release/v1.4.0")
    assert notes.parse_repo_branch("~/repos/mc Loop/x", va) == ("~/repos/mc", None)
    assert notes.parse_repo_branch("~/repos/mc bare-branch-name", va) == \
        ("~/repos/mc", None)


# --- item 2: per-defect fail-visible parse errors (EXPECT-2) ------------------

def test_tl1_notes_parse_defects_off_grammar(tmp_path):
    text = "\n".join([
        "**Objective:** bolded objective is off-grammar",   # line 1: near-miss
        HEADER_C_LINE,                                      # line 2
        SEP_C_LINE,                                         # line 3 (furniture)
        "| W2-L1 | W2 | L1 | n/a | n/a | n/a | sess_00000001 | forged | — |",   # line 4 ok
        "| W2-S | W2 | short | n/a | n/a | n/a | done |",   # line 5: 7 cells < 9
        "| W2-B | W2 | L | n/a | n/a | n/a | sess_00000002 | launched-ish | — |",  # line 6: bad status
        "|  | W2 | L | n/a | n/a | n/a | n/a | done | — |",  # line 7: empty id
    ])
    parsed = notes.parse_note(text, note_path="/vault/tl1_off.md")
    defects = {(d["line"], d["row_id"]): d for d in parsed.defects}
    assert set(defects) == {(1, None), (5, "W2-S"), (6, "W2-B"), (7, None)}
    assert all(d["note_path"] == "/vault/tl1_off.md" for d in parsed.defects)
    # The row that CAN parse still renders (no vanishing lanes).
    assert [r.row_id for r in parsed.rows] == ["W2-L1", "W2-B"]
    assert parsed.skipped == 2


def test_tl1_notes_parse_defects_objective_missing():
    parsed = notes.parse_note("\n".join([
        HEADER_C_LINE, SEP_C_LINE,
        "| W2-L1 | W2 | L1 | n/a | n/a | n/a | n/a | forged | — |",
    ]), note_path="/vault/tl1_noobj.md")
    assert parsed.objective == ""
    obj_defects = [d for d in parsed.defects if "objective" in d["defect"]]
    assert len(obj_defects) == 1
    assert obj_defects[0]["note_path"] == "/vault/tl1_noobj.md"
    assert obj_defects[0]["row_id"] is None


def test_tl1_notes_parse_defects_none_when_healthy():
    parsed = notes.parse_note("\n".join([
        "objective: healthy note",
        HEADER_C_LINE, SEP_C_LINE,
        "| W2-L1 | W2 | L1 | n/a | n/a | n/a | sess_00000001 | forged | — |",
    ]), note_path="/vault/tl1_ok.md")
    assert parsed.defects == []
    assert parsed.skipped == 0


def test_tl1_collect_defects_and_degraded_line_gain_path(tmp_path):
    note = mcwallt_make_note(tmp_path, "tl1_offgrammar.md", [
        "**Objective:** bolded",                              # defect line 1
        HEADER_C_LINE, SEP_C_LINE,
        "| W2-S | W2 | short | n/a | n/a | n/a | done |",     # skipped line 4
    ])
    cfg = TowerConfig(
        db_path=mcwallt_make_session_db(tmp_path),
        programs=(ProgramConfig(program="tl1", tag="t", note_glob=note),),
    )
    state = collect_state(cfg)
    prog = state["programs"][0]
    assert prog["parse_defects"] == [
        {"note_path": note, "line": 1, "defect": "objective not parsed (bolded/malformed)",
         "row_id": None},
        {"note_path": note, "line": 4, "defect": "row skipped: 7 cells < 9", "row_id": "W2-S"},
    ]
    # entry 10 keeps the count AND now names the note path.
    assert state["server"]["degraded"] == [f"note rows skipped: 1 ({note})"]
    contract.assert_shape(state)


# --- items 1/10: lane/program/state contract keys, schema 2 (EXPECT-4) --------

def test_tl1_contract_example_is_schema_2_and_passes_shape():
    # v3 re-pin (W4-L4 + W5-L5, 2026-10-07): schema 2 -> 3 with the additive
    # root keys merges + sessions_orphaned + parse_defects; every v2 key and
    # shape below is unchanged.
    assert contract.CONTRACT_EXAMPLE["schema_version"] == 3
    contract.assert_shape(contract.CONTRACT_EXAMPLE)
    lane = contract.CONTRACT_EXAMPLE["programs"][0]["lanes"][0]
    assert lane["deps"] == ["W2-L4"] and lane["verified"] is False \
        and lane["verify_due"] is None
    assert contract.CONTRACT_EXAMPLE["programs"][0]["parse_defects"] == []
    assert contract.CONTRACT_EXAMPLE["needs_me"] == [
        {"kind": "merge-ready", "row_id": "", "program": "", "action": "",
         "deep_link": None}]
    assert "needs_me" in contract.SHAPES["state"]
    assert contract.SHAPES["verify_queue_row"]["finished_ago_s"] == "int|None"


def test_tl1_lane_shell_carries_v2_keys():
    lane = contract.lane_shell("W2-L1", "mc", "loop/x", "slug", "forged", "forged",
                               deps=("W1-L0",), verified=True)
    assert lane["deps"] == ["W1-L0"]
    assert lane["verified"] is True
    assert lane["verify_due"] is None
    assert contract.lane_shell("R", None, None, None, "done", "done")["deps"] == []


def test_tl1_state_schema2_and_needs_me_top_level(tmp_path, monkeypatch):
    cfg, _set = mcwallt_world(tmp_path, monkeypatch)
    state = collect_state(cfg)
    assert state["schema_version"] == 3  # v3 (W4-L4 +merges, W5-L5 wall-honesty)
    assert isinstance(state["needs_me"], list)
    contract.assert_shape(state)


# --- item 5: verify_queue grace + verified-clear + unknown-age (EXPECT-3) -----

def test_tl1_derive_verify_queue_filters():
    views = [
        # done, 400s known age, NOT verified -> row kept (400 >= 300)
        {"row_id": "V-1", "program": "p", "status_parsed": "done",
         "finished_ago_s": 400, "master_hint": "", "verify_id": "sess_aaaa0000",
         "verified": False},
        # done, 299s known age (within grace) -> NO row
        {"row_id": "V-2", "program": "p", "status_parsed": "done",
         "finished_ago_s": 299, "master_hint": "", "verify_id": "sess_aaaa0001",
         "verified": False},
        # done, known age, verified via verify:ok -> NO row (D15 reversal)
        {"row_id": "V-3", "program": "p", "status_parsed": "done",
         "finished_ago_s": 9999, "master_hint": "", "verify_id": "sess_aaaa0002",
         "verified": True},
        # done, NO session -> due-with-unknown-age, finished_ago_s None
        {"row_id": "V-4", "program": "p", "status_parsed": "partial",
         "finished_ago_s": None, "master_hint": "", "verify_id": "",
         "verified": False},
        # launched -> never in the queue
        {"row_id": "V-5", "program": "p", "status_parsed": "launched",
         "finished_ago_s": 9999, "master_hint": "", "verify_id": "sess_aaaa0004",
         "verified": False},
    ]
    rows = derive.verify_queue_rows(views, grace_s=300)
    assert [r["row_id"] for r in rows] == ["V-1", "V-4"]
    assert rows[0]["finished_ago_s"] == 400
    assert rows[0]["verify_cmd"] == "/mission-control-verify sess_aaaa0000"
    assert rows[1]["finished_ago_s"] is None
    assert rows[1]["verify_cmd"] == ""


def test_tl1_collect_verify_queue_end_to_end(tmp_path, monkeypatch):
    # W1-L1: done, joined session aged 400s >= grace, verify:ok ABSENT -> due.
    cfg, _set = mcwallt_world(tmp_path, monkeypatch)
    state = collect_state(cfg)
    assert [r["row_id"] for r in state["verify_queue"]] == ["W1-L1", "W1-L3"]
    w1 = {l["row_id"]: l for l in state["programs"][0]["lanes"]}["W1-L1"]
    assert w1["verify_due"] == {"because": ["status=done", "finished_ago_s=400"]}
    assert w1["verified"] is False
    # W1-L3: done, no session -> unknown-age row + verify_due says unknown.
    w3 = {l["row_id"]: l for l in state["programs"][0]["lanes"]}["W1-L3"]
    assert w3["verify_due"] == {"because": ["status=done", "finished_ago_s=unknown"]}
    queue_w3 = [r for r in state["verify_queue"] if r["row_id"] == "W1-L3"][0]
    assert queue_w3["finished_ago_s"] is None
    # W1-L2 launched: never due.
    w2 = {l["row_id"]: l for l in state["programs"][0]["lanes"]}["W1-L2"]
    assert w2["verify_due"] is None


def test_tl1_collect_verify_token_clears_queue_row(tmp_path, monkeypatch):
    repo = tmp_path / "mcwallt_world_repo"
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, rows=[
        f"| W1-L1 | W1 | L1 | {repo} loop/mcwall-tower | mcwallt-slug | n/a"
        f" | !5; sess_9a690ab2 verify:ok | done |"])
    state = collect_state(cfg)
    assert state["verify_queue"] == []
    lane = state["programs"][0]["lanes"][0]
    assert lane["verified"] is True
    assert lane["verify_due"] is None
    assert lane["suggest_verify"] is None


def test_tl1_collect_within_grace_not_due(tmp_path, monkeypatch):
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, lane1_age_s=299.0)
    state = collect_state(cfg)
    assert [r["row_id"] for r in state["verify_queue"]] == ["W1-L3"]  # only unknown-age
    lane = {l["row_id"]: l for l in state["programs"][0]["lanes"]}["W1-L1"]
    assert lane["verify_due"] is None


# --- item 3: needs_me aggregation ---------------------------------------------

def _mr(open_state="open", pipeline="green"):
    return {"ref": "!5", "repo_host": "gitlab", "state": open_state,
            "title": "t", "pipeline": pipeline, "age_s": 10}


def test_tl1_derive_needs_me_rows():
    merge_ready = {"row_id": "M-1", "program": "p", "repo": "mc", "repo_host": "gitlab",
                   "branch": "loop/x", "mr": _mr(), "mr_failed": False,
                   "manifest": {"precondition_mrs": []}, "precondition_results": {}}
    merge_not_ready = dict(merge_ready, row_id="M-2", mr=_mr(pipeline="failed"))
    verify_rows = [{"row_id": "V-1", "program": "p", "finished_ago_s": 400,
                    "master_hint": "", "verify_cmd": "/mission-control-verify sess_x"},
                   {"row_id": "V-4", "program": "p", "finished_ago_s": None,
                    "master_hint": "", "verify_cmd": ""}]
    stalled = [{"row_id": "S-1", "program": "p", "repo": "mc", "branch": "loop/y"},
               {"row_id": "S-2", "program": "p", "repo": None, "branch": None}]
    out = derive.needs_me_rows([merge_ready, merge_not_ready], verify_rows, stalled)
    assert out == [
        {"kind": "merge-ready", "row_id": "M-1", "program": "p",
         "action": "merge !5 on mc/loop/x (gitlab)", "deep_link": None},
        {"kind": "verify-due", "row_id": "V-1", "program": "p",
         "action": "/mission-control-verify sess_x", "deep_link": None},
        {"kind": "verify-due", "row_id": "V-4", "program": "p",
         "action": "/mission-control-verify", "deep_link": None},
        {"kind": "stalled", "row_id": "S-1", "program": "p",
         "action": "mc/loop/y", "deep_link": None},
        {"kind": "stalled", "row_id": "S-2", "program": "p",
         "action": "S-2", "deep_link": None},
    ]


def test_tl1_collect_needs_me_end_to_end(tmp_path, monkeypatch):
    # Canonical world: !5 open but !8 precondition UNKNOWN -> merge row NOT
    # ready -> NO merge-ready entry; W1-L1 + W1-L3 verify-due; nobody stalled
    # at 400s/1h against 6h.
    cfg, _set = mcwallt_world(tmp_path, monkeypatch)
    state = collect_state(cfg)
    assert state["needs_me"] == [
        {"kind": "verify-due", "row_id": "W1-L1", "program": "secfix",
         "action": f"/mission-control-verify {MCWALLT_WORLD_LANE}",
         "deep_link": None},
        {"kind": "verify-due", "row_id": "W1-L3", "program": "secfix",
         "action": "/mission-control-verify", "deep_link": None},
    ]
    # All-green merge (no preconditions) -> merge-ready first; 7h-idle lane
    # (session AND queue.md both past the 6h stall bound) -> stalled entry
    # naming repo/branch.
    repo = tmp_path / "mcwallt_world_repo"
    # Since the sd1 status guard (W0-O1 incident) a DONE lane never stalls —
    # the stalled entry rides W1-L4 (launched, own 7h-idle session, same
    # goal dir slug so the manifest's stall bound applies).
    rows4 = [f"| W1-L1 | W1 | L1 | {repo} loop/mcwall-tower | mcwallt-slug | n/a | !5; sess_9a690ab2-cde8-4e9a-bc4e-177fcc68545f | done |",
             "| W1-L2 | W1 | L2 | plugin cache 1.3.0 (plain lane) | n/a | n/a | n/a | launched |",
             f"| W1-L3 | W1 | L3 | {repo} main | mcwallt-slug-3 | n/a | n/a | done |",
             f"| W1-L4 | W1 | L4 | {repo} loop/mcwallt-fixes | mcwallt-slug | n/a | !9; sess_5ca1e000-dead-4bee-a1de-5ca1e000dead | launched |"]
    stale_sess = {"id": "sess_5ca1e000-dead-4bee-a1de-5ca1e000dead", "title": "mcwallt stalled lane",
                  "directory": "/mcwallt/s4", "time_updated": 0, "time_created": 0}
    cfg, _set = mcwallt_world(
        tmp_path, monkeypatch, lane1_age_s=25200.0, queue_age_s=28800.0,
        manifest={"stall_t_hours": 6, "precondition_mrs": []},
        rows=rows4, extra_sessions=[stale_sess])
    state = collect_state(cfg)
    kinds = [(e["kind"], e["row_id"]) for e in state["needs_me"]]
    assert kinds == [("merge-ready", "W1-L1"), ("verify-due", "W1-L1"),
                     ("verify-due", "W1-L3"), ("stalled", "W1-L4")]
    assert state["needs_me"][0]["action"] == \
        "merge !5 on mcwallt-repo/loop/mcwall-tower (gitlab)"
    assert state["needs_me"][3]["action"] == "mcwallt-repo/loop/mcwallt-fixes"


# --- item 6: stall_t_hours defaults to 6 when the manifest carries none -------

def test_tl1_goals_stall_default_6_without_manifest(tmp_path, monkeypatch):
    # A goal dir with NO manifest.json: stall default 6 -> a 7h-idle session
    # fires the stalled derivation (v1 kept stall_t_hours 0 = never stalled).
    repo = tmp_path / "tl1_nomanifest_repo"
    gd = repo / "regenloop/local/orchestrator/goals/tl1-slug"
    gd.mkdir(parents=True)
    (gd / "prompt.md").write_text("p\n", encoding="utf-8")
    manifest = goals.read_manifest(str(gd))
    assert manifest["stall_t_hours"] == 6

    NOW = 2_000_000_000.0
    db = mcwallt_make_db(tmp_path, sessions=[
        {"id": "sess_11111111-2222-4222-8222-222222222222", "title": "t",
         "directory": "/tl1", "time_updated": int((NOW - 25200) * 1000),
         "time_created": int((NOW - 25200) * 1000)}])
    note = mcwallt_make_note(tmp_path, "tl1_default_stall.md", [
        "objective: default stall",
        "| id | wave | lane | repo/branch | slug | base | session/MR artifacts | status |",
        "|---|---|---|---|---|---|---|---|",
        f"| W2-L1 | W2 | L1 | {repo} loop/x | tl1-slug | n/a | sess_11111111 | launched |",
    ])
    from tests.tower.conftest import mcwallt_clock
    cfg = TowerConfig(db_path=db, now_s=mcwallt_clock(NOW),
                      programs=(ProgramConfig(program="tl1", tag="t", note_glob=note),),
                      repos=(RepoConfig(name="tl1-repo", path=str(repo), host="gitlab"),))
    # No network: the lane has no branch-push interest for this assertion.
    import mc_wall.tower.collect as collect_module
    monkeypatch.setattr(collect_module, "_read_signals", lambda *a: None)
    monkeypatch.setattr(collect_module, "_read_merges", lambda *a: ([], 0))  # W4-L4 hermeticity
    state = collect_state(cfg)
    lane = state["programs"][0]["lanes"][0]
    assert lane["stalled"] is not None
    assert lane["stalled"]["because"] == "inactive for 25200s > stall_t 6h"


def test_tl1_goals_stall_default_6_wrong_typed(tmp_path):
    repo, gd = goals.goal_root(str(tmp_path)), None
    root = tmp_path / "r" / "regenloop" / "local" / "orchestrator" / "goals"
    gd = root / "tl1-slug"
    gd.mkdir(parents=True)
    (gd / "manifest.json").write_text(json.dumps({"stall_t_hours": "x"}),
                                      encoding="utf-8")
    assert goals.read_manifest(str(gd))["stall_t_hours"] == 6
    # An explicit 0 still disables (honored verbatim).
    (gd / "manifest.json").write_text(json.dumps({"stall_t_hours": 0}),
                                      encoding="utf-8")
    assert goals.read_manifest(str(gd))["stall_t_hours"] == 0


# --- item 9: find_row reads the tower document's real shape (EXPECT-6) --------

def test_tl1_find_row_resolves_tower_doc():
    from mc_wall.server.state_contract import UnknownRowError, find_row
    lane = {"row_id": "W2-L1", "status_parsed": "forged"}
    state = {"schema_version": 2,
             "programs": [{"program": "p", "lanes": [lane]},
                          {"program": "q", "lanes": [{"row_id": "W3-L3"}]}]}
    assert find_row(state, "W2-L1") is lane
    assert find_row(state, "W3-L3")["row_id"] == "W3-L3"
    with pytest.raises(UnknownRowError):
        find_row(state, "nope")
    with pytest.raises(UnknownRowError):
        find_row({}, "x")


def test_tl1_find_row_rows_bridge_still_wins_when_present():
    # The documented rows bridge (StubTower/legacy producers) keeps priority;
    # the tower shape is the production fallback that fixes the B3 404.
    from mc_wall.server.state_contract import find_row
    bridge = {"row_id": "W1-L1", "goal_text": "BRIDGE"}
    lane = {"row_id": "W1-L1"}
    state = {"programs": [{"program": "p", "lanes": [lane]}], "rows": [bridge]}
    assert find_row(state, "W1-L1") is bridge
