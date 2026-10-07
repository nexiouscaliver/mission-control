"""sd1 — stall-by-default for plain (manifest-less) lanes + the working-status
guard. The W0-O1 incident (2026-10-07): derive_stalled's manifest-None
early-out made the wall-side default 6h unreachable — plain lanes (the whole
no-regenloop era) could NEVER derive stalled, and a 41h-dead 'launched'
session rendered yellow+spin. Fix: default bound without manifest; stalled
derives only for launched/in-flight rows (collect-side guard)."""

import math

from mc_wall.tower import derive

NOW = 1_700_000_000.0


def _call(manifest, idle_s, stall_raw=None, session="sess_abc123"):
    return derive.derive_stalled(
        manifest, NOW - idle_s, session, None, NOW, stall_raw)


def test_sd1_plain_lane_default_bound_fires():
    # manifest None (plain lane) + 41h idle -> fires with the DEFAULT 6h
    out = _call(None, idle_s=41 * 3600)
    assert out is not None
    assert "stall_t 6h" in out["because"]
    assert "session sess_abc123" in out["last_event"]


def test_sd1_plain_lane_inside_bound_quiet():
    assert _call(None, idle_s=5 * 3600) is None


def test_sd1_no_epoch_never_fabricates():
    out = derive.derive_stalled(None, None, "sess_x", None, NOW, None)
    assert out is None


def test_sd1_manifest_bound_overrides_default():
    out = _call({"stall_t_hours": 2}, idle_s=3 * 3600, stall_raw=2)
    assert out is not None and "stall_t 2h" in out["because"]


def test_sd1_manifest_zero_disables_even_for_plain_default_paths():
    assert _call({"stall_t_hours": 0}, idle_s=99 * 3600, stall_raw=0) is None


def test_sd1_because_string_uses_effective_bound():
    out = _call(None, idle_s=7 * 3600)
    assert out["because"] == (
        f"inactive for {7 * 3600}s > stall_t 6h")


def test_sd1_idle_strictly_past_bound():
    assert _call(None, idle_s=6 * 3600) is None  # exactly at bound: quiet
    assert _call(None, idle_s=6 * 3600 + 1) is not None
