"""Session-store adapter boundary — the ONE seam between the tower and a
harness's session database (v1.5.0).

The tower names its session store by adapter (``TowerConfig.store``; the
boot default is ``"zcode"``) and reaches it ONLY through ``resolve()``:
collect's §4.1 read/join and the drift guard both dispatch on that name, so
a new harness is a new registry entry plus a module implementing the
zcode_db surface (open_db_ro, check_schema, probe_factor, cutoff_stored,
to_seconds, scan_tags, session_rows, lane_join, unmapped_rows, check_drift,
DEGRADED_*). A Claude Code adapter is an extension point — NOT IMPLEMENTED
(see the wall README's "Session-store adapter" paragraph).

``default_db_path`` is the canonical per-store default; tower_boot and
scripts/mc_status.py both delegate here so the default lives in ONE place.
"""

from __future__ import annotations

import os

from . import zcode_db

KNOWN_STORES = {"zcode": zcode_db}


def resolve(store: str):
    """store name -> adapter module; ValueError (one clear line) when unknown."""
    try:
        return KNOWN_STORES[store]
    except KeyError:
        raise ValueError(
            'mc-wall: unknown session store "%s" — expected one of: %s'
            % (store, ", ".join(sorted(KNOWN_STORES)))
        )


def default_db_path(store: str) -> str:
    """The canonical default db path for a store (no env handling here —
    MC_WALL_DB precedence belongs to the boot contract, tower_boot)."""
    if store == "zcode":
        return os.path.join(os.path.expanduser("~"), ".zcode", "cli", "db", "db.sqlite")
    raise ValueError(
        'mc-wall: unknown session store "%s" — expected one of: %s'
        % (store, ", ".join(sorted(KNOWN_STORES)))
    )


def check_drift(config):
    """Dispatch the §8 drift guard to the configured store's adapter."""
    return resolve(config.store).check_drift(config)
