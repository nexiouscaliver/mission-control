"""F-1 + contract v2 item 8: build a TowerConfig from wall.json + env — and
re-check it EVERY collect cycle (per-poll registration).

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

Program-note discovery (spec mcwall-tower-discovery): ``_declared_config``
builds the wall.json-declared config alone; ``_refresh_discovery`` re-runs
``discover_programs`` (appending undeclared mission-control-*-program.md
notes + their derived repos, carrying skip lines on
TowerConfig.discovery_degraded) over a declared config. Boot
(``tower_config_from_wall``) runs it once with the boot-time INFO line;
``PerPollTowerConfig.current()`` re-runs it EVERY collect cycle — a new or
deleted program note is picked up on the next poll with no restart, and the
wall.json mtime is re-checked each cycle (config rebuilt on change).
MC_WALL_DISCOVERY=0/off/no/false opts out with one INFO line.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib

from mc_wall.tower import discovery, session_store
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


def _declared_config(data: dict, wall_home: pathlib.Path) -> TowerConfig:
    """The wall.json-DECLARED config only (no discovery) — the stable base the
    per-poll provider re-derives discovery over."""
    wall_home = pathlib.Path(wall_home)
    raw_programs = data.get("programs", [])  # .get default never fires on null
    if not isinstance(raw_programs, list):
        raise ValueError('mc-wall: wall.json "programs" must be a list — fix wall.json')
    programs = []
    for i, p in enumerate(raw_programs):
        where = f"wall.json programs[{i}]"
        if not isinstance(p, dict):
            raise ValueError(f'mc-wall: {where} must be an object — fix wall.json')
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
            raise ValueError(f'mc-wall: {where} must be an object — fix wall.json')
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


def _refresh_discovery(cfg: TowerConfig, run_git=None) -> TowerConfig:
    """Declared config + a FRESH discovery pass (contract v2 item 8): the
    scan itself is identical to the boot pass — declared wins, degraded lines
    keep their wording; only WHEN it runs changed (boot once -> every cycle).
    Discovery disabled -> the declared config verbatim."""
    if not discovery.enabled():
        disc = discovery.DiscoveryResult((), (), ())
        disabled = True
    else:
        disc = discovery.discover_programs(cfg.programs, run_git=run_git,
                                           declared_repos=cfg.repos)
        disabled = False
    return TowerConfig(
        db_path=cfg.db_path,
        programs=cfg.programs + disc.programs,
        store=cfg.store,
        repos=cfg.repos + disc.repos,
        pending_launch_path=cfg.pending_launch_path,
        now_s=cfg.now_s,
        uptime_s_provider=cfg.uptime_s_provider,
        banner_provider=cfg.banner_provider,
        session_window_s=cfg.session_window_s,
        tag_scan_window_s=cfg.tag_scan_window_s,
        verify_grace_s=cfg.verify_grace_s,
        network=cfg.network,
        network_cache=cfg.network_cache,  # SAME cache: poll refresh keeps TTLs
        discovery_degraded=disc.degraded,
        discovery_disabled=disabled,
    )


def tower_config_from_wall(data: dict, wall_home: pathlib.Path) -> TowerConfig:
    cfg = _declared_config(data, wall_home)
    if not discovery.enabled():
        logging.getLogger("mc_wall.server").info(
            "MC_WALL_DISCOVERY set — boot-time discovery disabled")
    return _refresh_discovery(cfg)


class PerPollTowerConfig:
    """Contract v2 item 8 — per-poll registration. Holds the DECLARED config
    (built once at construction from wall.json) and serves ``current()``
    per collect cycle: wall.json's mtime is re-checked (full rebuild on
    change; a transient unreadable/unparseable wall.json keeps the last good
    config — a poll never crashes on it) and discovery re-scans per cycle
    (new/deleted program notes appear on the next poll with no restart). The
    ``git remote -v`` probes discovery makes for derived repos cache by path
    on SUCCESS permanently; a FAILING probe is cached for a bounded backoff
    window (wd1, wall-deadlock 2026-10-07: success-only caching made every
    failed probe respawn every cycle — a per-cycle livelock vector, ~5 s per
    probe under a stalled environment)."""

    #: A failed ``git remote -v`` probe is retried at most this often (s).
    GIT_PROBE_FAIL_BACKOFF_S = 300.0

    def __init__(self, wall_home, data: dict | None = None,
                 clock=None):
        import time as _time

        wall_home = pathlib.Path(wall_home)
        if data is None:
            data = _read_wall_json(wall_home)
        if not discovery.enabled():
            logging.getLogger("mc_wall.server").info(
                "MC_WALL_DISCOVERY set — boot-time discovery disabled")
        self._wall_home = wall_home
        self._wall_json = wall_home / "wall.json"
        self._declared = _declared_config(data, wall_home)
        self._mtime = self._stat_mtime()
        self._git_cache: dict[str, tuple[int, str]] = {}
        self._git_fail_until: dict[str, float] = {}
        self._clock = clock or _time.monotonic

    @property
    def db_path(self) -> str:
        return self._declared.db_path

    def _stat_mtime(self):
        try:
            return os.stat(self._wall_json).st_mtime
        except OSError:
            return None

    def _cached_run_git(self, path: str) -> tuple[int, str]:
        hit = self._git_cache.get(path)
        if hit is not None:
            return hit
        until = self._git_fail_until.get(path)
        if until is not None and self._clock() < until:
            return (1, "")  # bounded failure cache: no respawn this cycle
        rc, out = discovery._run_git(path)
        if rc == 0:
            self._git_cache[path] = (rc, out)  # successes cache permanently
        else:
            self._git_fail_until[path] = (self._clock()
                                          + self.GIT_PROBE_FAIL_BACKOFF_S)
        return (rc, out)

    def current(self) -> TowerConfig:
        mtime = self._stat_mtime()
        if mtime is not None and mtime != self._mtime:
            try:  # rebuild on change; a bad mid-write file keeps the last good
                data = _read_wall_json(self._wall_home)
                self._declared = _declared_config(data, self._wall_home)
                self._mtime = mtime
            except (OSError, ValueError):
                pass
        return _refresh_discovery(self._declared, run_git=self._cached_run_git)


def _read_wall_json(wall_home: pathlib.Path) -> dict:
    path = pathlib.Path(wall_home) / "wall.json"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        raise ValueError(f"mc-wall: cannot read {path} — run `mc-wall install` first")
    if not isinstance(data, dict) or not data.get("token"):
        raise ValueError(f"mc-wall: {path} has no token — run `mc-wall install`")
    return data


def build_tower_config(wall_home: pathlib.Path) -> TowerConfig:
    return tower_config_from_wall(_read_wall_json(wall_home),
                                  pathlib.Path(wall_home))
