"""Frozen configuration dataclasses for the tower (spec §2)."""

import time
from dataclasses import dataclass, field
from typing import Callable

from .netcache import NetCache


@dataclass(frozen=True, slots=True)
class NetworkSettings:
    ttl_s: int = 120              # success cache TTL
    backoff_base_s: float = 30.0  # first failure backoff
    backoff_max_s: float = 600.0  # backoff ceiling; factor 2.0 per consecutive failure


@dataclass(frozen=True, slots=True)
class RepoConfig:
    name: str  # e.g. "cleo" — used in lanes[].repo and degraded strings
    path: str  # absolute path to local checkout
    host: str  # "gitlab" | "github" | "other" (discovery mints "other")


@dataclass(frozen=True, slots=True)
class ProgramConfig:
    program: str                    # e.g. "secfix"
    tag: str                        # lane tag for the session join
    note_glob: str                  # absolute glob for this program's vault note
    master_tag: str | None = None   # None -> master is all-nulls


@dataclass(frozen=True, slots=True)
class TowerConfig:
    db_path: str
    programs: tuple[ProgramConfig, ...]
    store: str = "zcode"           # session-store adapter name (v1.5.0 seam)
    repos: tuple[RepoConfig, ...] = ()
    pending_launch_path: str | None = None
    now_s: Callable[[], float] = time.time
    uptime_s_provider: Callable[[], int] = lambda: 0
    banner_provider: Callable[[], str | None] = lambda: None
    session_window_s: int = 86400    # creation window for sessions_unmapped (§6.5: time_created cutoff)
    tag_scan_window_s: int = 259200  # how far back session_input is scanned for tags
    verify_grace_s: int = 300        # min finished age before suggest_verify fires
    network: NetworkSettings = NetworkSettings()
    network_cache: NetCache = field(default_factory=NetCache)  # fresh cache per config build
    discovery_degraded: tuple[str, ...] = ()  # boot-time discovery lines (DegradedLog group 7)
