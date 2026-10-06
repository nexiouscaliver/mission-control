"""Frozen-contract compliance (spec §9 + wall-overhaul contracts v2/v3): the
verbatim example, the declarative shape spec checked by ``assert_shape``, and
the zero-value builders.

``assert_shape`` accepts ``None`` for any nullable field (a nullable key must
be PRESENT with a null value — an omitted key is a violation) and leaves
``launch_pending`` type-unchecked (any JSON value is contract-compliant).
``bool`` is NOT an ``int`` for these checks.

Contract v2 (schema_version 2): lanes carry ``deps`` / ``verified`` /
``verify_due``; programs carry ``parse_defects``; the document carries
``needs_me``; ``verify_queue_row.finished_ago_s`` is ``int|None`` (null =
due-with-unknown-age: a done/partial lane with no joined session).

Contract v3 (schema_version 3) carries two additive root keys:
- W4-L4: ``merges`` — the MR/PR registry (tower/merges.py). ``program`` /
  ``row_id`` / ``session`` are nullable (an unjoinable lane MR lists with
  nulls, never guessed); ``conflicts`` is ``bool|None`` (null = mergeability
  unknown); ``merged_at`` is ``int|None`` (null = not merged).
- W5-L5 (wall-honesty, v1.11.1): ``sessions_orphaned``
  (tagged-for-a-known-program sessions bound to no lane; the 2026-10-07
  incident's invisible class) and a ROOT ``parse_defects`` aggregation
  (flattened per-program defects — the web's defect strip key the tower
  never emitted before).
All v2 consumers are untouched by the additive keys.
"""

import json

STR, INT, NULSTR = str, int, "str|None"

# The §9 example, verbatim (json.loads preserves key insertion order).
CONTRACT_EXAMPLE = json.loads(r'''{"schema_version":3,"server":{"uptime_s":0,"generated_ts":0,"degraded":[],"banner":null},
 "programs":[{"program":"secfix","note_path":"","note_mtime":0,"objective":"","parse_defects":[],
   "master":{"session_id":null,"title":null,"last_active_ago_s":null},
   "lanes":[{"row_id":"W2-L5","repo":"cleo","branch":null,"slug":null,
     "status_note":"launched","status_parsed":"launched",
     "deps":["W2-L4"],"verified":false,"verify_due":null,
     "manifest":null,"session":null,"goal":null,
     "signals":{"pushed":null,"mr":null},
     "suggest_verify":null,"stalled":null}]}],
 "verify_queue":[{"row_id":"","program":"","finished_ago_s":0,"master_hint":"","verify_cmd":""}],
 "human_actions":[{"kind":"merge","ref":"","repo":"","repo_host":"","title":"","pipeline":"","ready":true}],
 "needs_me":[{"kind":"merge-ready","row_id":"","program":"","action":"","deep_link":null}],
 "merges":[{"host":"","repo":"","number":0,"title":"","branch":"","program":null,"row_id":null,
   "session":null,"state":"","conflicts":null,"draft":false,"created_at":0,"updated_at":0,
   "merged_at":null,"url":"","author":""}],
 "sessions_unmapped":[{"id":"","title":"","dir":"","last_active_ago_s":0,"parent_session_id":null,"parent_title":null}],
 "sessions_orphaned":[{"id":"","title":"","tag":"","last_active_ago_s":0}],
 "parse_defects":[{"note_path":"","line":0,"defect":"","row_id":null}],
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
        "needs_me": ["needs_me_row"],
        "merges": ["merge_row"],
        "sessions_unmapped": ["unmapped_row"],
        # contract v3 (wall-honesty): conservation + root defect aggregation
        "sessions_orphaned": ["orphaned_row"],
        "parse_defects": ["parse_defect"],
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
    "verify_due": {"because": [STR]},
    "stalled": {"because": STR, "last_event": STR},
    "parse_defect": {"note_path": STR, "line": INT, "defect": STR, "row_id": "str|None"},
    "program": {"program": STR, "note_path": STR, "note_mtime": INT, "objective": STR,
                "parse_defects": ["parse_defect"],
                "master": "master", "lanes": ["lane"]},
    "lane": {"row_id": STR, "repo": NULSTR, "branch": NULSTR, "slug": NULSTR,
             "status_note": STR, "status_parsed": STR,
             "deps": [STR], "verified": bool, "verify_due": "verify_due|None",
             "manifest": "manifest|None", "session": "session|None", "goal": "goal|None",
             "signals": "signals",
             "suggest_verify": "suggest_verify|None", "stalled": "stalled|None"},
    "signals": {"pushed": "pushed|None", "mr": "mr|None"},
    "verify_queue_row": {"row_id": STR, "program": STR, "finished_ago_s": "int|None",
                         "master_hint": STR, "verify_cmd": STR},
    "human_action_row": {"kind": STR, "ref": STR, "repo": STR, "repo_host": STR,
                         "title": STR, "pipeline": STR, "ready": bool},
    "needs_me_row": {"kind": STR, "row_id": STR, "program": STR, "action": STR,
                     "deep_link": "str|None"},
    # contract v3 (W4-L4): the MR/PR registry row. program/row_id/session
    # null = unjoinable (never guessed); conflicts null = mergeability
    # unknown; merged_at null = not merged; created_at/updated_at epoch s
    # (0 = unparseable timestamp, a documented degradation).
    "merge_row": {"host": STR, "repo": STR, "number": INT, "title": STR,
                  "branch": STR, "program": NULSTR, "row_id": NULSTR,
                  "session": NULSTR, "state": STR, "conflicts": "bool|None",
                  "draft": bool, "created_at": INT, "updated_at": INT,
                  "merged_at": "int|None", "url": STR, "author": STR},
    "unmapped_row": {"id": STR, "title": STR, "dir": STR, "last_active_ago_s": INT,
                     "parent_session_id": NULSTR, "parent_title": NULSTR},
    # contract v3 (wall-honesty A6): the orphaned-row family — same shape
    # family as unmapped, with the program tag that names the claim.
    "orphaned_row": {"id": STR, "title": STR, "tag": STR, "last_active_ago_s": INT},
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
               status_note: str, status_parsed: str,
               deps: "tuple[str, ...] | list[str]" = (),
               verified: bool = False) -> dict:
    """Lane dict in §9 key order: the parsed fields (six + contract-v2
    deps/verified), then the nullable objects."""
    return {"row_id": row_id, "repo": repo, "branch": branch, "slug": slug,
            "status_note": status_note, "status_parsed": status_parsed,
            "deps": list(deps), "verified": verified, "verify_due": None,
            "manifest": None, "session": None, "goal": None,
            "signals": {"pushed": None, "mr": None},
            "suggest_verify": None, "stalled": None}


def zero_document(program_names: list[str], uptime_s: int, generated_ts: int,
                  banner: str | None) -> dict:
    """All-keys-present zero document (§9 + contracts v2/v3); the caller
    supplies provider values (keeps this module config-free)."""
    return {
        "schema_version": 3,
        "server": {"uptime_s": uptime_s, "generated_ts": generated_ts,
                   "degraded": [], "banner": banner},
        "programs": [{"program": name, "note_path": "", "note_mtime": 0,
                      "objective": "", "parse_defects": [],
                      "master": null_master(), "lanes": []}
                     for name in program_names],
        "verify_queue": [],
        "human_actions": [],
        "needs_me": [],
        "merges": [],
        "sessions_unmapped": [],
        "sessions_orphaned": [],
        "parse_defects": [],
        "launch_pending": None,
    }
