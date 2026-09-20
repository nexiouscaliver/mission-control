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
SERVER_KEY = "server"
GENERATED_TS_KEY = "generated_ts"
VERIFY_QUEUE_KEY = "verify_queue"
HUMAN_ACTIONS_KEY = "human_actions"

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


def _int_or_none(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def owed_actions(state: dict) -> list:
    """F-3b (spec §5): legacy producers that write state["owed_actions"] keep
    their meaning verbatim; otherwise the tower's verify_queue/human_actions
    rows are adapted into the keys consumers already read. Missing/non-int
    generated_ts (or non-int finished_ago_s) omits finished_signal_ms — the
    picker then skips the row as invalid-finished-signal (never guess). An
    empty verify_cmd omits the payload entirely -> copied=None (jump only).
    Merges key on ref (the page's merge-card data-row-id) and mirror the
    page-fallback copy payload (mr_link = ref).

    Ordering mirrors the page fallback via the consumer's (finished_signal_ms,
    row_id) sort: verifies oldest-first by finished_ago_s, merges after by
    generated_ts. DOCUMENTED DIVERGENCE (spec §5, accepted): on equal
    finished_ago_s the server tie-breaks by row_id (app.py's sort) while the
    page stable-sorts by served order — harmless because both row ids exist
    on the page."""
    actions = state.get(OWED_ACTIONS_KEY)
    if isinstance(actions, list):  # legacy / StubTower docs: verbatim
        return actions
    server = state.get(SERVER_KEY)
    generated = _int_or_none(server.get(GENERATED_TS_KEY)) if isinstance(server, dict) else None
    adapted = []
    for row in state.get(VERIFY_QUEUE_KEY) or []:
        if not isinstance(row, dict):
            continue
        rid = row.get(ROW_ID_KEY)
        if not isinstance(rid, str) or not rid:
            continue
        action = {OA_ROW_ID_KEY: rid, OA_KIND_KEY: "verify", OA_PARKED_KEY: False}
        ago = _int_or_none(row.get("finished_ago_s"))
        if generated is not None and ago is not None:
            action[OA_FINISHED_SIGNAL_MS_KEY] = (generated - ago) * 1000
        cmd = row.get("verify_cmd")
        if isinstance(cmd, str) and cmd != "":
            action[OA_VERIFY_CMD_KEY] = cmd
        adapted.append(action)
    for row in state.get(HUMAN_ACTIONS_KEY) or []:
        if not isinstance(row, dict) or row.get("kind") != "merge":
            continue
        ref = row.get("ref")
        if not isinstance(ref, str) or not ref:
            continue
        action = {OA_ROW_ID_KEY: ref, OA_KIND_KEY: "merge", OA_PARKED_KEY: False,
                  OA_MR_LINK_KEY: ref}
        if generated is not None:
            action[OA_FINISHED_SIGNAL_MS_KEY] = generated * 1000
        adapted.append(action)
    return adapted


def schema_version(state: dict):
    # Passthrough only — the version is never defaulted or mutated here.
    return state.get(SCHEMA_VERSION_KEY)
