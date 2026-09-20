"""F-1: build a TowerConfig from wall.json + env (the default boot contract).

Field precedence (spec S4 table, pinned): store = wall.json "store" >
"zcode" (validated against the session-store registry); db_path = MC_WALL_DB >
wall.json "db_path" > the store's canonical default (zcode:
~/.zcode/cli/db/db.sqlite, owned by mc_wall.tower.session_store); programs/
repos from the wall.json arrays (repos[].path ~-expanded HERE, at build
time); pending_launch_path = wall.json value > <wall_home>/pending-launch.json;
every other TowerConfig field keeps its dataclass default. Malformed
wall.json content raises ValueError with ONE clear line naming wall.json —
main() prints it and exits 1 (install-time contract; the entry never
guesses). Runtime collect failures are NOT boot failures: they degrade
inside collect_state (fail-open), never here.
"""

from __future__ import annotations

import json
import os
import pathlib

from mc_wall.tower import session_store
from mc_wall.tower.config import ProgramConfig, RepoConfig, TowerConfig


def default_db_path() -> pathlib.Path:
    env = os.environ.get("MC_WALL_DB")
    if env:
        return pathlib.Path(env)
    return pathlib.Path(session_store.default_db_path("zcode"))


def _require_str(entry: dict, key: str, where: str) -> str:
    v = entry.get(key)
    if not isinstance(v, str) or not v:
        raise ValueError(f'mc-wall: {where} has an invalid "{key}" — fix wall.json')
    return v


def _optional_str(entry: dict, key: str, where: str):
    v = entry.get(key)
    if v is not None and not (isinstance(v, str) and v):
        raise ValueError(f'mc-wall: {where} has an invalid "{key}" — fix wall.json')
    return v


def tower_config_from_wall(data: dict, wall_home: pathlib.Path) -> TowerConfig:
    wall_home = pathlib.Path(wall_home)
    raw_programs = data.get("programs", [])  # .get default never fires on null
    if not isinstance(raw_programs, list):
        raise ValueError('mc-wall: wall.json "programs" must be a list — fix wall.json')
    programs = []
    for i, p in enumerate(raw_programs):
        where = f"wall.json programs[{i}]"
        if not isinstance(p, dict):
            raise ValueError(f"mc-wall: {where} must be an object — fix wall.json")
        programs.append(ProgramConfig(
            program=_require_str(p, "program", where),
            tag=_require_str(p, "tag", where),
            note_glob=_require_str(p, "note_glob", where),
            master_tag=_optional_str(p, "master_tag", where),
        ))
    raw_repos = data.get("repos", [])
    if not isinstance(raw_repos, list):
        raise ValueError('mc-wall: wall.json "repos" must be a list — fix wall.json')
    repos = []
    for i, r in enumerate(raw_repos):
        where = f"wall.json repos[{i}]"
        if not isinstance(r, dict):
            raise ValueError(f"mc-wall: {where} must be an object — fix wall.json")
        repos.append(RepoConfig(
            name=_require_str(r, "name", where),
            path=os.path.expanduser(_require_str(r, "path", where)),
            host=_require_str(r, "host", where),
        ))
    db = _optional_str(data, "db_path", "wall.json")
    pending = _optional_str(data, "pending_launch_path", "wall.json")
    store = _optional_str(data, "store", "wall.json")
    if store is None:
        store = "zcode"  # the sole adapter today (session_store seam)
    else:
        try:
            session_store.resolve(store)  # name check only — fail LOUD at boot
        except ValueError:
            raise ValueError(
                'mc-wall: wall.json "store" is "%s" — expected one of: %s'
                % (store, ", ".join(sorted(session_store.KNOWN_STORES)))
            )
    db_env = os.environ.get("MC_WALL_DB")
    if db_env:  # spec §4 precedence: MC_WALL_DB > wall.json "db_path" > home default
        db_path = pathlib.Path(db_env)
    elif db is not None:
        db_path = pathlib.Path(db)
    else:
        db_path = default_db_path()
    return TowerConfig(
        db_path=str(db_path),
        programs=tuple(programs),
        store=store,
        repos=tuple(repos),
        pending_launch_path=str(pending) if pending is not None
        else str(wall_home / "pending-launch.json"),
    )


def build_tower_config(wall_home: pathlib.Path) -> TowerConfig:
    path = pathlib.Path(wall_home) / "wall.json"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        raise ValueError(f"mc-wall: cannot read {path} — run `mc-wall install` first")
    if not isinstance(data, dict) or not data.get("token"):
        raise ValueError(f"mc-wall: {path} has no token — run `mc-wall install`")
    return tower_config_from_wall(data, pathlib.Path(wall_home))
