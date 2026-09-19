"""collect_state orchestration (T-1 skeleton; notes wired in T-2; the zcode
session-db reader in T-3; the regenloop goal-state reader in T-4).

Owns the document assembly, the §4 degraded-entry ordering machinery
(``DegradedLog``), the pending-launch read (§4.5), the §4.2 vault-note read,
the §4.1 session-store read/join (through ``zcode_db``), the §4.3 goal
state/manifest read (through ``goals``), and the no-escape boundary. The
remaining per-source reader (signals) lands in T-5 and plugs into ``_collect``.
"""

import json
import os

from . import contract, goals, notes, zcode_db
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
    programs = []
    # Internal lane stash (plan T-2 Produces, F3): T-3 (session joins), T-4
    # (goal/manifest), T-5 (signals), and T-6 (derivations) consume these
    # records; the contract lane dict itself never exposes sess_token.
    lane_records = []  # (program_idx, lane_dict, sess_token, repo_name_or_None,
                       #  repo_idx_or_None, branch, status_parsed)
    for i, p in enumerate(config.programs):
        programs.append(_read_program_notes(config, p, i, log, lane_records))
    sessions_unmapped = _read_sessions(config, now, log, programs, lane_records)
    _read_goals(config, log, lane_records)
    return {"schema_version": 1,
            "server": {"uptime_s": int(config.uptime_s_provider()),
                       "generated_ts": int(now),
                       "degraded": log.emit(),
                       "banner": config.banner_provider()},
            "programs": programs,
            "verify_queue": [],
            "human_actions": [],
            "sessions_unmapped": sessions_unmapped,
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


def _read_sessions(config: TowerConfig, now: float, log: "DegradedLog",
                   programs: list, lane_records: list) -> list[dict]:
    """§4.1 session-store read: ro-open + schema check + ONE unit probe; ANY
    failure -> entry 1 (unreadable/operational) or entry 2 (schema drift), with
    ALL session data left nulled (lane.session everywhere, masters, and no
    unmapped rows — strict fail-open, never partial data). Healthy: windowed
    tag scan, per-program masters, per-lane token joins, unmapped enumeration.
    Returns the sessions_unmapped rows."""
    try:
        con = zcode_db.open_db_ro(config.db_path)
    except Exception:
        log.add((0, 0, 0, ""), zcode_db.DEGRADED_UNREADABLE)
        return []
    try:
        cur = con.cursor()
        if zcode_db.check_schema(cur):
            log.add((0, 0, 0, ""), zcode_db.DEGRADED_SCHEMA_DRIFT)
            return []
        # Exactly ONE unit probe per collect; all cutoffs derive from it.
        factor = zcode_db.probe_factor(cur)
        return _join_sessions(config, cur, now, factor, log, programs, lane_records)
    except Exception:
        # Strict fail-open (§4.1 / assumption 19): a failure MID-join must not
        # leak whatever masters/lane sessions were already written — null them
        # all before returning (no-op when nothing was written yet).
        for prog in programs:
            prog["master"] = contract.null_master()
        for record in lane_records:
            record[1]["session"] = None
        log.add((0, 0, 0, ""), zcode_db.DEGRADED_UNREADABLE)
        return []
    finally:
        con.close()


def _join_sessions(config: TowerConfig, cur, now: float, factor: int,
                   log: "DegradedLog", programs: list, lane_records: list) -> list[dict]:
    """Healthy-path §5 joins: windowed tag scan (entry 7 per ambiguous session,
    ordered by sid via the log key), newest-wins masters, token-prefix lane
    joins (entry 9 on ambiguity), then the §6.5 unmapped enumeration."""
    tag_map = zcode_db.scan_tags(
        cur, zcode_db.cutoff_stored(now, config.tag_scan_window_s, factor))
    for sid in sorted(tag_map):
        if len(tag_map[sid]) >= 2:
            log.add((4, 0, 0, sid), f"join degraded: ambiguous tags {sid}")
    configured_tags: dict[str, str] = {}
    for p in config.programs:
        configured_tags[p.tag] = "program"
        if p.master_tag is not None:
            configured_tags[p.master_tag] = "master"
    # Masters: tag set EXACTLY {master_tag}, newest by (time_updated,
    # time_created, id) descending (§5 tie-breaks). The None-safe key prefixes
    # each timestamp with (ts is not None): a SQL-NULL timestamp loses
    # newest-wins to ANY real timestamp instead of raising.
    for i, p in enumerate(config.programs):
        if p.master_tag is None:
            continue
        cands = [sid for sid, tags in tag_map.items() if tags == {p.master_tag}]
        rows = zcode_db.session_rows(cur, cands)
        cands = [sid for sid in cands if sid in rows]  # orphan inputs can't join
        if not cands:
            continue
        best = max(cands, key=lambda sid: (
            rows[sid]["time_updated"] is not None, rows[sid]["time_updated"],
            rows[sid]["time_created"] is not None, rows[sid]["time_created"], sid))
        row = rows[best]
        master_ts = zcode_db.to_seconds(row["time_updated"])
        programs[i]["master"] = {
            "session_id": best,
            "title": row["title"],
            # None (unknown) is contract-legal for master.last_active_ago_s
            "last_active_ago_s": max(0, int(now - master_ts)) if master_ts is not None else None}
    # Lane sessions: token-prefix joins; the windowed scan never invalidates one.
    joined_ids: set[str] = set()
    for _pidx, lane, token, *_rest in lane_records:
        if not token:
            continue
        obj, ambiguous = zcode_db.lane_join(cur, token)
        if ambiguous:
            log.add((5, 0, 0, token), f"join ambiguous session: {token}")
            continue
        if obj is None:
            continue  # pre-launch row: absence is not failure
        title = obj["title"]
        epoch = obj["time_updated_epoch_s"]
        lane["session"] = {"id": obj["id"], "title": title,
                           "title_pending": title is None or title == "",
                           "dir": obj["dir"],
                           # contract requires int; 0 = the spec's unknown-age convention
                           "last_active_ago_s": max(0, int(now - epoch)) if epoch is not None else 0}
        joined_ids.add(obj["id"])
    return zcode_db.unmapped_rows(cur, now, factor, config.session_window_s,
                                  joined_ids, tag_map, configured_tags)


def _read_goals(config: TowerConfig, log: "DegradedLog", lane_records: list) -> None:
    """§4.3 regenloop goal state, wired into lanes in place. Per repo in config
    order: a repo whose configured path does not exist degrades once (entry 4)
    and every lane of that repo keeps goal/manifest null. Healthy repos: each
    lane with a configured repo AND a slug gets the goal object (ALWAYS the
    full {"state","queue_tail","budget"} — read_goal never returns None; a
    missing slug under an existing root is "absent", not a failure) and the
    manifest (None without a goal dir). Lanes with no configured repo or no
    slug keep the lane_shell nulls — absence, not failure, so no entry."""
    degraded_repos = set()
    for r, rc in enumerate(config.repos):
        if not os.path.isdir(rc.path):
            log.add((2, r, 0, ""), f"goals degraded: {rc.name}")
            degraded_repos.add(r)
    for _pidx, lane, _token, _repo_name, repo_idx, _branch, _status in lane_records:
        if repo_idx is None or repo_idx in degraded_repos:
            continue  # unconfigured repo / degraded repo: nulls, no entry
        slug = lane["slug"]
        if slug is None:
            continue  # §4.3: slug null -> goal null, manifest null
        root = goals.goal_root(config.repos[repo_idx].path)
        state, gd = goals.goal_state(root, slug)
        lane["goal"] = goals.read_goal(gd, state)
        lane["manifest"] = goals.read_manifest(gd)


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
