"""Shared mc_wall tower test helpers (fixtures-only world; zero live reads).

Helpers are prefixed ``mcwallt_`` per the plan's naming rule; later tasks grow
this file (T-3 adds db builders, T-4 the goal tree, T-5 the fake command
runner, T-6 the composed fixture world).
"""

import json
import sqlite3
import time

import pytest


def mcwallt_make_note(tmp_path, name, lines):
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def mcwallt_clock(start: float, step: float = 0.0):
    t = {"v": start}
    def _now(): t["v"] += step; return t["v"]
    return _now
