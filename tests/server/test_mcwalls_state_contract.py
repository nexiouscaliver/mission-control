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
