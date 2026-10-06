"""Derived computations (spec §6 + wall-overhaul contract v2): ``stalled``,
``suggest_verify``/``verify_due``, ``verify_queue`` rows, ``human_actions``
merge rows, and the ``needs_me`` aggregation.

Pure functions over lane views the caller (``collect``) assembles — derive
never reads the filesystem, the db, or the network, and never emits degraded
entries: the ``DegradedLog`` stays collect's, so entry 11
(``precondition state unknown: {ref}``) is added by collect, which owns the
log. ``sessions_unmapped`` (spec §6.5) lives in ``zcode_db.unmapped_rows``
(plan ambiguity resolution 1); this module does not duplicate it.

View shapes (built by collect in program-config order then note order — the
document order of verify_queue/human_actions/needs_me rows):

- verify view: ``{"row_id", "program", "status_parsed", "finished_ago_s"
  (int KNOWN age or None when the lane has no session), "master_hint",
  "verify_id", "verified"}`` — ``verify_id`` is the JOINED FULL db id on an
  exactly-1 token join, else the raw ``sess_`` token, "" when no token parsed.
- action view: the verify-view keys plus ``{"repo_idx", "repo", "repo_host",
  "branch", "mr", "mr_failed", "manifest", "precondition_results"}`` —
  ``precondition_results`` maps ref -> the resolved MR's state ("merged" only
  when the by-ref lookup returned a merged MR) or None for a failed/unknown
  lookup; collect computes it via ``signals.lookup_mr_by_ref``.
- stalled view: ``{"row_id", "program", "repo", "branch"}`` for each lane
  whose ``stalled`` derivation fired (collect passes them in lane order).
"""

# The §6.2/§6.3 "finished" statuses that own a verify_queue row / suggest hint.
FINISHED_STATUSES = ("done", "partial")


def derive_stalled(manifest: dict | None, session_epoch_s: float | None,
                   session_id: str | None, queue_mtime_s: float | None,
                   now: float, stall_t_raw) -> dict | None:
    """§6.1: {"because", "last_event"} or None.

    Fires only when the manifest exists with stall_t_hours > 0 AND an activity
    epoch is known AND now - epoch is STRICTLY past stall_t_hours * 3600. The
    epoch is the max of the lane session's time_updated (seconds; None when
    the join missed or the db timestamp is NULL) and the goal dir's queue.md
    mtime — the SESSION wins ties (the queue takes over only when strictly
    newer). Neither known -> None (never fabricate inactivity). ``stall_t_raw``
    is the manifest.json value as loaded, formatted AS GIVEN (6 -> "6",
    6.5 -> "6.5").
    """
    if manifest is None:
        return None
    if not isinstance(stall_t_raw, (int, float)) or isinstance(stall_t_raw, bool):
        return None  # read_manifest already guarantees numeric; guard stays pure
    if stall_t_raw <= 0:
        return None
    epoch = session_epoch_s
    source = "session" if session_epoch_s is not None else None
    if queue_mtime_s is not None and (epoch is None or queue_mtime_s > epoch):
        epoch, source = queue_mtime_s, "queue"
    if epoch is None:
        return None
    idle = now - epoch
    if not idle > stall_t_raw * 3600:
        return None
    if source == "session":
        last_event = f"session {session_id} at {int(now - session_epoch_s)}s ago"
    else:
        last_event = f"queue.md mtime at {int(now - queue_mtime_s)}s ago"
    return {"because": f"inactive for {int(idle)}s > stall_t {stall_t_raw}h",
            "last_event": last_event}


def derive_suggest_verify(status_parsed: str, finished_ago_s: int | None,
                          grace_s: int, verified: bool = False) -> dict | None:
    """§6.2 + contract v2 items 1/5: ``{"because": ["status=<s>",
    "finished_ago_s=<n>|unknown"]}`` exactly, or None — this one rule feeds
    BOTH the lane-level ``suggest_verify``/``verify_due`` keys and the
    verify_queue row (machine-due, decision D8/D15).

    Fires when status is done/partial AND the lane is NOT verified AND the
    finished age is known-and-past-grace OR UNKNOWN (``finished_ago_s is
    None``: the lane has no joined session — its age can never be proven
    inside the grace window, so it renders due-with-unknown-age). The
    controller-written ``verify:ok`` token in the artifacts cell (parsed by
    ``notes``) short-circuits to None — and by contract v2 (D15 reversal) it
    now REMOVES the verify_queue row too.
    """
    if verified:
        return None
    if status_parsed not in FINISHED_STATUSES:
        return None
    if finished_ago_s is None:
        return {"because": [f"status={status_parsed}", "finished_ago_s=unknown"]}
    if finished_ago_s >= grace_s:
        return {"because": [f"status={status_parsed}", f"finished_ago_s={finished_ago_s}"]}
    return None


def verify_queue_rows(lane_views: list[dict], grace_s: int = 0) -> list[dict]:
    """§6.3 + contract v2 item 5: one row per done/partial lane that is NOT
    verified AND past verify_grace_s (a KNOWN finished age must be >= grace;
    an UNKNOWN age — no joined session — renders due with
    ``finished_ago_s: None``). Input order = the caller's
    program-order-then-note-order views. verify_cmd carries the joined full
    db id on a 1-hit join, else the raw token, "" only when no token parsed."""
    rows = []
    for view in lane_views:
        due = derive_suggest_verify(view["status_parsed"], view["finished_ago_s"],
                                    grace_s, verified=view.get("verified", False))
        if due is None:
            continue
        verify_id = view.get("verify_id") or ""
        rows.append({"row_id": view["row_id"],
                     "program": view["program"],
                     "finished_ago_s": view["finished_ago_s"],
                     "master_hint": view["master_hint"],
                     "verify_cmd": f"/mission-control-verify {verify_id}" if verify_id else ""})
    return rows


def human_action_rows(lane_views: list[dict]) -> list[dict]:
    """§6.4: one kind="merge" row per lane whose resolved MR is OPEN and whose
    own MR lookup did not fail (a failed lookup yields NO row — spec §4.4).
    ready = pipeline green AND every precondition merged; a null manifest has
    no precondition_mrs (green alone suffices); a failed/unknown precondition
    leaves the row with ready=False (the entry-11 emission is collect's).
    """
    rows = []
    for view in lane_views:
        mr = view.get("mr")
        if mr is None or view.get("mr_failed"):
            continue
        if mr.get("state") != "open":
            continue
        ready = mr.get("pipeline") == "green"
        manifest = view.get("manifest")
        preconds = manifest["precondition_mrs"] if manifest is not None else []
        if preconds:
            results = view.get("precondition_results") or {}
            ready = ready and all(results.get(ref) == "merged" for ref in preconds)
        rows.append({"kind": "merge",
                     "ref": mr["ref"],
                     "repo": view["repo"],
                     "repo_host": mr["repo_host"],
                     "title": mr["title"],
                     "pipeline": mr["pipeline"],
                     "ready": ready})
    return rows


def _merge_ready(view: dict) -> bool:
    """The human_actions ready rule (pipeline green AND every precondition
    merged; a null manifest has no preconditions) — shared by
    ``human_action_rows`` and the merge-ready needs_me entries."""
    mr = view.get("mr") or {}
    ready = mr.get("pipeline") == "green"
    manifest = view.get("manifest")
    preconds = manifest["precondition_mrs"] if manifest is not None else []
    if preconds:
        results = view.get("precondition_results") or {}
        ready = ready and all(results.get(ref) == "merged" for ref in preconds)
    return ready


def needs_me_rows(action_views: list[dict], verify_rows: list[dict],
                  stalled_views: list[dict]) -> list[dict]:
    """Contract v2 item 3: the needs-me aggregation — one surface enumerating
    what needs the operator now, in the SC-3 order merge-ready, verify-due,
    stalled (each kind in the caller's lane/document order).

    - merge-ready: lanes whose resolved MR is open and ready (the
      human_actions logic via ``_merge_ready``); action names the merge.
      deep_link is None — the tower state carries no MR web URL today
      (extending the frozen ``mr`` shape is a coordinated follow-up, not a
      unilateral lane change).
    - verify-due: one per verify_queue row; action is the verify command.
    - stalled: one per stalled lane; action is repo/branch (row_id when
      neither is known).
    """
    out: list[dict] = []
    for view in action_views:
        mr = view.get("mr")
        if mr is None or view.get("mr_failed") or mr.get("state") != "open":
            continue
        if not _merge_ready(view):
            continue
        where = "/".join(x for x in (view.get("repo"), view.get("branch")) if x) \
            or view["row_id"]
        out.append({"kind": "merge-ready", "row_id": view["row_id"],
                    "program": view["program"],
                    "action": f"merge {mr['ref']} on {where} ({mr['repo_host']})",
                    "deep_link": None})
    for row in verify_rows:
        out.append({"kind": "verify-due", "row_id": row["row_id"],
                    "program": row["program"],
                    "action": row["verify_cmd"] or "/mission-control-verify",
                    "deep_link": None})
    for view in stalled_views:
        where = "/".join(x for x in (view.get("repo"), view.get("branch")) if x) \
            or view["row_id"]
        out.append({"kind": "stalled", "row_id": view["row_id"],
                    "program": view["program"], "action": where,
                    "deep_link": None})
    return out
