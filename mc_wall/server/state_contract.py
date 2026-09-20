"""Tower state field contract: accessor constants + defensive lookups.

The only module (with pending.py) that knows the tower state's key names;
L1/T6/T8 consumers import these constants instead of spelling strings.
"""

from __future__ import annotations

SCHEMA_VERSION_KEY = "schema_version"
ROWS_KEY = "rows"
ROW_ID_KEY = "row_id"
LANE_TAG_KEY = "lane_tag"
PROMPT_TEXT_KEY = "prompt_text"
GOAL_TEXT_KEY = "goal_text"
ROW_REPO_ROOT_KEY = "repo_root"  # T8: the goal block renders the row's own repo root
OWED_ACTIONS_KEY = "owed_actions"

OA_ROW_ID_KEY = "row_id"
OA_KIND_KEY = "kind"
OA_FINISHED_SIGNAL_MS_KEY = "finished_signal_ms"
OA_VERIFY_CMD_KEY = "verify_cmd"
OA_MR_LINK_KEY = "mr_link"
OA_PARKED_KEY = "parked"


class UnknownRowError(KeyError):
    """No row with the requested row_id exists in the tower state."""


def find_row(state: dict, row_id: str) -> dict:
    rows = state.get(ROWS_KEY) or []
    for row in rows:
        if row.get(ROW_ID_KEY) == row_id:
            return row
    raise UnknownRowError(row_id)


def owed_actions(state: dict) -> list:
    actions = state.get(OWED_ACTIONS_KEY)
    if not isinstance(actions, list):  # defensive: absent / None / non-list -> []
        return []
    return actions


def schema_version(state: dict):
    # Passthrough only — the version is never defaulted or mutated here.
    return state.get(SCHEMA_VERSION_KEY)
