"""Network signal resolution (spec §4.4): ``pushed`` via ``git ls-remote`` and
MR resolution via glab (gitlab, ``!N`` refs) / gh (github, ``#N`` refs) — all
through ``NetCache``; the argv templates and CLI verifications are recorded in
``netcache.py``'s docstring (checked against the installed CLIs 2026-09-19).

Resolution order for ``mr``: the artifacts-cell ref matching the repo's host
wins (a ``#5`` on a gitlab repo is ignored); else, when the lane has a branch,
the newest OPEN MR/PR for that branch (created_at desc, then iid/number desc);
else null with no lookup. ``pushed`` exposes exactly {"value","age_s"}, with
age measured against the cache ENTRY's observation timestamp — there is NO
local object-store derivation anywhere in this module (the ls-remote stdout
carries only sha+ref, and the sha stays internal to the cache).
"""

import json
import re
from datetime import datetime
from typing import Callable

from .config import NetworkSettings, RepoConfig

GIT_LS_REMOTE = "git_ls_remote"
MR_BY_REF = "mr_by_ref"
MR_BY_BRANCH = "mr_by_branch"

# By-ref grammar (the notes artifacts regexes yield exactly this; manifest.json
# precondition refs must match it too BEFORE anything reaches argv): optional
# !/# prefix, digits only. Anything else is not an MR ref.
MR_REF_RE = re.compile(r"^[!#]?\d+$")

# Closed normalization maps (§4.4). Raw CLI values are casefolded first so
# gh's uppercase "OPEN" lands in the same vocabulary as gitlab's "opened".
_STATE_MAP = {"opened": "open", "open": "open", "merged": "merged", "closed": "closed"}
_PIPELINE_MAP = {"success": "green", "passed": "green", "failed": "failed",
                 "running": "running", "pending": "running"}


def _created_epoch_s(raw) -> float | None:
    """ISO-8601 created timestamp -> epoch seconds; None when unparseable (the
    caller renders that as age 0, never a raise)."""
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _state_of(adapted: dict) -> str:
    raw = adapted.get("state_raw")
    return _STATE_MAP.get(str(raw).strip().casefold() if raw is not None else "", "")


def _n_of(adapted: dict):
    n = adapted.get("n")
    return n if isinstance(n, int) and not isinstance(n, bool) else None


def adapt_cli_mr(obj: dict, host: str) -> dict | None:
    """Thin CLI-JSON adapter -> {"n","state_raw","title","created_at",
    "pipeline_raw"}; None when the input is not a usable object. This is the
    ONLY place CLI-specific key names live (uncertainty stays here per plan):
    gitlab --output json emits the raw API object (iid/state/title/created_at;
    pipeline from head_pipeline.status falling back to pipeline.status), gh
    --json emits number/state/title/createdAt (v1 requests no check-rollup
    field, so github pipeline_raw is "" — recorded in netcache.py)."""
    if not isinstance(obj, dict):
        return None
    if host == "github":
        n, created, pipeline_raw = obj.get("number"), obj.get("createdAt"), ""
    else:
        n, created = obj.get("iid"), obj.get("created_at")
        pipeline_raw = ""
        for key in ("head_pipeline", "pipeline"):
            pipe = obj.get(key)
            if isinstance(pipe, dict) and isinstance(pipe.get("status"), str):
                pipeline_raw = pipe["status"]
                break
    title = obj.get("title")
    return {"n": n, "state_raw": obj.get("state"),
            "title": title if isinstance(title, str) else "",
            "created_at": created, "pipeline_raw": pipeline_raw}


def normalize_mr(adapted: dict, host: str, now: float) -> dict:
    """Adapted row -> the contract mr object: canonical ref (!N gitlab / #N
    github), closed state/pipeline vocabularies, age floored at 0 (0 when
    created_at is unparseable or in the future)."""
    n = _n_of(adapted)
    pipeline_raw = adapted.get("pipeline_raw")
    pipeline = str(pipeline_raw).strip().casefold() if pipeline_raw else ""
    created = _created_epoch_s(adapted.get("created_at"))
    return {"ref": ("#" if host == "github" else "!") + (str(n) if n is not None else ""),
            "repo_host": host,
            "state": _state_of(adapted),
            "title": adapted.get("title") or "",
            "pipeline": _PIPELINE_MAP.get(pipeline, ""),
            "age_s": max(0, int(now - created)) if created is not None else 0}


def pushed_signal(cache, cfg_network: NetworkSettings, repo: RepoConfig,
                  branch: str | None, now_s: Callable[[], float], clock_now: float) -> tuple[dict | None, bool]:
    """(pushed | None, degraded). branch None -> (None, False) — no lookup, no
    entry. Failure -> (None, True) (entry 5 is the caller's). Success parses
    the ls-remote lines: value = a line whose ref column is EXACTLY
    refs/heads/{branch} (rc 0 with no such line is value=False, a VALID
    result). age_s = clock_now - the entry's observation timestamp."""
    if branch is None:
        return (None, False)
    res = cache.fetch((GIT_LS_REMOTE, repo.name, branch),
                      ["git", "ls-remote", "origin", branch], repo.path, now_s,
                      cfg_network.ttl_s, cfg_network.backoff_base_s,
                      cfg_network.backoff_max_s)
    if not res.ok:
        return (None, True)
    want_ref = f"refs/heads/{branch}"
    value = any("\t" in line and line.split("\t")[1] == want_ref
                for line in res.stdout.splitlines())
    return ({"value": value,
             "age_s": max(0, int(clock_now - res.observed_at_s))}, False)


def resolve_mr(cache, cfg_network: NetworkSettings, repo: RepoConfig,
               branch: str | None, mr_bang: str | None, mr_hash: str | None,
               now_s: Callable[[], float], clock_now: float) -> tuple[dict | None, bool]:
    """(mr | None, lookup_failed) for one lane: the artifacts ref matching the
    repo host wins (host-mismatched tokens are ignored); else by-branch newest
    open; else (None, False). The caller handles unconfigured repos."""
    ref = mr_hash if repo.host == "github" else mr_bang
    if ref is not None:
        return lookup_mr_by_ref(cache, cfg_network, repo, ref, now_s, clock_now)
    if branch is None:
        return (None, False)
    return _mr_by_branch(cache, cfg_network, repo, branch, now_s, clock_now)


def lookup_mr_by_ref(cache, cfg_network: NetworkSettings, repo: RepoConfig,
                     ref: str, now_s: Callable[[], float], clock_now: float) -> tuple[dict | None, bool]:
    """By-ref MR lookup (backing artifacts refs and, in T-6, preconditions).
    v1 recorded choice: rc 0 with empty/unusable stdout (NOT-FOUND) is a lookup
    FAILURE -> (None, True) — unknown != met, absence-as-degraded (documented
    in netcache.py alongside the flag spellings). Hardening: refs are
    grammar-checked (``MR_REF_RE``) FIRST — an off-grammar precondition ref
    from manifest.json (e.g. "--sort=x") must NEVER reach argv; it takes this
    same failure path (unknown, row kept ready=False + entry 11 in collect),
    never a spawn."""
    if not MR_REF_RE.match(ref):
        return (None, True)
    num = ref[1:] if ref[:1] in ("!", "#") else ref
    if repo.host == "github":
        argv = ["gh", "pr", "view", num, "--json", "number,state,title,createdAt"]
    else:
        argv = ["glab", "mr", "view", num, "--output", "json"]
    res = cache.fetch((MR_BY_REF, repo.name, ref), argv, repo.path, now_s,
                      cfg_network.ttl_s, cfg_network.backoff_base_s,
                      cfg_network.backoff_max_s)
    if not res.ok:
        return (None, True)
    try:
        obj = json.loads(res.stdout)
    except ValueError:
        obj = None
    adapted = adapt_cli_mr(obj, repo.host)
    if adapted is None or _n_of(adapted) is None:
        # M1: an rc-0 dict WITHOUT a usable id (a glab error body like
        # {"message": "404"}) is not a usable MR — it takes the same
        # NOT-FOUND-as-failure path, never a nonsense "!" ref and never a
        # skipped entry 6.
        return (None, True)
    return (normalize_mr(adapted, repo.host, clock_now), False)


def _mr_by_branch(cache, cfg_network: NetworkSettings, repo: RepoConfig,
                  branch: str, now_s: Callable[[], float], clock_now: float) -> tuple[dict | None, bool]:
    """Newest OPEN MR for the branch (created_at desc, tie iid/number desc).
    rc 0 + parseable EMPTY list is a valid empty result -> (None, False); a
    spawn failure or unparseable stdout is a lookup failure -> (None, True)."""
    if repo.host == "github":
        argv = ["gh", "pr", "list", "--head", branch, "--json",
                "number,state,title,createdAt"]
    else:
        argv = ["glab", "mr", "list", "--source-branch", branch, "--output", "json"]
    res = cache.fetch((MR_BY_BRANCH, repo.name, branch), argv, repo.path, now_s,
                      cfg_network.ttl_s, cfg_network.backoff_base_s,
                      cfg_network.backoff_max_s)
    if not res.ok:
        return (None, True)
    try:
        items = json.loads(res.stdout)
    except ValueError:
        return (None, True)
    if not isinstance(items, list):
        return (None, True)
    candidates = []
    for obj in items:
        adapted = adapt_cli_mr(obj, repo.host)
        # Same id-usability guard as the by-ref path (M1): an id-less dict can
        # never become a candidate, whatever the list endpoint returned.
        if (adapted is not None and _n_of(adapted) is not None
                and _state_of(adapted) == "open"):
            candidates.append(adapted)
    if not candidates:
        return (None, False)

    def rank(a: dict):
        created = _created_epoch_s(a.get("created_at"))
        n = _n_of(a)
        return (created if created is not None else float("-inf"),
                n if n is not None else -1)

    return (normalize_mr(max(candidates, key=rank), repo.host, clock_now), False)
