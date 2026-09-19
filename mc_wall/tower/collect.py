"""collect_state orchestration (T-1 skeleton; notes wired in T-2).

Owns the document assembly, the §4 degraded-entry ordering machinery
(``DegradedLog``), the pending-launch read (§4.5), a minimal session-store
probe, the §4.2 vault-note read, and the no-escape boundary. The remaining
per-source readers (zcode db, goals, signals) land in T-3..T-5 and plug into
``_collect``.
"""

import json
import os
import sqlite3

from . import contract, notes
from .config import TowerConfig


def collect_state(config: TowerConfig) -> dict:
    try:
        return _collect(config)
    except Exception:
        # UNREACHABLE BACKSTOP for internal bugs (plan ambiguity resolution 5) —
        # NOT the fail-open mechanism; per-source readers and NetCache.fetch's
        # rc=None path (T-5) are the fail-open mechanism. No suite test may
        # reach this backstop; if one does, that is a bug.
        doc = contract.zero_document([p.program for p in config.programs], 0, 0, None)
        doc["server"]["degraded"] = ["internal error: collect_state failed"]
        return doc


def _collect(config: TowerConfig) -> dict:
    log = DegradedLog()
    now = config.now_s()
    launch = _read_launch(config.pending_launch_path, log)  # §4.5
    if not _db_ok(config.db_path):  # T-1 minimal probe; T-3 replaces with the real reader
        log.add((0, 0, 0, ""), "tracking degraded: session store unreadable")
    programs = []
    # Internal lane stash (plan T-2 Produces, F3): T-3 (session joins), T-5
    # (signals), and T-6 (derivations) consume these records; the contract lane
    # dict itself never exposes sess_token.
    lane_records = []  # (program_idx, lane_dict, sess_token, repo_name_or_None,
                       #  repo_idx_or_None, branch, status_parsed)
    for i, p in enumerate(config.programs):
        programs.append(_read_program_notes(config, p, i, log, lane_records))
    return {"schema_version": 1,
            "server": {"uptime_s": int(config.uptime_s_provider()),
                       "generated_ts": int(now),
                       "degraded": log.emit(),
                       "banner": config.banner_provider()},
            "programs": programs,
            "verify_queue": [],
            "human_actions": [],
            "sessions_unmapped": [],
            "launch_pending": launch}


def _read_program_notes(config: TowerConfig, program, idx: int,
                        log: "DegradedLog", lane_records: list) -> dict:
    """§4.2 note read for one program: resolve the glob (0 matches -> entry 3 +
    zero row; >1 -> newest mtime, no degradation), parse, build the program row
    and its lanes in note order. A present-but-headerless note keeps
    path/mtime/objective with lanes=[] + entry 3. Entry 10 fires once per
    program when any row was skipped."""
    zero = {"program": program.program, "note_path": "", "note_mtime": 0,
            "objective": "", "master": contract.null_master(), "lanes": []}
    path = notes.find_note(program.note_glob)
    if path is None:
        log.add((1, idx, 0, ""), f"notes degraded: {program.program}")
        return zero
    try:
        with open(path, "rb") as fh:          # bytes + decode: an undecodable
            text = fh.read().decode("utf-8")  # note is an entry-3 failure, not an escape
        mtime = int(os.stat(path).st_mtime)
    except (OSError, UnicodeDecodeError):
        log.add((1, idx, 0, ""), f"notes degraded: {program.program}")
        return zero
    parsed = notes.parse_note(text)
    if not parsed.header_found:
        log.add((1, idx, 0, ""), f"notes degraded: {program.program}")
    lanes = []
    for row in parsed.rows:
        repo_name, repo_idx = _map_repo(config.repos, row.repo_token)
        lane = contract.lane_shell(row.row_id, repo_name, row.branch, row.slug,
                                   row.status_note, row.status_parsed)
        lanes.append(lane)
        lane_records.append((idx, lane, row.sess_token, repo_name, repo_idx,
                             row.branch, row.status_parsed))
    if parsed.skipped >= 1:
        log.add((1, idx, 1, ""), f"note rows skipped: {parsed.skipped}")
    return {"program": program.program, "note_path": os.path.abspath(path),
            "note_mtime": mtime, "objective": parsed.objective,
            "master": contract.null_master(), "lanes": lanes}


def _map_repo(repos, repo_token: str | None) -> tuple[str | None, int | None]:
    """repo token -> (RepoConfig.name, index); (None, None) when no token or no
    match (the lane then follows the unconfigured-repo rule §4.3). The token is
    expanded (~) HERE — collect owns config — and matched against RepoConfig.path
    or RepoConfig.name."""
    if repo_token is None:
        return (None, None)
    expanded = os.path.expanduser(repo_token)
    for idx, rc in enumerate(repos):
        if rc.path == expanded or rc.name == expanded:
            return (rc.name, idx)
    return (None, None)


def _db_ok(path: str) -> bool:
    """Minimal T-1 check: the db opens read-only (mode=ro URI only) and has a
    session table. sqlite connects/opens eagerly under mode=ro, so a missing
    or corrupt file raises at connect or at the first execute — both covered."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            con.execute("SELECT 1 FROM session LIMIT 1")
        finally:
            con.close()
        return True
    except Exception:
        return False


def _read_launch(path: str | None, log: "DegradedLog"):
    """§4.5 pending-launch: None-configured or absent file -> None (no entry);
    present + parseable -> parsed value verbatim (any JSON value); present +
    unparseable (bad JSON OR non-UTF-8 bytes) -> None + entry 8."""
    if path is None:
        return None
    try:
        with open(path, "rb") as fh:  # bytes: decode below so undecodable
            raw = fh.read()           # content is an entry-8 failure, not an escape
    except OSError:
        return None
    try:
        # UnicodeDecodeError is a ValueError, so decode+parse failures share
        # the entry-8 path; json.loads gets a str, keeping the encoding pinned.
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        log.add((6, 0, 0, ""), "launch state degraded: pending-launch unreadable")
        return None


class DegradedLog:
    """§4 degraded ordering.

    key = (group, group_idx, sub, extra); emit() sorts by key then insertion
    seq and drops exact duplicates (keep first). Rank groups: db=0, program
    entries=1 (entry 3 -> sub 0, entry 10 -> sub 1), entry 4=2, net=3
    (entry 5 -> 0, entry 6 -> 1, entry 11 -> 2 with the ref as extra),
    entry 7=4 (session_id as extra), entry 9=5 (token as extra), entry 8=6.
    Sorting is by the raw key tuples, so entry-11 refs order LEXICOGRAPHICALLY
    ("!10" before "!9") — a deliberate v1 choice T-5/T-6 inherit knowingly.
    """

    def __init__(self) -> None:
        self._items = []  # (key, seq, text)

    def add(self, key: tuple, text: str) -> None:
        self._items.append((key, len(self._items), text))

    def emit(self) -> list[str]:
        out, seen = [], set()
        for _k, _s, text in sorted(self._items):
            if text not in seen:
                seen.add(text)
                out.append(text)
        return out
