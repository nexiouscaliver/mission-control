"""T-6 derivation tests: AC-DERIVE-1..4.

Every case drives ``collect_state`` over ``mcwallt_world`` variants (fixtures
only; the world monkeypatches ``netcache._run_cmd``, so zero real subprocess /
git / glab / gh execution and zero network).
"""

import json

from mc_wall.tower import collect_state
from mc_wall.tower import netcache as netcache_module
from tests.tower.conftest import (MCWALLT_WORLD_LANE, mcwallt_fake_cmd, mcwallt_world,
                                  mcwallt_world_default_handler)

# The canonical merge row (everything but `ready`, which varies per case).
_MERGE_ROW = {"kind": "merge", "ref": "!5", "repo": "mcwallt-repo",
              "repo_host": "gitlab", "title": "mcwallt MR five", "pipeline": "green"}


def _lane1(state):
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    return lanes["W1-L1"]


def test_mcwallt_derive_stalled(tmp_path, monkeypatch):
    # AC-DERIVE-1: fires only past stall_t_hours, exact because/last_event.
    # Session last active 7h (25200s) ago with an even older (8h) queue.md:
    # the SESSION is the most recent activity — the max epoch (§6.1) — so it
    # carries the because/last_event numbers.
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, lane1_age_s=25200.0, queue_age_s=28800.0)
    assert _lane1(collect_state(cfg))["stalled"] == {
        "because": "inactive for 25200s > stall_t 6h",
        "last_event": f"session {MCWALLT_WORLD_LANE} at 25200s ago"}

    # queue.md dominating (NEWER than the 8h-idle session, still past the 6h
    # window): last_event names the queue.
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, lane1_age_s=28800.0, queue_age_s=25200.0)
    assert _lane1(collect_state(cfg))["stalled"] == {
        "because": "inactive for 25200s > stall_t 6h",
        "last_event": "queue.md mtime at 25200s ago"}

    # Tie (session epoch == queue mtime): the session wins.
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, lane1_age_s=25200.0, queue_age_s=25200.0)
    stalled = _lane1(collect_state(cfg))["stalled"]
    assert stalled["last_event"] == f"session {MCWALLT_WORLD_LANE} at 25200s ago"

    # No joined session at all: queue.md alone carries the activity epoch.
    repo = tmp_path / "mcwallt_world_repo"
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, queue_age_s=28800.0, rows=[
        f"| W1-L1 | W1 | L1 | {repo} loop/mcwall-tower | mcwallt-slug | n/a | !5 | done |"])
    assert _lane1(collect_state(cfg))["stalled"] == {
        "because": "inactive for 28800s > stall_t 6h",
        "last_event": "queue.md mtime at 28800s ago"}

    # A fresh queue.md (1h) over the same 7h-idle session: the newest activity
    # is recent -> NOT stalled (someone worked the queue an hour ago).
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, lane1_age_s=25200.0, queue_age_s=3600.0)
    assert _lane1(collect_state(cfg))["stalled"] is None

    # Recent activity (400s session / 1h queue vs a 6h window) -> null.
    cfg, _set = mcwallt_world(tmp_path, monkeypatch)
    state = collect_state(cfg)
    assert _lane1(state)["stalled"] is None
    # No activity sources at all (no session, no goal dir -> manifest null):
    # never fabricate inactivity (W1-L3 in the canonical world).
    assert {l["row_id"]: l for l in state["programs"][0]["lanes"]}["W1-L3"]["stalled"] is None

    # Exactly AT the threshold is NOT stalled (strictly greater).
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, lane1_age_s=21600.0, queue_age_s=21600.0)
    assert _lane1(collect_state(cfg))["stalled"] is None

    # stall_t_hours == 0 disables the check entirely.
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, lane1_age_s=25200.0,
                              manifest={"stall_t_hours": 0, "precondition_mrs": []})
    assert _lane1(collect_state(cfg))["stalled"] is None

    # Raw-value formatting: 6.5 renders "6.5" in the because string.
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, lane1_age_s=25200.0, queue_age_s=28800.0,
                              manifest={"stall_t_hours": 6.5, "precondition_mrs": []})
    assert _lane1(collect_state(cfg))["stalled"]["because"] == \
        "inactive for 25200s > stall_t 6.5h"


def test_mcwallt_derive_suggest_verify(tmp_path, monkeypatch):
    # AC-DERIVE-2: done + finished 400s >= grace 300 -> the exact because list.
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, lane1_age_s=400.0)
    assert _lane1(collect_state(cfg))["suggest_verify"] == \
        {"because": ["status=done", "finished_ago_s=400"]}

    # 299s: below the grace window -> null.
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, lane1_age_s=299.0)
    assert _lane1(collect_state(cfg))["suggest_verify"] is None

    # Exactly 300 (the >= boundary) with status partial -> fires.
    repo = tmp_path / "mcwallt_world_repo"
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, lane1_age_s=300.0, rows=[
        f"| W1-L1 | W1 | L1 | {repo} loop/mcwall-tower | mcwallt-slug | n/a | !5; sess_9a690ab2 | partial |"])
    assert _lane1(collect_state(cfg))["suggest_verify"] == \
        {"because": ["status=partial", "finished_ago_s=300"]}

    # launched never fires, whatever the finished age (same 400s session).
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, lane1_age_s=400.0, rows=[
        f"| W1-L1 | W1 | L1 | {repo} loop/mcwall-tower | mcwallt-slug | n/a | !5; sess_9a690ab2 | launched |"])
    assert _lane1(collect_state(cfg))["suggest_verify"] is None

    # No session -> finished_ago_s 0 -> null (W1-L3, done without a token).
    cfg, _set = mcwallt_world(tmp_path, monkeypatch)
    lanes = {l["row_id"]: l for l in collect_state(cfg)["programs"][0]["lanes"]}
    assert lanes["W1-L3"]["suggest_verify"] is None


def test_mcwallt_derive_verify_queue(tmp_path, monkeypatch):
    # AC-DERIVE-3: rows for done/partial lanes, program order then note order.
    cfg, _set = mcwallt_world(tmp_path, monkeypatch)
    state = collect_state(cfg)
    master_id = state["programs"][0]["master"]["session_id"]
    assert state["verify_queue"] == [
        # 1-hit join: finished_ago_s = lane session age; the JOINED FULL db id.
        {"row_id": "W1-L1", "program": "secfix", "finished_ago_s": 400,
         "master_hint": master_id,
         "verify_cmd": f"/mission-control-verify {MCWALLT_WORLD_LANE}"},
        # done with no token parsed: finished 0, master hint still set, "" cmd.
        {"row_id": "W1-L3", "program": "secfix", "finished_ago_s": 0,
         "master_hint": master_id, "verify_cmd": ""},
    ]

    # No master_tag configured -> master_hint "".
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, master_tag=None)
    assert all(r["master_hint"] == "" for r in collect_state(cfg)["verify_queue"])

    # 0-hit join: the row still fires with the RAW sess_ token.
    repo = tmp_path / "mcwallt_world_repo"
    no_preconds = {"stall_t_hours": 6, "precondition_mrs": []}
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, manifest=no_preconds, rows=[
        f"| W1-L1 | W1 | L1 | {repo} loop/mcwall-tower | mcwallt-slug | n/a | !5; sess_0dddaaa0 | done |"])
    state = collect_state(cfg)
    assert state["verify_queue"][0]["finished_ago_s"] == 0
    assert state["verify_queue"][0]["verify_cmd"] == "/mission-control-verify sess_0dddaaa0"
    assert state["server"]["degraded"] == []  # 0 hits is NOT degradation

    # Ambiguous join (entry 9): the row still fires with the raw token.
    extra = [{"id": "sess_a0000001-1111-4111-8111-111111111111", "title": "a1",
              "directory": "/mcwallt/a1", "time_updated": 0, "time_created": 0},
             {"id": "sess_a0000001-2222-4222-8222-222222222222", "title": "a2",
              "directory": "/mcwallt/a2", "time_updated": 0, "time_created": 0}]
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, extra_sessions=extra, rows=[
        f"| W1-L1 | W1 | L1 | {repo} loop/mcwall-tower | mcwallt-slug | n/a | !5; sess_a0000001 | done |"])
    state = collect_state(cfg)
    assert state["verify_queue"][0]["verify_cmd"] == "/mission-control-verify sess_a0000001"
    assert "join ambiguous session: sess_a0000001" in state["server"]["degraded"]

    # Program-config order beats note/row_id order: program 2's lane (row_id
    # "A9-L1", lexicographically FIRST) comes after program 1's rows.
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, second_program_done_lane=True)
    state = collect_state(cfg)
    assert [r["row_id"] for r in state["verify_queue"]] == ["W1-L1", "W1-L3", "A9-L1"]
    assert [r["program"] for r in state["verify_queue"]] == ["secfix", "secfix", "secfix2"]


def _override(views):
    """A world handler overriding specific ``glab mr view`` refs and deferring
    everything else to the canonical world handler."""
    def handler(argv, cwd):
        if argv[0] == "glab" and argv[2] == "view" and argv[3] in views:
            return views[argv[3]]
        return mcwallt_world_default_handler(argv, cwd)
    return handler


def test_mcwallt_derive_human_actions_ready(tmp_path, monkeypatch):
    # AC-DERIVE-4: canonical world — !5 open/green, !7 merged, !8 UNKNOWN ->
    # the row is KEPT with ready=False and exactly one entry 11 (!8).
    cfg, _set = mcwallt_world(tmp_path, monkeypatch)
    state = collect_state(cfg)
    assert state["human_actions"] == [dict(_MERGE_ROW, ready=False)]
    assert state["server"]["degraded"] == ["precondition state unknown: !8"]

    # No preconditions (empty list): green alone -> ready True.
    cfg, _set = mcwallt_world(tmp_path, monkeypatch,
                              manifest={"stall_t_hours": 6, "precondition_mrs": []})
    state = collect_state(cfg)
    assert state["human_actions"] == [dict(_MERGE_ROW, ready=True)]
    assert state["server"]["degraded"] == []

    # manifest null (slug without a goal dir): no preconditions -> green suffices.
    repo = tmp_path / "mcwallt_world_repo"
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, rows=[
        f"| W1-L1 | W1 | L1 | {repo} loop/mcwall-tower | n/a | n/a | !5 | done |"])
    state = collect_state(cfg)
    assert state["human_actions"] == [dict(_MERGE_ROW, ready=True)]
    assert state["server"]["degraded"] == []

    # Precondition KNOWN and not merged (!7 open): ready False, NO unknown entry.
    opened7 = (0, json.dumps({"iid": 7, "state": "opened", "title": "mcwallt MR seven",
                              "created_at": "2026-09-19T08:00:00Z"}))
    merged8 = (0, json.dumps({"iid": 8, "state": "merged", "title": "mcwallt MR eight",
                              "created_at": "2026-09-19T08:30:00Z"}))
    cfg, _set = mcwallt_world(tmp_path, monkeypatch,
                              handler=_override({"7": opened7, "8": merged8}))
    state = collect_state(cfg)
    assert state["human_actions"] == [dict(_MERGE_ROW, ready=False)]
    assert state["server"]["degraded"] == []

    # Pipeline not green (no preconditions): row kept, ready False, no entry.
    failed5 = (0, json.dumps({"iid": 5, "state": "opened", "title": "mcwallt MR five",
                              "created_at": "2026-09-19T09:00:00Z",
                              "head_pipeline": {"status": "failed"}}))
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, handler=_override({"5": failed5}),
                              manifest={"stall_t_hours": 6, "precondition_mrs": []})
    state = collect_state(cfg)
    assert state["human_actions"] == [dict(_MERGE_ROW, pipeline="failed", ready=False)]
    assert state["server"]["degraded"] == []

    # The lane's OWN MR lookup failed -> NO row for that lane (+ entry 6).
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, handler=_override({"5": (1, "")}))
    state = collect_state(cfg)
    assert state["human_actions"] == []
    assert state["server"]["degraded"] == ["network degraded: mr mcwallt-repo"]

    # Non-open (merged) own MR -> no row, and no precondition lookups either
    # (entry 11 fires only for lanes that produce a merge row).
    merged5 = (0, json.dumps({"iid": 5, "state": "merged", "title": "mcwallt MR five",
                              "created_at": "2026-09-19T09:00:00Z"}))
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, handler=_override({"5": merged5}))
    state = collect_state(cfg)
    assert state["human_actions"] == []
    assert state["server"]["degraded"] == []

    # P3 hardening pin: an off-grammar precondition ref from manifest.json
    # ("--sort=x" would arrive in argv as a flag) is UNKNOWN — never spawned,
    # entry 11, row kept with ready=False.
    cfg, _set = mcwallt_world(tmp_path, monkeypatch,
                              manifest={"stall_t_hours": 6,
                                        "precondition_mrs": ["--sort=x"]})
    fake, calls = mcwallt_fake_cmd(mcwallt_world_default_handler)
    monkeypatch.setattr(netcache_module, "_run_cmd", fake)
    state = collect_state(cfg)
    assert state["human_actions"] == [dict(_MERGE_ROW, ready=False)]
    assert state["server"]["degraded"] == ["precondition state unknown: --sort=x"]
    assert all("--sort=x" not in argv for argv in calls["argv"])
