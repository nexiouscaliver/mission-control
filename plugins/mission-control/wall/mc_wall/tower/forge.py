"""Forge-manifest cross-check (wall-honesty A5, v1.11.1 — the 2026-10-07
lane-invisibility incident's day-one alarm).

The controller's forge writes ONE write-once machine record per prompt-log row
under ``~/.mc-wall/forge/<program>/<row_id>/manifest.json`` (skill §4 step 5).
Those manifests are the only program state the NOTE'S grammar cannot break:
they live outside the vault, so a blank line, a mangled table, or a deleted
row cannot silence them. This module compares every manifest row_id against
the ids the note parser actually produced — every forged row ABSENT from the
parse alarms as a TOP defect (line 0, so it sorts above every line-addressed
defect in the program's parse_defects).

Fail-open discipline: an unreadable/corrupt manifest defects (never silent)
but never raises; a missing forge dir or program subdir is plain absence —
programs older than the forge era never alarm.
"""

import json
import os


def missing_row_defects(forge_root: str, program: str, parsed_row_ids: set,
                        note_path: str) -> list[dict]:
    """Every manifest row_id absent from ``parsed_row_ids`` -> one TOP defect
    (line 0) in the pinned parse-defect shape; a corrupt/unreadable manifest
    defects by itself (never silently swallowed). Empty when the program has
    no forge dir or no manifests."""
    root = os.path.join(forge_root, program)
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return []  # no forge dir / no program subdir: absence, not failure
    defects: list[dict] = []
    for entry in entries:
        path = os.path.join(entry.path, "manifest.json")
        data = None
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            pass
        if not (isinstance(data, dict) and isinstance(data.get("row_id"), str)
                and data["row_id"] != ""):
            defects.append({"note_path": note_path, "line": 0,
                            "defect": f"forge manifest unreadable: {path}",
                            "row_id": None})
            continue
        if data["row_id"] not in parsed_row_ids:
            defects.append({"note_path": note_path, "line": 0,
                            "defect": f"forged row {data['row_id']} absent"
                                      f" from note parse (manifest exists)",
                            "row_id": data["row_id"]})
    return defects
