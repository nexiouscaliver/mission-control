"""T4 — state_contract: tower field accessor constants, find_row, owed_actions.

Every mc_wall / harness import is INSIDE the test functions, mirroring
test_mcwalls_boot.py: at RED time each test must FAIL individually (exit 1)
instead of erroring at collection (exit 2).
"""

import pytest


def test_find_row_hits_and_misses():
    from mc_wall.server.state_contract import (
        ROWS_KEY,
        SCHEMA_VERSION_KEY,
        UnknownRowError,
        OWED_ACTIONS_KEY,
        find_row,
        owed_actions,
    )

    # Accessor constants ARE the cross-module contract (T6/T8 consume them).
    assert (SCHEMA_VERSION_KEY, ROWS_KEY, OWED_ACTIONS_KEY) == (
        "schema_version",
        "rows",
        "owed_actions",
    )

    row_a = {"row_id": "a"}
    row_b = {"row_id": "b", "lane_tag": "[x]"}
    state = {SCHEMA_VERSION_KEY: 3, ROWS_KEY: [row_a, row_b]}
    assert find_row(state, "b") is row_b

    with pytest.raises(UnknownRowError):
        find_row(state, "nope")
    # UnknownRowError IS a KeyError; absent keys never raise bare KeyError.
    assert issubclass(UnknownRowError, KeyError)
    with pytest.raises(UnknownRowError):
        find_row({}, "x")

    assert owed_actions({}) == []
    assert owed_actions({OWED_ACTIONS_KEY: None}) == []
    assert owed_actions({OWED_ACTIONS_KEY: "not-a-list"}) == []


def test_missing_optional_fields_never_keyerror():
    from mc_wall.server.state_contract import (
        OWED_ACTIONS_KEY,
        ROW_ID_KEY,
        ROWS_KEY,
        SCHEMA_VERSION_KEY,
        find_row,
        owed_actions,
        schema_version,
    )

    bare_row = {ROW_ID_KEY: "r1"}  # every optional row field absent
    bare_action = {}  # every optional owed-action field absent
    state = {
        SCHEMA_VERSION_KEY: 2,
        ROWS_KEY: [bare_row],
        OWED_ACTIONS_KEY: [bare_action],
    }
    # Raw dicts pass through untouched — no KeyError on any optional field.
    assert owed_actions(state) == [bare_action]
    assert find_row(state, "r1") is bare_row
    assert schema_version(state) == 2
    assert schema_version({}) is None


def test_mcwallf_owed_actions_adapts_verify_row():
    from mc_wall.server import state_contract as sc

    state = {"server": {"generated_ts": 2000},
             "verify_queue": [{"row_id": "r1", "program": "p", "finished_ago_s": 400,
                               "master_hint": "", "verify_cmd": "/mission-control-verify sess_x"}],
             "human_actions": []}
    assert sc.owed_actions(state) == [
        {"row_id": "r1", "kind": "verify", "finished_signal_ms": (2000 - 400) * 1000,
         "verify_cmd": "/mission-control-verify sess_x", "parked": False}]


def test_mcwallf_owed_actions_verify_row_empty_cmd_omits_payload():
    from mc_wall.server import state_contract as sc

    state = {"server": {"generated_ts": 2000},
             "verify_queue": [{"row_id": "r2", "program": "p", "finished_ago_s": 400,
                               "master_hint": "", "verify_cmd": ""}],
             "human_actions": []}
    assert sc.owed_actions(state) == [
        {"row_id": "r2", "kind": "verify", "finished_signal_ms": 1_600_000,
         "parked": False}]
    # choose_owed_action must yield copied=None (jump only, no clipboard write)
    import logging

    from mc_wall.server.app import choose_owed_action

    action = choose_owed_action(state, logging.getLogger("mcwallf-test"))
    assert action is not None and action["copied"] is None and action["row_id"] == "r2"


def test_mcwallf_owed_actions_adapts_merge_row():
    from mc_wall.server import state_contract as sc

    state = {"server": {"generated_ts": 2000}, "verify_queue": [],
             "human_actions": [{"kind": "merge", "ref": "!5", "repo": "r",
                                "repo_host": "gitlab", "title": "t",
                                "pipeline": "green", "ready": True}]}
    assert sc.owed_actions(state) == [
        {"row_id": "!5", "kind": "merge", "finished_signal_ms": 2000 * 1000,
         "mr_link": "!5", "parked": False}]


def test_mcwallf_owed_actions_missing_generated_ts_omits_signal():
    from mc_wall.server import state_contract as sc

    state = {"server": {"degraded": []},
             "verify_queue": [{"row_id": "r1", "program": "p", "finished_ago_s": 400,
                               "master_hint": "", "verify_cmd": "/x"}],
             "human_actions": [{"kind": "merge", "ref": "!5", "repo": "r",
                                "repo_host": "gitlab", "title": "t",
                                "pipeline": "green", "ready": True}]}
    out = sc.owed_actions(state)
    assert out and all("finished_signal_ms" not in a for a in out)


def test_mcwallf_owed_actions_legacy_key_passthrough():
    from mc_wall.server import state_contract as sc

    legacy = [{"row_id": "L1", "kind": "verify", "finished_signal_ms": 5,
               "verify_cmd": "c", "parked": False}]
    assert sc.owed_actions({"owed_actions": legacy, "verify_queue": []}) == legacy
