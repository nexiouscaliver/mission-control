"""T-1 contract tests: assert_shape unit coverage, AC-CONTRACT-2, AC-STD-1."""

import ast
import copy
import sys
from pathlib import Path

import pytest

from mc_wall.tower import ProgramConfig, TowerConfig, collect_state
from mc_wall.tower import contract

TOWER_DIR = Path(__file__).resolve().parents[2] / "mc_wall" / "tower"


def _example():
    return copy.deepcopy(contract.CONTRACT_EXAMPLE)


def _empty_list_variant():
    v = _example()
    v["programs"] = []
    v["verify_queue"] = []
    v["human_actions"] = []
    v["sessions_unmapped"] = []
    return v


def _drop_server_banner(v):
    del v["server"]["banner"]


def _add_top_level_key(v):
    v["unexpected_top"] = 1


def _drop_lane_repo(v):
    del v["programs"][0]["lanes"][0]["repo"]


def _string_schema_version(v):
    v["schema_version"] = "1"


def _drop_master_last_active(v):
    del v["programs"][0]["master"]["last_active_ago_s"]


def _int_in_degraded(v):
    v["server"]["degraded"] = ["x", 3]


def _pushed_missing_age(v):
    v["programs"][0]["lanes"][0]["signals"]["pushed"] = {"value": True}


def test_mcwallt_contract_assert_shape_unit():
    # The §9 example itself (one zero row per list) is compliant.
    contract.assert_shape(contract.CONTRACT_EXAMPLE)
    # Empty lists are equally compliant.
    contract.assert_shape(_empty_list_variant())

    # launch_pending is type-unconstrained: any JSON value must pass.
    for value in ([1, 2], 42, "x", True, None, {"a": 1}):
        variant = copy.deepcopy(contract.CONTRACT_EXAMPLE)
        variant["launch_pending"] = value
        contract.assert_shape(variant)

    bad_states = (
        _add_top_level_key,        # extra key at top level
        _drop_server_banner,       # missing server.banner
        _drop_lane_repo,           # omitted nullable lane.repo — NOT omittable
        _string_schema_version,    # schema_version as str
        _drop_master_last_active,  # master missing last_active_ago_s
        _int_in_degraded,          # degraded containing an int
        _pushed_missing_age,       # pushed object missing age_s
    )
    for mutate in bad_states:
        bad = _example()
        mutate(bad)
        with pytest.raises(contract.ContractViolation):
            contract.assert_shape(bad)


def test_mcwallt_contract_keys_survive_full_degradation(tmp_path):
    launch = tmp_path / "mcwallt_launch.json"
    launch.write_bytes(b"not-json{")
    cfg = TowerConfig(
        db_path=str(tmp_path / "mcwallt_missing.db"),
        programs=(ProgramConfig(program="secfix", tag="secfix",
                                note_glob=str(tmp_path / "mcwallt_none_*.md")),),
        pending_launch_path=str(launch),
    )
    state = collect_state(cfg)
    assert isinstance(state, dict)
    contract.assert_shape(state)
    assert state["programs"][0] == {
        "program": "secfix",
        "note_path": "",
        "note_mtime": 0,
        "objective": "",
        "master": {"session_id": None, "title": None, "last_active_ago_s": None},
        "lanes": [],
    }
    assert state["server"]["degraded"] == [
        "tracking degraded: session store unreadable",
        "notes degraded: secfix",
        "launch state degraded: pending-launch unreadable",
    ]
    assert state["launch_pending"] is None


def test_mcwallt_tower_stdlib_only():
    failures = []
    for py in sorted(TOWER_DIR.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root not in sys.stdlib_module_names and root != "mc_wall":
                        failures.append(f"{py.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    continue  # relative import — stays inside mc_wall
                root = (node.module or "").split(".")[0]
                if root and root not in sys.stdlib_module_names and root != "mc_wall":
                    failures.append(f"{py.name}: from {node.module} import ...")
    assert failures == []
