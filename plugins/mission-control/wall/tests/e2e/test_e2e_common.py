"""Hermetic pure-logic tests for the mcwall-e2e harness helpers (goal mcwall-e2e).

Spec §5d scope: ONLY pure logic through ``tmp_path`` fixtures — no live session
db, no vault reads, no network, no server boot. The helpers' module import
time is likewise side-effect free (constants + deferred product imports only).

Seed-value hygiene (plan §3.3 dispatch rule): every assert on a seeded random
field uses a precomputed boolean with a static message, so even a FAILURE
never echoes a lane_tag / row_id / prompt_sha256 value into the report.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import shutil
import sys
import time

import pytest

_HERE = pathlib.Path(__file__).resolve().parent
# Make the sibling helpers importable regardless of pytest's basedir choice
# (tests/e2e has no __init__.py by constraint) and mc_wall importable from
# THIS worktree even when the file is run standalone.
for _entry in (str(_HERE.parents[1]), str(_HERE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

try:  # TDD RED phase: the helpers do not exist yet. Keeping the file
    # importable makes each test FAIL (AttributeError) instead of erroring at
    # collection, so the failing test ids land in the gate receipt's items.
    import e2e_common  # noqa: E402  (sys.path setup above must precede)
    import seed_pending  # noqa: E402
    import serve  # noqa: E402
except ImportError:  # pragma: no cover - RED-phase only, helpers absent
    e2e_common = seed_pending = serve = None

# Exact PendingRecord key set (mc_wall/server/pending.py:30-46) — pinned so a
# schema drift in the seed builder fails loudly here, not in a browser run.
PENDING_KEYS = (
    "status",
    "flag",
    "reason",
    "row_id",
    "lane_tag",
    "repo_root",
    "prompt_sha256",
    "launch_click_ms",
    "matched_session_id",
    "matched_at_ms",
    "last_eval_ms",
    "advisory_120s_fired",
    "canary_fired",
    "updated_at_ms",
    "version",
)

# e2e-env.json schema written by serve.py before the READY line (plan §3.5).
ENV_KEYS = ("home", "port", "token_file", "url_base", "page_url", "pid")


def test_helpers_importable():
    """RED-phase pin: the sibling helpers must import (else every other test
    fails with AttributeError)."""
    assert e2e_common is not None and seed_pending is not None and serve is not None


def _ns(home, reuse):
    return argparse.Namespace(home=home, reuse=reuse)


# ---------------------------------------------------------------- constants


def test_url_and_filename_constants():
    assert e2e_common.E2E_PORT == 8799
    assert e2e_common.URL_BASE == "http://127.0.0.1:8799"
    assert e2e_common.WALL_JSON_FILENAME == "wall.json"
    assert e2e_common.ENV_FILENAME == "e2e-env.json"
    assert e2e_common.SEED_STATUSES == ("prompt-armed", "goal-armed", "flagged")


def test_real_inputs_pinned():
    """The real read-only inputs are pinned exactly (plan §3.1 / spec §1).

    Pure string checks only — nothing on disk is opened.
    """
    assert e2e_common.REAL_DB_PATH == (
        pathlib.Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"
    )
    assert set(e2e_common.VAULT_PROGRAM_NOTES) == {"mc-wall", "secfix", "det-filter"}
    assert {n: p.name for n, p in e2e_common.VAULT_PROGRAM_NOTES.items()} == {
        "mc-wall": "mission-control-mc-wall-program.md",
        "secfix": "mission-control-secfix-orbit-socials-program.md",
        "det-filter": "mission-control-det-filter-program.md",
    }
    hosts = {name: host for name, _path, host in e2e_common.REPO_SPECS}
    assert hosts == {"cleo": "gitlab", "mc-wall": "gitlab"}


# ------------------------------------------------------- e2e-env.json I/O


def test_env_schema_roundtrip(tmp_path):
    env = {
        "home": str(tmp_path),
        "port": e2e_common.E2E_PORT,
        "token_file": str(tmp_path / e2e_common.WALL_JSON_FILENAME),
        "url_base": e2e_common.URL_BASE,
        "page_url": "%s/%s/" % (e2e_common.URL_BASE, "unit-token"),
        "pid": 424242,
    }
    (tmp_path / e2e_common.ENV_FILENAME).write_text(json.dumps(env), encoding="utf-8")
    assert e2e_common.load_env(tmp_path) == env


def test_load_env_rejects_bad_schema(tmp_path):
    path = tmp_path / e2e_common.ENV_FILENAME
    with pytest.raises(SystemExit):  # missing file
        e2e_common.load_env(tmp_path)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit):  # unparseable
        e2e_common.load_env(tmp_path)
    path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    with pytest.raises(SystemExit):  # not an object
        e2e_common.load_env(tmp_path)
    path.write_text(json.dumps({k: 1 for k in ENV_KEYS if k != "pid"}), encoding="utf-8")
    with pytest.raises(SystemExit):  # missing required key
        e2e_common.load_env(tmp_path)


# ------------------------------------------------- token / URL derivation


def test_make_home_fresh_writes_wall_json(tmp_path):
    home = serve._make_home(_ns(str(tmp_path), reuse=False))
    assert home == tmp_path.resolve()
    wall = json.loads(
        (home / e2e_common.WALL_JSON_FILENAME).read_text(encoding="utf-8")
    )
    assert set(wall) == {"token", "port"}
    assert wall["port"] == e2e_common.E2E_PORT
    ok = isinstance(wall["token"], str) and (
        re.fullmatch(r"[A-Za-z0-9_-]{24,}", wall["token"]) is not None
    )
    assert ok, "token must be a non-empty urlsafe string (token_urlsafe(24))"
    # page_url derivation pinned by plan §3.5 (trailing slash, token segment).
    page_url = "%s/%s/" % (e2e_common.URL_BASE, wall["token"])
    assert page_url.startswith(e2e_common.URL_BASE + "/")
    assert page_url.endswith("/")


def test_make_home_fresh_default_is_mkdtemp_prefix():
    home = serve._make_home(_ns(None, reuse=False))
    try:
        assert home.name.startswith("mcwall-e2e-")
        assert (home / e2e_common.WALL_JSON_FILENAME).is_file()
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_make_home_reuse_keeps_wall_json_bytes(tmp_path):
    serve._make_home(_ns(str(tmp_path), reuse=False))
    first = (tmp_path / e2e_common.WALL_JSON_FILENAME).read_bytes()
    again = serve._make_home(_ns(str(tmp_path), reuse=True))
    assert again == tmp_path.resolve()
    assert (again / e2e_common.WALL_JSON_FILENAME).read_bytes() == first, (
        "reuse must keep the SAME token so the browser URL survives a restart"
    )


def test_make_home_usage_errors(tmp_path):
    with pytest.raises(SystemExit) as exc:  # --reuse without --home
        serve._make_home(_ns(None, reuse=True))
    assert exc.value.code == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit) as exc:  # --reuse with no wall.json
        serve._make_home(_ns(str(empty), reuse=True))
    assert exc.value.code == 2
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / e2e_common.WALL_JSON_FILENAME).write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:  # fresh with existing wall.json
        serve._make_home(_ns(str(occupied), reuse=False))
    assert exc.value.code == 2


# ------------------------------------------------------ TowerConfig builder


def test_build_tower_config_fields(tmp_path, monkeypatch):
    notes = {
        "mc-wall": tmp_path / "notes" / "mission-control-mc-wall-program.md",
        "secfix": tmp_path / "notes" / "mission-control-secfix-*.md",  # glob passthrough
        "det-filter": tmp_path / "notes" / "mission-control-det-filter-program.md",
    }
    db = tmp_path / "db.sqlite"
    repos = (
        ("cleo", str(tmp_path / "cleo"), "gitlab"),
        ("mc-wall", str(tmp_path / "mc-wall"), "gitlab"),
    )
    monkeypatch.setattr(e2e_common, "VAULT_PROGRAM_NOTES", notes)
    monkeypatch.setattr(e2e_common, "REAL_DB_PATH", db)
    monkeypatch.setattr(e2e_common, "REPO_SPECS", repos)

    cfg = e2e_common.build_tower_config()

    assert cfg.db_path == str(db)
    assert [p.program for p in cfg.programs] == ["mc-wall", "secfix", "det-filter"]
    for p in cfg.programs:
        assert p.tag == p.program, "plan §7.2: tag = program name (join key)"
        assert p.master_tag is None
        assert p.note_glob == str(notes[p.program]), "note_glob passes through verbatim"
    assert [(r.name, r.path, r.host) for r in cfg.repos] == list(repos)
    assert cfg.pending_launch_path is None  # plan §7.3: prod default
    # TowerConfig defaults elsewhere (spec §1: windows/grace untouched).
    assert cfg.session_window_s == 86400
    assert cfg.tag_scan_window_s == 259200
    assert cfg.verify_grace_s == 300


def test_assert_worktree_imports_passes_in_this_checkout():
    """In the run worktree this must be a no-op; SystemExit(5) anywhere else."""
    e2e_common.assert_worktree_imports()


# ------------------------------------------------- pending seed (unmatchable)


def test_build_record_schema_pins(tmp_path):
    for status in e2e_common.SEED_STATUSES:
        rec = seed_pending.build_record(status, tmp_path)
        assert tuple(sorted(rec)) == tuple(sorted(PENDING_KEYS)), (
            "seed record must carry the EXACT PendingRecord key set"
        )
        assert rec["status"] == status
        assert rec["matched_session_id"] is None
        assert rec["matched_at_ms"] is None
        assert rec["last_eval_ms"] is None
        assert rec["advisory_120s_fired"] is False
        assert rec["canary_fired"] is False
        assert rec["version"] == 1
        if status == "flagged":
            assert rec["flag"] == "ambiguous"
            assert rec["reason"] == "e2e-seed"
        else:
            assert rec["flag"] is None
            assert rec["reason"] is None


def test_record_schema_matches_product_pendingrecord():
    from mc_wall.server.pending import PendingRecord

    product = tuple(sorted(f.name for f in dataclasses.fields(PendingRecord)))
    assert product == tuple(sorted(PENDING_KEYS))


def test_build_record_unmatchable_invariants(tmp_path):
    before = time.time()
    rec = seed_pending.build_record("prompt-armed", tmp_path)
    after = time.time()

    ok = (
        isinstance(rec["lane_tag"], str)
        and rec["lane_tag"].startswith("[e2e-seed-")
        and rec["lane_tag"].endswith("]")
        and len(rec["lane_tag"]) > len("[e2e-seed-]")
    )
    assert ok, "lane_tag must be a bracketed RANDOM e2e tag (never a real lane)"

    ok = isinstance(rec["row_id"], str) and rec["row_id"].startswith("e2e-seed-row-")
    assert ok, "row_id must live in the nonexistent e2e-seed namespace"

    ok = (
        isinstance(rec["prompt_sha256"], str)
        and re.fullmatch(r"[0-9a-f]{64}", rec["prompt_sha256"]) is not None
    )
    assert ok, "prompt_sha256 must be a random 64-hex digest"

    # repo_root INSIDE the temp home: the matcher's realpath gate can never
    # resolve it to a real session workspace (plan §3.3 unmatchability proof).
    assert pathlib.Path(rec["repo_root"]) == tmp_path / "seed-repo"

    lower = int(before * 1000) - 1000
    upper = int(after * 1000) + 1000
    assert lower <= rec["launch_click_ms"] <= upper, "launch_click_ms is epoch-ms now"
    assert rec["updated_at_ms"] == rec["launch_click_ms"]

    other = seed_pending.build_record("prompt-armed", tmp_path)
    ok = rec["lane_tag"] != other["lane_tag"]
    assert ok, "two seeds must never share a lane tag"
    ok = rec["row_id"] != other["row_id"]
    assert ok, "two seeds must never share a row_id"
    ok = rec["prompt_sha256"] != other["prompt_sha256"]
    assert ok, "two seeds must never share a prompt digest"


def test_seed_pending_cli_protocol(tmp_path, capsys):
    rc = seed_pending.main(["--home", str(tmp_path), "--status", "goal-armed"])
    assert rc == 0
    path = tmp_path / "state" / "pending.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    assert rec["status"] == "goal-armed"

    out = capsys.readouterr().out
    assert out.splitlines() == ["E2E-SEED goal-armed %s" % path], (
        "stdout protocol is exactly 'E2E-SEED <status> <path>'"
    )
    assert "[e2e-seed-" not in out, "stdout must NEVER carry the seeded lane_tag"

    rc = seed_pending.main(["--home", str(tmp_path)])  # default + idempotent overwrite
    assert rc == 0
    again = json.loads(path.read_text(encoding="utf-8"))
    assert again["status"] == "prompt-armed"
    out = capsys.readouterr().out
    assert out.splitlines() == ["E2E-SEED prompt-armed %s" % path]


# ------------------------------------------------ QA mock-doc extraction (pure)


def test_extract_mock_case_fixture_html():
    """Fixture-string HTML only — the real web/index.html is never read."""
    html = "\n".join(
        [
            "<!doctype html><html><body>",
            '<script type="application/json" id="mock-full">'
            '{"schema_version": 1, "programs": [{"program": "secfix"}]}</script>',
            '<script id="mock-pending-null" type="application/json">'
            '{"programs": []}</script>',
            '<script type="text/javascript">window.not_a_mock = 1;</script>',
            "</body></html>",
        ]
    )
    full = e2e_common.extract_mock_case(html, "full")
    assert full == {"schema_version": 1, "programs": [{"program": "secfix"}]}
    # Attribute order (id first vs type first) must not matter.
    assert e2e_common.extract_mock_case(html, "pending-null") == {"programs": []}


def test_extract_mock_case_unknown_lists_valid_cases():
    html = '<script type="application/json" id="mock-full">{}</script>'
    with pytest.raises(ValueError) as exc:
        e2e_common.extract_mock_case(html, "nope")
    msg = str(exc.value)
    assert "nope" in msg and "full" in msg, "error names the bad case + valid ones"


def test_extract_mock_case_rejects_bad_json_body():
    html = '<script type="application/json" id="mock-full">{not json}</script>'
    with pytest.raises(ValueError):  # json.JSONDecodeError is a ValueError
        e2e_common.extract_mock_case(html, "full")
