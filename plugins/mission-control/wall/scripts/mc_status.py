"""mc-status — the MC Wall board as text, over the REAL tower (read-only).

Invoked ``.venv/bin/python -m scripts.mc_status`` from the repo root. CLI:
``--config PATH`` and ``--db PATH``. Resolution order — config: flag >
$MC_WALL_TOWER_CONFIG > <wall_home>/tower.json where wall_home is
$MC_WALL_HOME or ~/.zcode/mc-wall; db: flag > $MC_WALL_DB >
~/.zcode/cli/db/db.sqlite (the same default as mc_wall/server/__main__).

Exit matrix: a MISSING config prints one degrade line and still renders the
db-derived sections over an empty program list, exit 0; an UNPARSEABLE config
exits 2 with one stderr line ONLY when the path came from --config, else the
same degrade-and-render path, exit 0; a PARSED config whose program/repo rows
have wrong keys prints one ``tower config invalid`` line and takes the same
degrade-and-render path, exit 0; a missing/unreadable db surfaces the
tower's own fail-open degraded entries, exit 0. A top-level catch prints one
line and exits 0 — this CLI NEVER shows a traceback.

Testable seams: ``main(argv) -> int`` and ``render(doc) -> str`` (render is
pure over a collect_state document; nullable fields render as "—"/"unknown",
never a crash). Syntax floor: Python 3.9 — no f-strings; stdlib only besides
the repo's own mc_wall package (imported lazily, the _templates() pattern).
"""

import json
import os
import sys

UNMAPPED_CAP = 15
CONFIG_HINT = "(set MC_WALL_TOWER_CONFIG; see README)"


def _wall_home():
    env = os.environ.get("MC_WALL_HOME")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".zcode", "mc-wall")


def _default_db():
    env = os.environ.get("MC_WALL_DB")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".zcode", "cli", "db", "db.sqlite")


def _resolve_config_path(flag):
    """(path, explicit) — explicit True only when the flag named the path (the
    exit-2 unparseable case is reserved for an operator-typed --config)."""
    if flag:
        return flag, True
    env = os.environ.get("MC_WALL_TOWER_CONFIG")
    if env:
        return env, False
    return os.path.join(_wall_home(), "tower.json"), False


def _load_config_body(path):
    with open(path, "rb") as fh:
        data = json.loads(fh.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("tower config is not a JSON object")
    return data


def _build_tower_config(data, db_path):
    from mc_wall.tower import ProgramConfig, RepoConfig, TowerConfig

    programs = tuple(ProgramConfig(**p) for p in (data.get("programs") or ()))
    repos = tuple(RepoConfig(**r) for r in (data.get("repos") or ()))
    # Default providers + the dataclass's fresh NetCache: no injection here —
    # this CLI renders the live tower exactly as the wall server would.
    return TowerConfig(db_path=db_path, programs=programs, repos=repos,
                       pending_launch_path=data.get("pending_launch_path"))


def _show(value):
    return "—" if value is None else str(value)


def _age(value):
    return "unknown" if value is None else "%ss ago" % value


def render(doc):
    """The board text for one collect_state document (pure; ends with \\n)."""
    lines = []
    server = doc.get("server") or {}
    lines.append("mc-status board  generated_ts=%s" % _show(server.get("generated_ts")))
    degraded = server.get("degraded") or []
    if degraded:
        lines.append("degraded:")
        for entry in degraded:
            lines.append("  - %s" % entry)

    lines.append("programs:")
    programs = doc.get("programs") or []
    if not programs:
        lines.append("  (none configured)")
    for prog in programs:
        lines.append("  %s  objective: %s" % (_show(prog.get("program")),
                                              _show(prog.get("objective"))))
        master = prog.get("master") or {}
        lines.append("    master: %s | %s | last active %s" % (
            _show(master.get("session_id")), _show(master.get("title")),
            _age(master.get("last_active_ago_s"))))
        lines.append("    lanes: id  status  branch  slug  session  last-active")
        lanes = prog.get("lanes") or []
        if not lanes:
            lines.append("      (no lanes)")
        for lane in lanes:
            session = lane.get("session") or {}
            lines.append("      %s  %s  %s  %s  %s  %s" % (
                _show(lane.get("row_id")), _show(lane.get("status_parsed")),
                _show(lane.get("branch")), _show(lane.get("slug")),
                _show(session.get("id")), _age(session.get("last_active_ago_s"))))

    lines.append("verify queue:")
    verify = doc.get("verify_queue") or []
    if not verify:
        lines.append("  (none)")
    for row in verify:
        lines.append("  %s  %s  finished %s ago  hint=%s  %s" % (
            _show(row.get("row_id")), _show(row.get("program")),
            _show(row.get("finished_ago_s")), _show(row.get("master_hint")),
            _show(row.get("verify_cmd"))))

    lines.append("human actions:")
    actions = doc.get("human_actions") or []
    if not actions:
        lines.append("  (none)")
    for row in actions:
        lines.append("  %s  %s  %s (%s)  %s  pipeline=%s  ready=%s" % (
            _show(row.get("kind")), _show(row.get("ref")), _show(row.get("repo")),
            _show(row.get("repo_host")), _show(row.get("title")),
            _show(row.get("pipeline")), _show(row.get("ready"))))

    lines.append("sessions unmapped:")
    unmapped = doc.get("sessions_unmapped") or []
    if not unmapped:
        lines.append("  (none)")
    for row in unmapped[:UNMAPPED_CAP]:
        lines.append("  %s  %s  %s  %s" % (
            _show(row.get("id")), _show(row.get("title")), _show(row.get("dir")),
            _age(row.get("last_active_ago_s"))))
    if len(unmapped) > UNMAPPED_CAP:
        lines.append("  (%d more)" % (len(unmapped) - UNMAPPED_CAP))

    lines.append("launch pending: %s" % (
        "(none)" if doc.get("launch_pending") is None
        else json.dumps(doc.get("launch_pending"), sort_keys=True)))
    return "\n".join(lines) + "\n"


def _run(argv):
    import argparse

    parser = argparse.ArgumentParser(
        prog="mc-status",
        description="Print the MC Wall program/lanes/session board as text (read-only).")
    parser.add_argument("--config", help="tower.json path (default: $MC_WALL_TOWER_CONFIG"
                                         " or <wall_home>/tower.json)")
    parser.add_argument("--db", help="session db path (default: $MC_WALL_DB"
                                     " or ~/.zcode/cli/db/db.sqlite)")
    args = parser.parse_args(argv)

    cfg_path, explicit = _resolve_config_path(args.config)
    db_path = args.db or _default_db()

    if not os.path.exists(cfg_path):
        print("tower config not found: %s %s" % (cfg_path, CONFIG_HINT))
        data = {}
    else:
        try:
            data = _load_config_body(cfg_path)
        except Exception:
            if explicit:
                sys.stderr.write("mc-status: cannot parse config: %s\n" % cfg_path)
                return 2
            print("tower config unreadable: %s %s" % (cfg_path, CONFIG_HINT))
            data = {}
    try:
        tower_config = _build_tower_config(data, db_path)
    except (TypeError, ValueError):
        # Parses as JSON but the program/repo rows have wrong keys — the same
        # degrade-and-render path as a missing config, never a blank board:
        # the db-derived sections still render over an empty program list.
        print("tower config invalid: %s" % cfg_path)
        tower_config = _build_tower_config({}, db_path)

    from mc_wall.tower import collect_state

    sys.stdout.write(render(collect_state(tower_config)))
    return 0


def main(argv=None):
    try:
        return _run(argv)
    except Exception as exc:
        # Fail-open CLI: one line, exit 0 — never a traceback.
        print("mc-status failed: %s" % type(exc).__name__)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
