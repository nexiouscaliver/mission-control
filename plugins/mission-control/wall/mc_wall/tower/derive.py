"""Derived computations (spec §6): ``stalled``, ``suggest_verify``,
``verify_queue`` rows, ``human_actions`` merge rows.

Pure functions over lane views the caller (``collect``) assembles — derive
never reads the filesystem, the db, or the network, and never emits degraded
entries: the ``DegradedLog`` stays collect's, so entry 11
(``precondition state unknown: {ref}``) is added by collect, which owns the
log. ``sessions_unmapped`` (spec §6.5) lives in ``zcode_db.unmapped_rows`
(plan ambiguity resolution 1); this module does not duplicate it.

View shapes (built by collect in program-config order then note order — the
document order of verify_queue/human_actions rows):

- verify view: ``{"row_id", "program", "status_parsed", "finished_ago_s",
  "master_hint", "verify_id"}`` — ``verify_id`` is the JOINED FULL db id on an
  exactly-1 token join, else the raw ``sess_`` token, "" when no token parsed.
- action view: ``{"repo_idx", "repo", "repo_host", "branch", "mr",
  "mr_failed", "manifest", "precondition_results"}`` —
  ``precondition_results`` maps ref -> the resolved MR's state ("merged" only
  when the by-ref lookup returned a merged MR) or None for a failed/unknown
  lookup; collect computes it via ``signals.lookup_mr_by_ref``.
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


def derive_suggest_verify(status_parsed: str, finished_ago_s: int, grace_s: int,
                          verified: bool = False) -> dict | None:
    """§6.2: {"because": ["status=<s>", "finished_ago_s=<n>"]} exactly, or None.

    v1 has no verify-run record (spec assumption 10): status done/partial AND
    finished_ago_s >= verify_grace_s is the only "not yet verified" proxy —
    unless the row's artifacts cell carried the controller-written ``verify:ok``
    token (a controller-only convention parsed by ``notes``; incidental
    substring occurrences in free text are accepted by design), reported here
    as ``verified``: it short-circuits the cue to None. The ``verify_queue``
    row is unaffected by design — it is the operator's entry point and stays.
    """
    if verified:
        return None
    if status_parsed in FINISHED_STATUSES and finished_ago_s >= grace_s:
        return {"because": [f"status={status_parsed}", f"finished_ago_s={finished_ago_s}"]}
    return None


def verify_queue_rows(lane_views: list[dict]) -> list[dict]:
    """§6.3: one row per done/partial lane, input order (the caller passes
    program-order-then-note-order views). finished_ago_s and master_hint are
    precomputed by the caller; verify_cmd carries the joined full db id on a
    1-hit join, else the raw token, "" only when no token was parsed."""
    rows = []
    for view in lane_views:
        if view["status_parsed"] not in FINISHED_STATUSES:
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
