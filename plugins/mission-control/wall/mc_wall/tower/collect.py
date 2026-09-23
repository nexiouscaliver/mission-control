"""collect_state orchestration: skeleton in T-1; notes in T-2; the zcode
session-db reader in T-3; the regenloop goal-state reader in T-4; the network
signals in T-5; the §6 derivations + final assembly in T-6.

Owns the document assembly, the §4 degraded-entry ordering machinery
(``DegradedLog``), the pending-launch read (§4.5), the §4.2 vault-note read,
the §4.1 session-store read/join (through the ``session_store`` adapter seam —
``store="zcode"`` resolves to ``zcode_db``), the §4.3 goal
state/manifest read (through ``goals``), the §4.4 network signals (through
``signals``, all spawns via ``NetCache``/``_run_cmd``), the §6 derivations
(through ``derive``, with the precondition by-ref lookups and their entry-11
emissions run HERE — derive stays pure), and the no-escape boundary.
"""

import json
import logging
import os

from . import contract, derive, goals, notes, session_store, signals
from .config import TowerConfig

_DISCOVERY_DISABLED_LOGGED = False  # decision 3b: the wall.log disabled-line lands once per process


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
    global _DISCOVERY_DISABLED_LOGGED
    log = DegradedLog()
    for i, line in enumerate(config.discovery_degraded):
        log.add((7, i, 0, ""), line)
    if config.discovery_disabled and not _DISCOVERY_DISABLED_LOGGED:
        # decision 3b: __main__ builds the config before setup_logging, so collect re-emits the line once per process.
        logging.getLogger("mc_wall.server").info(
            "MC_WALL_DISCOVERY set — boot-time discovery disabled")
        _DISCOVERY_DISABLED_LOGGED = True
    now = config.now_s()
    launch = _read_launch(config.pending_launch_path, log)  # §4.5
    programs = []
    # Internal lane stash (plan T-2 Produces, F3): T-3 (session joins), T-4
    # (goal/manifest), T-5 (signals), and T-6 (derivations) consume these
    # records; the contract lane dict itself never exposes sess_token.
    lane_records = []  # (program_idx, lane_dict, sess_token, repo_name_or_None,
                       #  repo_idx_or_None, branch, status_parsed, mr_bang,
                       #  mr_hash) — _read_signals appends (mr, mr_failed) per
                       #  record for T-6's human_actions.
    for i, p in enumerate(config.programs):
        programs.append(_read_program_notes(config, p, i, log, lane_records))
    sessions_unmapped, session_epochs = _read_sessions(config, now, log, programs,
                                                       lane_records)
    _read_goals(config, log, lane_records)
    _read_signals(config, log, lane_records, now)
    verify_queue, human_actions = _derive(config, programs, log, lane_records,
                                          session_epochs, now)
    return {"schema_version": 1,
            "server": {"uptime_s": int(config.uptime_s_provider()),
                       "generated_ts": int(now),
                       "degraded": log.emit(),
                       "banner": config.banner_provider()},
            "programs": programs,
            "verify_queue": verify_queue,
            "human_actions": human_actions,
            "sessions_unmapped": sessions_unmapped,
            "launch_pending": launch}


def _read_program_notes(config: TowerConfig, program, idx: int,
                        log: "DegradedLog", lane_records: list) -> dict:
    """§4.2 note read for one program: resolve the glob (0 matches -> entry 3 +
    zero row; >1 -> newest mtime, no degradation), parse, build the program row
    and its lanes in note order. A present-but-headerless note keeps
    path/mtime/objective with lanes=[] + entry 3. Entry 10 is ADDED once per
    program when any row was skipped — but its text is the frozen §4 vocabulary
    with the count as its only variable, so two programs skipping the SAME
    number of rows emit identical lines and the §4 dedup (identical text, keep
    first) collapses them to ONE emitted line; which program it came from is an
    ambiguity the frozen vocabulary knowingly accepts."""
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
                             row.branch, row.status_parsed, row.mr_bang,
                             row.mr_hash))
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
                   programs: list, lane_records: list) -> tuple[list[dict], dict]:
    """§4.1 session-store read: ro-open + schema check + ONE unit probe; ANY
    failure -> entry 1 (unreadable/operational) or entry 2 (schema drift), with
    ALL session data left nulled (lane.session everywhere, masters, and no
    unmapped rows — strict fail-open, never partial data). Healthy: windowed
    tag scan, per-program masters, per-lane token joins, unmapped enumeration.
    Returns (sessions_unmapped rows, {id(lane): session time_updated epoch in
    SECONDS} for T-6's stalled derivation — keyed by the lane dict's identity;
    a NULL timestamp never enters the map, and every failure path returns an
    empty map so a degraded db contributes no activity epochs)."""
    try:
        store = session_store.resolve(config.store)
        con = store.open_db_ro(config.db_path)
    except ValueError:
        raise  # unknown store name: a config error, never degraded-away
    except Exception:
        log.add((0, 0, 0, ""), store.DEGRADED_UNREADABLE)
        return [], {}
    try:
        cur = con.cursor()
        if store.check_schema(cur):
            log.add((0, 0, 0, ""), store.DEGRADED_SCHEMA_DRIFT)
            return [], {}
        # Exactly ONE unit probe per collect; all cutoffs derive from it.
        factor = store.probe_factor(cur)
        return _join_sessions(config, store, cur, now, factor, log, programs, lane_records)
    except Exception:
        # Strict fail-open (§4.1 / assumption 19): a failure MID-join must not
        # leak whatever masters/lane sessions were already written — null them
        # all before returning (no-op when nothing was written yet); the
        # partial epochs map is discarded with the same stroke.
        for prog in programs:
            prog["master"] = contract.null_master()
        for record in lane_records:
            record[1]["session"] = None
        log.add((0, 0, 0, ""), store.DEGRADED_UNREADABLE)
        return [], {}
    finally:
        con.close()


def _join_sessions(config: TowerConfig, store, cur, now: float, factor: int,
                   log: "DegradedLog", programs: list, lane_records: list) -> tuple[list[dict], dict]:
    """Healthy-path §5 joins: windowed tag scan (entry 7 per ambiguous session,
    ordered by sid via the log key), newest-wins masters, token-prefix lane
    joins (entry 9 on ambiguity), tag-driven binding of token-less lanes
    (SC-3: paste-primary with title fallback, newest-wins on contention), then
    the §6.5 unmapped enumeration. Returns (unmapped rows, lane ->
    joined-session time_updated epoch in seconds)."""
    prods = store.scan_tag_products(
        cur, store.cutoff_stored(now, config.tag_scan_window_s, factor))
    tag_map = {sid: store.products_to_tags(p) for sid, p in prods.items()}
    paste_pairs = {sid: store.products_to_bindings(p) for sid, p in prods.items()}
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
        rows = store.session_rows(cur, cands)
        cands = [sid for sid in cands if sid in rows]  # orphan inputs can't join
        if not cands:
            continue
        best = max(cands, key=lambda sid: (
            rows[sid]["time_updated"] is not None, rows[sid]["time_updated"],
            rows[sid]["time_created"] is not None, rows[sid]["time_created"], sid))
        row = rows[best]
        master_ts = store.to_seconds(row["time_updated"])
        programs[i]["master"] = {
            "session_id": best,
            "title": row["title"],
            # None (unknown) is contract-legal for master.last_active_ago_s
            "last_active_ago_s": max(0, int(now - master_ts)) if master_ts is not None else None}
    # Lane sessions: token-prefix joins; the windowed scan never invalidates one.
    joined_ids: set[str] = set()
    session_epochs: dict[int, float] = {}  # id(lane) -> time_updated epoch (s)
    for _pidx, lane, token, *_rest in lane_records:
        if not token:
            continue
        obj, ambiguous = store.lane_join(cur, token)
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
                           "last_active_ago_s": max(0, int(now - epoch)) if epoch is not None else 0,
                           # the spawning chat's id (session.parent_id); .get so an
                           # adapter predating the field degrades to null, never crashes
                           "parent_session_id": obj.get("parent_session_id")}
        joined_ids.add(obj["id"])
        if epoch is not None:
            session_epochs[id(lane)] = epoch
    # Tag-driven binding (SC-3): fills token-less lanes from pasted/title tag pairs.
    ambiguous_sessions = {sid for sid, pairs in paste_pairs.items() if len(pairs) >= 2}
    for sid in sorted(ambiguous_sessions):
        log.add((4, 1, 1, sid), f"tag bind degraded: ambiguous session {sid}")
    skip_ids = {sid for sid, pairs in paste_pairs.items() if pairs}  # paste-primary per session
    title_pairs = store.scan_title_bindings(cur, now, factor, config.session_window_s, skip_ids)
    cand: dict[tuple[str, str], list[str]] = {}
    for sid, pairs in paste_pairs.items():
        if sid in ambiguous_sessions or sid in joined_ids or not pairs:
            continue
        pair = next(iter(pairs))   # unambiguous paste set is a singleton
        cand.setdefault(pair, []).append(sid)
    for sid, pair in title_pairs.items():
        if sid in joined_ids:
            continue
        cand.setdefault(pair, []).append(sid)
    invisible_drop: set[str] = set(ambiguous_sessions)
    for pidx, lane, token, *_rest in lane_records:
        if token:
            continue  # condition (d): artifacts cell carries a sess_ token (parsed row) — declared wins
        tag = config.programs[pidx].tag
        claims = cand.get((tag, lane["row_id"]), [])
        if not claims:
            continue  # absence is not failure — no degraded line
        rows = store.session_rows(cur, claims)
        claims = [s for s in claims if s in rows]  # orphans dropped before newest-wins
        if not claims:
            continue
        if len(claims) >= 2:
            best = max(claims, key=lambda s: (
                rows[s]["time_updated"] is not None, rows[s]["time_updated"],
                rows[s]["time_created"] is not None, rows[s]["time_created"], s))
            losers = sorted(s for s in claims if s != best)
            log.add((4, 1, 0, f"{tag}/{lane['row_id']}"),
                    f"tag bind degraded: {tag}/{lane['row_id']} newest wins, losers {','.join(losers)}")
            invisible_drop.update(losers)
            claims = [best]
        sid = claims[0]
        row = rows[sid]
        epoch = store.to_seconds(row["time_updated"])
        lane["session"] = {"id": row["id"], "title": row["title"],
                           "title_pending": row["title"] is None or row["title"] == "",
                           "dir": row["directory"],
                           "last_active_ago_s": max(0, int(now - epoch)) if epoch is not None else 0,
                           "parent_session_id": row.get("parent_session_id")}
        joined_ids.add(sid)
        if epoch is not None:
            session_epochs[id(lane)] = epoch
    tag_map_view = {sid: tags for sid, tags in tag_map.items() if sid not in invisible_drop}
    return (store.unmapped_rows(cur, now, factor, config.session_window_s,
                                joined_ids, tag_map_view, configured_tags),
            session_epochs)


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
    for _pidx, lane, _token, _repo_name, repo_idx, _branch, _status, *_refs in lane_records:
        if repo_idx is None or repo_idx in degraded_repos:
            continue  # unconfigured repo / degraded repo: nulls, no entry
        slug = lane["slug"]
        if slug is None:
            continue  # §4.3: slug null -> goal null, manifest null
        root = goals.goal_root(config.repos[repo_idx].path)
        state, gd = goals.goal_state(root, slug)
        lane["goal"] = goals.read_goal(gd, state)
        lane["manifest"] = goals.read_manifest(gd)


def _read_signals(config: TowerConfig, log: "DegradedLog", lane_records: list,
                  now: float) -> None:
    """§4.4 network signals, wired into lanes in place. Per lane with a
    configured repo: pushed (branch None -> null, no entry; lookup failure ->
    null + entry 5) and the MR (artifacts ref first, else by-branch; lookup
    failure -> null + entry 6). Entries are added per lane but deduped to once
    per repo per collect by DegradedLog (identical text, keep first).
    Unconfigured-repo lanes keep the lane_shell signal nulls — a config gap is
    absence, not failure. (mr, mr_failed) is appended to the internal lane
    record for T-6's human_actions. A repo whose configured path is missing
    needs no special-casing here: the spawn fails inside _run_cmd (nonexistent
    cwd) and degrades through these same entries."""
    for i, rec in enumerate(lane_records):
        repo_idx = rec[4]
        if repo_idx is None:
            continue
        repo = config.repos[repo_idx]
        pushed, p_degraded = signals.pushed_signal(
            config.network_cache, config.network, repo, rec[5], config.now_s, now)
        if p_degraded:
            log.add((3, repo_idx, 0, ""), f"network degraded: git {repo.name}")
        rec[1]["signals"]["pushed"] = pushed
        mr, mr_failed = signals.resolve_mr(
            config.network_cache, config.network, repo, rec[5], rec[7], rec[8],
            config.now_s, now)
        if mr_failed:
            log.add((3, repo_idx, 1, ""), f"network degraded: mr {repo.name}")
        rec[1]["signals"]["mr"] = mr
        lane_records[i] = rec + (mr, mr_failed)  # T-6 consumes (mr, mr_failed)


def _derive(config: TowerConfig, programs: list, log: "DegradedLog",
            lane_records: list, session_epochs: dict, now: float) -> tuple[list[dict], list[dict]]:
    """§6 derivations, wired into lanes in place; returns (verify_queue,
    human_actions). Views are built in lane_records order — program-config
    order then note order, the document order of both row lists. The
    precondition by-ref lookups and their entry-11 emissions run HERE (collect
    owns the cache and the log; ``derive`` stays pure) and only for lanes that
    produce a merge row with non-empty precondition_mrs."""
    verify_views: list[dict] = []
    action_views: list[dict] = []
    for rec in lane_records:
        pidx, lane, token = rec[0], rec[1], rec[2]
        repo_idx = rec[4]
        status_parsed = rec[6]
        # Lanes without a configured repo were never extended by _read_signals.
        mr = rec[9] if len(rec) > 9 else None
        mr_failed = rec[10] if len(rec) > 10 else False
        session = lane["session"]
        session_id = session["id"] if session is not None else None
        # §6.2/§6.3: finished_ago_s is the lane session's age, 0 unknown.
        finished_ago_s = session["last_active_ago_s"] if session is not None else 0
        manifest = lane["manifest"]
        lane["stalled"] = derive.derive_stalled(
            manifest, session_epochs.get(id(lane)), session_id,
            _queue_mtime(manifest), now,
            manifest["stall_t_hours"] if manifest is not None else None)
        lane["suggest_verify"] = derive.derive_suggest_verify(
            status_parsed, finished_ago_s, config.verify_grace_s)
        master = programs[pidx]["master"]
        verify_views.append({
            "row_id": lane["row_id"],
            "program": config.programs[pidx].program,
            "status_parsed": status_parsed,
            "finished_ago_s": finished_ago_s,
            "master_hint": master["session_id"] or "",
            # §6.3: the JOINED FULL db id on a 1-hit join, else the raw token.
            "verify_id": session_id if session is not None else (token or "")})
        if repo_idx is None or mr is None or mr_failed or mr["state"] != "open":
            continue  # no merge row can come of this lane — no lookups either
        repo = config.repos[repo_idx]
        preconds = (manifest or {}).get("precondition_mrs") or []
        results: dict[str, str | None] = {}
        for ref in preconds:
            pre_mr, pre_failed = signals.lookup_mr_by_ref(
                config.network_cache, config.network, repo, ref,
                config.now_s, now)
            if pre_failed or pre_mr is None:
                # Unknown != met, never guessed (§4.4/§6.4): the row stays with
                # ready=False and one entry-11 per failed/unknown ref.
                results[ref] = None
                log.add((3, repo_idx, 2, ref), f"precondition state unknown: {ref}")
            else:
                results[ref] = pre_mr["state"]
        action_views.append({"repo_idx": repo_idx, "repo": repo.name,
                             "repo_host": repo.host, "branch": rec[5],
                             "mr": mr, "mr_failed": mr_failed,
                             "manifest": manifest,
                             "precondition_results": results})
    return derive.verify_queue_rows(verify_views), derive.human_action_rows(action_views)


def _queue_mtime(manifest: dict | None) -> float | None:
    """The goal dir's queue.md mtime — the §6.1 non-session activity epoch.
    None when the lane has no manifest (no goal dir) or no queue.md (absence
    is never fabricated into an epoch)."""
    if manifest is None:
        return None
    try:
        return os.stat(os.path.join(manifest["path"], "queue.md")).st_mtime
    except OSError:
        return None


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
    entry 7=4 (session_id as extra), entry 9=5 (token as extra), entry 8=6,
    discovery=7 (boot lines; group_idx = emission order).
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
