"""Note lint (wall-honesty A7, v1.11.1): the write-side hook controllers run
after every prompt-log edit. ONE truth — this runs the SAME parser the server
collects with (``notes.parse_note`` + the forge-manifest cross-check), never a
reimplementation, so a lint pass is exactly what the Wall will render.

``lint_note_file`` is the pure core (bin/mc-wall's ``lint-note`` command and
any future check-hook both call it). An unreadable/undecodable note is an
``error`` result (exit 1 at the CLI), never an exception past this module.
"""

import os

from . import forge, notes


def default_forge_root() -> str:
    """MC_WALL_FORGE_ROOT overrides the canonical ~/.mc-wall/forge (the
    test/operator injection seam; bin/mc-wall owns env handling)."""
    env = os.environ.get("MC_WALL_FORGE_ROOT")
    if env:
        return env
    return os.path.expanduser(os.path.join("~", ".mc-wall", "forge"))


def lint_note_file(path: str, program: str | None = None,
                   forge_root: str | None = None) -> dict:
    """Lint one note file with the production parser.

    Returns ``{"note_path", "ok", "rows", "defects", "error"}``: ``rows`` is
    the parsed prompt-log row count (the CLI's summary line), ``defects`` the
    parser's + manifest cross-check's (empty when clean), ``error`` a str for
    an unreadable/undecodable note (rows 0, defects []), ``ok`` True only for
    a readable note with zero defects. ``program`` given -> the forge-manifest
    absent-row alarm runs against that program's manifests."""
    abs_path = os.path.abspath(path)
    try:
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"note_path": abs_path, "ok": False, "rows": 0, "defects": [],
                "error": "%s: %s" % (type(exc).__name__, exc)}
    parsed = notes.parse_note(text, note_path=abs_path)
    defects = list(parsed.defects)
    if program is not None:
        defects += forge.missing_row_defects(
            forge_root if forge_root is not None else default_forge_root(),
            program, {r.row_id for r in parsed.rows}, abs_path)
    defects.sort(key=lambda d: d["line"])
    return {"note_path": abs_path, "ok": not defects,
            "rows": len(parsed.rows), "defects": defects, "error": None}
