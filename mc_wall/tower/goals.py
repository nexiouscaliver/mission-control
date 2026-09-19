"""Regenloop goal-state reading (spec §4.3): per-repo goal dirs under
``<repo>/regenloop/local/orchestrator/goals/``.

State resolution: active (the goal dir exists — wins even when the slug also
sits in the archive INDEX), archived (the slug appears in
``_archive/INDEX.md`` matched as a WHOLE TOKEN on word boundaries — token
equality, never a substring: ``mcwallt-slug`` never matches ``mcwallt-slug-2``),
absent (neither). Absence under an existing root is a legitimate value, never
a failure — this module emits NO degraded entries; the only entry-4 decision
(the configured repo path itself missing) belongs to collect, which owns the
log. ``budget.json`` is passed through VERBATIM (verified live shape, cleo +
omniforge goal dirs, 2026-09-19: ``{"slug", "whole_run", "gates"}`` — exactly
those keys; the tower never invents, renames, or reinterprets budget keys).
``manifest.json`` is a FUTURE mission-control skill P4 artifact — none exists
today — so its absence or unparseability is pre-P4 NORMAL: per-shape defaults,
never a degraded entry.
"""

import json
import os
import re


def goal_root(repo_path: str) -> str:
    """The goals root of a repo (spec §4.3 layout)."""
    return f"{repo_path}/regenloop/local/orchestrator/goals"


def goal_state(root: str, slug: str | None) -> tuple[str, str | None]:
    """("active", goal_dir) | ("archived", None) | ("absent", None).

    ``slug is None`` -> ("absent", None). Active (the goal dir exists) wins
    over an INDEX listing. The archive match is whole-token: the boundary is
    the complement of the slug character class [A-Za-z0-9_-], so a slug that
    is a substring of an indexed slug never matches. A missing/unreadable
    INDEX.md is simply no archive match."""
    if slug is None:
        return ("absent", None)
    gd = os.path.join(root, slug)
    if os.path.isdir(gd):
        return ("active", gd)
    text = _read_text(os.path.join(root, "_archive", "INDEX.md"))
    if text is not None and re.search(
            rf"(?<![A-Za-z0-9_-]){re.escape(slug)}(?![A-Za-z0-9_-])", text):
        return ("archived", None)
    return ("absent", None)


def read_goal(goal_dir: str | None, state: str) -> dict:
    """ALWAYS the full {"state", "queue_tail", "budget"} object — NEVER None
    (nullity of a lane's goal is decided by collect alone, for a degraded repo
    or a null slug). ``read_goal(None, "absent")`` ->
    ``{"state": "absent", "queue_tail": "", "budget": {}}``. With a dir:
    queue_tail = the last non-empty line of queue.md ("" missing/unreadable);
    budget = budget.json iff it parses to a dict, else {} — verbatim
    passthrough, keys never invented or renamed."""
    goal: dict = {"state": state, "queue_tail": "", "budget": {}}
    if goal_dir is None:
        return goal
    text = _read_text(os.path.join(goal_dir, "queue.md"))
    if text is not None:
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped:
                goal["queue_tail"] = stripped
    raw = _read_text(os.path.join(goal_dir, "budget.json"))
    if raw is not None:
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            goal["budget"] = parsed
    return goal


def read_manifest(goal_dir: str | None) -> dict | None:
    """None when goal_dir is None (no goal dir -> manifest null); else
    {"path", "prompt_md", "goal_md", "precondition_mrs", "stall_t_hours"} in
    §9 key order. ``prompt_md``/``goal_md`` are filesystem paths of the goal
    dir's files ("" when absent). manifest.json is read when present AND
    JSON-parseable as a dict; stall_t_hours keeps a numeric (int/float,
    never bool) value else 0, precondition_mrs keeps a list-of-strings else
    []; absent/unparseable/wrong-typed all fall back to those per-shape
    defaults with NO degraded entry (absence is not failure)."""
    if goal_dir is None:
        return None
    stall_t_hours = 0
    precondition_mrs: list[str] = []
    raw = _read_text(os.path.join(goal_dir, "manifest.json"))
    if raw is not None:
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            v = parsed.get("stall_t_hours")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                stall_t_hours = v
            v = parsed.get("precondition_mrs")
            if isinstance(v, list) and all(isinstance(x, str) for x in v):
                precondition_mrs = v
    return {"path": goal_dir,
            "prompt_md": _path_if_exists(os.path.join(goal_dir, "prompt.md")),
            "goal_md": _path_if_exists(os.path.join(goal_dir, "goal.md")),
            "precondition_mrs": precondition_mrs,
            "stall_t_hours": stall_t_hours}


def _read_text(path: str) -> str | None:
    """UTF-8 file read degrading to None (missing/unreadable/undecodable) —
    every caller maps None to its per-shape default, never an exception
    (spec §1 no-exception boundary)."""
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _path_if_exists(path: str) -> str:
    return path if os.path.exists(path) else ""
