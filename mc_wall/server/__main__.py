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
import pathlib

from mc_wall.server.app import ServerConfig, resolve_wall_home, run_server
from mc_wall.server.tower_boot import tower_config_from_wall

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]  # same resolution as templates_dir()

DEFAULT_PORT = 8765


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
    try:
        port = int(data.get("port", DEFAULT_PORT))
    except (TypeError, ValueError):  # null / non-numeric — one clear line, not a traceback
        raise ValueError(
            "mc-wall: %s has an invalid port — run `mc-wall install`" % path
        )
    tower = tower_config_from_wall(data, pathlib.Path(wall_home))
    return ServerConfig(
        token=data["token"],
        port=port,
        wall_home=pathlib.Path(wall_home),
        web_dir=REPO_ROOT / "web",
        state_dir=pathlib.Path(wall_home) / "state",
        log_dir=pathlib.Path(wall_home) / "logs",
        db_path=pathlib.Path(tower.db_path),  # same resolution the tower uses
        tower_config=tower,
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
