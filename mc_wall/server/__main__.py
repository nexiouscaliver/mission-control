"""``python -m mc_wall.server`` — the module entry launchd's run.sh execs.

Resolves the wall home (MC_WALL_HOME or ~/.zcode/mc-wall), reads wall.json
into a fully-populated ServerConfig, and hands off to run_server. wall.json is
the boot contract: if it is missing, unparseable, or tokenless, that is an
install-time problem and fails LOUDLY with one clear line (rc 1) — the entry
never generates or rewrites a token itself (silent rotation would break the
pinned URL; ``mc-wall install`` owns token creation).
"""

from __future__ import annotations

import json
import os
import pathlib

from mc_wall.server.app import ServerConfig, run_server

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]  # same resolution as templates_dir()

DEFAULT_PORT = 8765


def resolve_wall_home() -> pathlib.Path:
    env = os.environ.get("MC_WALL_HOME")
    if env:
        return pathlib.Path(env)
    return pathlib.Path.home() / ".zcode" / "mc-wall"


def load_config(wall_home: pathlib.Path) -> ServerConfig:
    path = pathlib.Path(wall_home) / "wall.json"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        raise ValueError(
            "mc-wall: cannot read %s — run `mc-wall install` first" % path
        )
    if not isinstance(data, dict) or not data.get("token"):
        raise ValueError("mc-wall: %s has no token — run `mc-wall install`" % path)
    # MC_WALL_DB exists so tests can pin a nonexistent tmp path; prod targets
    # the live session db (the monitor is read-only against it).
    db_env = os.environ.get("MC_WALL_DB")
    return ServerConfig(
        token=data["token"],
        port=int(data.get("port", DEFAULT_PORT)),
        wall_home=pathlib.Path(wall_home),
        web_dir=REPO_ROOT / "web",
        state_dir=pathlib.Path(wall_home) / "state",
        log_dir=pathlib.Path(wall_home) / "logs",
        db_path=(
            pathlib.Path(db_env)
            if db_env
            else pathlib.Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"
        ),
    )


def main(argv=None) -> int:
    try:
        cfg = load_config(resolve_wall_home())
    except ValueError as exc:
        print(exc)  # ONE clear line, stdout
        return 1
    return run_server(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
