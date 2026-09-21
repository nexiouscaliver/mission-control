"""Frozen-contract compliance (spec §9): the verbatim example, the declarative
shape spec checked by ``assert_shape``, and the zero-value builders.

``assert_shape`` accepts ``None`` for any nullable field (a nullable key must
be PRESENT with a null value — an omitted key is a violation) and leaves
``launch_pending`` type-unchecked (any JSON value is contract-compliant).
``bool`` is NOT an ``int`` for these checks.
"""

import json

STR, INT, NULSTR = str, int, "str|None"

# The §9 example, verbatim (json.loads preserves key insertion order).
CONTRACT_EXAMPLE = json.loads(r'''{"schema_version":1,"server":{"uptime_s":0,"generated_ts":0,"degraded":[],"banner":null},
 "programs":[{"program":"secfix","note_path":"","note_mtime":0,"objective":"",
   "master":{"session_id":null,"title":null,"last_active_ago_s":null},
   "lanes":[{"row_id":"W2-L5","repo":"cleo","branch":null,"slug":null,
     "status_note":"launched","status_parsed":"launched",
     "manifest":null,"session":null,"goal":null,
     "signals":{"pushed":null,"mr":null},
     "suggest_verify":null,"stalled":null}]}],
 "verify_queue":[{"row_id":"","program":"","finished_ago_s":0,"master_hint":"","verify_cmd":""}],
 "human_actions":[{"kind":"merge","ref":"","repo":"","repo_host":"","title":"","pipeline":"","ready":true}],
 "sessions_unmapped":[{"id":"","title":"","dir":"","last_active_ago_s":0,"parent_session_id":null,"parent_title":null}],
 "launch_pending":null}''')

# Declarative shape spec, checked recursively by assert_shape:
#   dict    -> exact key-set equality, then per-key type/shape
#   [spec]  -> list whose every element matches spec
#   "a|b"   -> union: each branch is a primitive name ("str", "int", "float",
#              "bool", "None") or a SHAPES key (a nested shape)
#   a type  -> isinstance against that type (bool is not int; int is not float)
#   _ANY    -> type-unchecked
# A nullable-object position is spelled "shape|None": it accepts None OR the
# dict shape. "master" inside a program is ALWAYS a dict (never null).
_ANY = object()

SHAPES = {
    "state": {
        "schema_version": INT,
        "server": "server",
        "programs": ["program"],
        "verify_queue": ["verify_queue_row"],
        "human_actions": ["human_action_row"],
        "sessions_unmapped": ["unmapped_row"],
        "launch_pending": _ANY,
    },
    "server": {"uptime_s": INT, "generated_ts": INT, "degraded": [STR], "banner": NULSTR},
    "master": {"session_id": NULSTR, "title": NULSTR, "last_active_ago_s": "int|None"},
    "pushed": {"value": bool, "age_s": INT},
    "mr": {"ref": STR, "repo_host": STR, "state": STR, "title": STR, "pipeline": STR, "age_s": INT},
    "manifest": {"path": STR, "prompt_md": STR, "goal_md": STR, "precondition_mrs": [STR],
                 "stall_t_hours": "int|float"},
    "session": {"id": STR, "title": NULSTR, "dir": NULSTR, "title_pending": bool,
                "last_active_ago_s": INT, "parent_session_id": NULSTR},
    "goal": {"state": STR, "queue_tail": STR, "budget": dict},
    "suggest_verify": {"because": [STR]},
    "stalled": {"because": STR, "last_event": STR},
    "program": {"program": STR, "note_path": STR, "note_mtime": INT, "objective": STR,
                "master": "master", "lanes": ["lane"]},
    "lane": {"row_id": STR, "repo": NULSTR, "branch": NULSTR, "slug": NULSTR,
             "status_note": STR, "status_parsed": STR,
             "manifest": "manifest|None", "session": "session|None", "goal": "goal|None",
             "signals": "signals",
             "suggest_verify": "suggest_verify|None", "stalled": "stalled|None"},
    "signals": {"pushed": "pushed|None", "mr": "mr|None"},
    "verify_queue_row": {"row_id": STR, "program": STR, "finished_ago_s": INT,
                         "master_hint": STR, "verify_cmd": STR},
    "human_action_row": {"kind": STR, "ref": STR, "repo": STR, "repo_host": STR,
                         "title": STR, "pipeline": STR, "ready": bool},
    "unmapped_row": {"id": STR, "title": STR, "dir": STR, "last_active_ago_s": INT,
                     "parent_session_id": NULSTR, "parent_title": NULSTR},
}

_PRIMITIVES = {"str": str, "int": int, "float": float, "bool": bool, "None": type(None)}


class ContractViolation(AssertionError):
    """Raised by assert_shape on any key-set/type mismatch at any level."""


def _matches_primitive(ptype: type, value) -> bool:
    if ptype is type(None):
        return value is None
    if ptype is bool:
        return isinstance(value, bool)
    if ptype is int:  # bool is NOT an int for contract checks
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, ptype)


def _check(spec, value, path: str, problems: list) -> None:
    if spec is _ANY:
        return
    if isinstance(spec, dict):
        if not isinstance(value, dict):
            problems.append(f"{path}: expected object, got {type(value).__name__}")
            return
        for key in spec:
            if key not in value:
                problems.append(f"{path}.{key}: missing key")
        for key in value:
            if key not in spec:
                problems.append(f"{path}.{key}: unexpected key")
        for key, sub in spec.items():
            if key in value:
                _check(sub, value[key], f"{path}.{key}", problems)
        return
    if isinstance(spec, list):
        inner = spec[0]
        if not isinstance(value, list):
            problems.append(f"{path}: expected list, got {type(value).__name__}")
            return
        for i, item in enumerate(value):
            _check(inner, item, f"{path}[{i}]", problems)
        return
    if isinstance(spec, str):  # union: try each branch, first match wins
        for branch in (b.strip() for b in spec.split("|")):
            if branch in _PRIMITIVES:
                if _matches_primitive(_PRIMITIVES[branch], value):
                    return
            elif branch in SHAPES:
                scratch: list = []
                _check(SHAPES[branch], value, path, scratch)
                if not scratch:
                    return
        problems.append(f"{path}: does not match {spec}")
        return
    # a bare type (str / int / bool / dict)
    if not _matches_primitive(spec, value):
        problems.append(f"{path}: expected {spec.__name__}, got {type(value).__name__}")


def assert_shape(state: dict) -> None:
    """Raise ContractViolation listing EVERY extra/missing/mistyped key."""
    problems: list = []
    _check(SHAPES["state"], state, "state", problems)
    if problems:
        raise ContractViolation("; ".join(problems))


def null_master() -> dict:
    return {"session_id": None, "title": None, "last_active_ago_s": None}


def lane_shell(row_id: str, repo: str | None, branch: str | None, slug: str | None,
               status_note: str, status_parsed: str) -> dict:
    """Lane dict in §9 key order: six parsed fields, then the nullable objects."""
    return {"row_id": row_id, "repo": repo, "branch": branch, "slug": slug,
            "status_note": status_note, "status_parsed": status_parsed,
            "manifest": None, "session": None, "goal": None,
            "signals": {"pushed": None, "mr": None},
            "suggest_verify": None, "stalled": None}


def zero_document(program_names: list[str], uptime_s: int, generated_ts: int,
                  banner: str | None) -> dict:
    """All-keys-present zero document (§9); the caller supplies provider values
    (keeps this module config-free)."""
    return {
        "schema_version": 1,
        "server": {"uptime_s": uptime_s, "generated_ts": generated_ts,
                   "degraded": [], "banner": banner},
        "programs": [{"program": name, "note_path": "", "note_mtime": 0,
                      "objective": "", "master": null_master(), "lanes": []}
                     for name in program_names],
        "verify_queue": [],
        "human_actions": [],
        "sessions_unmapped": [],
        "launch_pending": None,
    }
