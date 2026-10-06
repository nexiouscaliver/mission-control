"""MR/PR registry collector (wall-overhaul W4-L4, schema 3): every lane PR/MR
across the wall's repos, joined to its program row and session.

The registry is a READ-ONLY per-repo list scan — the forges are the truth;
there is no write-side ledger. Per repo (ONE list call per host): github
``gh pr list -R <owner/repo> --state all --limit 100 --json number,title,
headRefName,isDraft,createdAt,updatedAt,mergedAt,state,mergeable,url,author``
(verified live 2026-10-07, gh 2.78.0); gitlab ``glab mr list -R <group/repo>
--all -F json -P 100`` (glab 1.67.0 has no --state picker; --all covers every
state and the raw API objects carry iid/source_branch/merged_at/draft/web_url/
detailed_merge_status/author.username — field names verified live the same
day). Every spawn routes through ``netcache.NetCache``:

- remote probe:  ["git", "remote", "-v"]  (host + owner/repo slug; the probe
                 is the ground truth for BOTH — wall.json's host label stays
                 the lane-signals source, never this module's)
- github list:   gh pr list ... (above)
- gitlab list:   glab mr list ... (above)

Cache TTL is the REGISTRY TTL of 300 s (W4-L4 decision (a)) — deliberately NOT
the 120 s signals TTL — under distinct keys (``git_remote`` /
``mr_registry_list``) so registry and lane-signal entries never collide. The
repo set is rebuilt fresh every cycle from wall.json repos[] UNION the live
program-note row path tokens (collect hands over already-expanded tokens);
nothing about the set is cached across cycles beyond the NetCache TTL.

Joins (decision (c)): branch -> (program, row_id) via LIVE note rows first,
with the forge manifests under ``<wall_home>/forge/<program>/<row-id>/
manifest.json`` (each carries branch + program + row_id) as the persistent
fallback for merged history; session = the matched row's sess_ token when
present, else null — NEVER guessed (a manifest's controller_session is the
controller, not the lane session). Only entries whose head branch matches
``^(loop|fix)/`` are lane MRs; everything else is dropped (decision: repo-wide
any-branch tracking was rejected as noise). Entries whose join finds nothing
list with program/row_id/session null — honest, never hidden.

A repo whose probe or list call fails degrades SILENTLY (absent this cycle):
the registry has no lane to name in the degraded vocabulary, and NetCache
backoff still throttles retries. Memory-only: this module writes nothing.
"""

import json
import os
import re

from .config import TowerConfig
from .signals import _created_epoch_s as _iso_epoch

MERGES_TTL_S = 300           # the registry TTL (W4-L4 decision (a)); NOT 120
GIT_REMOTE = "git_remote"    # NetCache kind: the local remote probe
MR_LIST = "mr_registry_list"  # NetCache kind: the per-repo list call

# Lane branches (decision: ^(loop|fix)/ — the note grammar's two lane forms).
LANE_BRANCH_RE = re.compile(r"^(loop|fix)/")

# Closed state vocabulary, mirroring signals._STATE_MAP's casefolded joining
# of gh's uppercase OPEN/MERGED/CLOSED with gitlab's opened/merged/closed.
# glab's "locked" folds to "open": a locked MR IS open; blocked-ness already
# surfaces through the conflicts/mergeable detail.
_STATE_MAP = {"open": "open", "opened": "open", "locked": "open",
              "merged": "merged", "closed": "closed"}

# gitlab mergeable vocabulary (decision (d)). Only EXPLICIT values decide:
# modern detailed_merge_status "unresolved_conflicts" / legacy merge_status
# "cannot_be_merged" -> conflicts; "mergeable" / "can_be_merged" -> clean;
# everything else (not_approved, blocked, missing...) -> null, never guessed.
_GL_CONFLICT = {"unresolved_conflicts", "cannot_be_merged"}
_GL_CLEAN = {"mergeable", "can_be_merged"}


def _probe_remote(cache, config: TowerConfig, path: str, now_s):
    """(host, owner/repo slug) from the local checkout's remote, through
    NetCache at the registry TTL. (None, None) when the probe fails or the
    remote is neither github nor gitlab — the repo is skipped, not degraded."""
    res = cache.fetch((GIT_REMOTE, path, ""), ["git", "remote", "-v"], path,
                      now_s, MERGES_TTL_S,
                      config.network.backoff_base_s, config.network.backoff_max_s)
    if not res.ok:
        return (None, None)
    urls = [ln.split()[1] for ln in res.stdout.splitlines()
            if "(fetch)" in ln and len(ln.split()) >= 2]
    host = ("github" if any("github" in u for u in urls)
            else "gitlab" if any("gitlab" in u for u in urls) else None)
    if host is None:
        return (None, None)
    for u in urls:
        if host not in u:
            continue
        s = u.split("://", 1)[1] if "://" in u else u.split(":", 1)[-1]
        if "://" in u and "/" in s:
            s = s.split("/", 1)[1]        # drop the host segment of a URL
        s = s.strip("/")
        if s.endswith(".git"):
            s = s[:-len(".git")]
        if s and "/" in s:
            return (host, s)
    return (host, None)


def _list_argv(host: str, slug: str) -> list[str]:
    """The ONE list call per repo (argv templates in the module docstring)."""
    if host == "github":
        return ["gh", "pr", "list", "-R", slug, "--state", "all",
                "--limit", "100", "--json",
                "number,title,headRefName,isDraft,createdAt,updatedAt,"
                "mergedAt,state,mergeable,url,author"]
    return ["glab", "mr", "list", "-R", slug, "--all", "-F", "json", "-P", "100"]


def _adapt(obj, host: str) -> dict | None:
    """CLI-JSON object -> the normalized registry fields, or None when the
    object lacks a usable number (the same id-usability guard as signals M1).
    This is the ONLY place CLI key names live."""
    if not isinstance(obj, dict):
        return None
    if host == "github":
        author = obj.get("author")
        conflicts_raw = obj.get("mergeable")
        conflicts = (True if conflicts_raw == "CONFLICTING"
                     else False if conflicts_raw == "MERGEABLE" else None)
        return {"n": obj.get("number"), "branch": obj.get("headRefName"),
                "state_raw": obj.get("state"),
                "title": obj.get("title") if isinstance(obj.get("title"), str) else "",
                "draft": obj.get("isDraft") is True,
                "created": _iso_epoch(obj.get("createdAt")),
                "updated": _iso_epoch(obj.get("updatedAt")),
                "merged": obj.get("mergedAt"),
                "merged_epoch": _iso_epoch(obj.get("mergedAt")),
                "url": obj.get("url"),
                "author": (author or {}).get("login") if isinstance(author, dict) else None,
                "conflicts": conflicts}
    author = obj.get("author")
    detailed = obj.get("detailed_merge_status")
    legacy = obj.get("merge_status")
    conflicts = (True if (detailed in _GL_CONFLICT or legacy in _GL_CONFLICT)
                 else False if (detailed in _GL_CLEAN or legacy in _GL_CLEAN)
                 else None)
    merged_raw = obj.get("merged_at")
    return {"n": obj.get("iid"), "branch": obj.get("source_branch"),
            "state_raw": obj.get("state"),
            "title": obj.get("title") if isinstance(obj.get("title"), str) else "",
            "draft": obj.get("draft") is True or obj.get("work_in_progress") is True,
            "created": _iso_epoch(obj.get("created_at")),
            "updated": _iso_epoch(obj.get("updated_at")),
            "merged": merged_raw, "merged_epoch": _iso_epoch(merged_raw),
            "url": obj.get("web_url"),
            "author": ((author or {}).get("username")
                       if isinstance(author, dict) else None),
            "conflicts": conflicts}


def _entry(adapted: dict, host: str, repo_name: str, join) -> dict | None:
    """Adapted row -> one contract merge_row, or None when the row is not a
    usable lane MR (no number, no branch, off the lane-branch grammar, or a
    state outside the closed vocabulary)."""
    n = adapted["n"]
    branch = adapted["branch"]
    if not isinstance(n, int) or isinstance(n, bool):
        return None
    if not isinstance(branch, str) or not LANE_BRANCH_RE.match(branch):
        return None
    state = _STATE_MAP.get(str(adapted["state_raw"] or "").strip().casefold())
    if state is None:
        return None
    program, row_id, session = join(branch)
    merged_at = (int(adapted["merged_epoch"])
                 if adapted["merged_epoch"] is not None else None)
    return {"host": host,
            "repo": repo_name,
            "number": n,
            "title": adapted["title"],
            "branch": branch,
            "program": program,
            "row_id": row_id,
            "session": session,
            "state": state,
            "conflicts": adapted["conflicts"],
            "draft": adapted["draft"],
            "created_at": int(adapted["created"]) if adapted["created"] is not None else 0,
            "updated_at": int(adapted["updated"]) if adapted["updated"] is not None else 0,
            "merged_at": merged_at,
            "url": adapted["url"] if isinstance(adapted["url"], str) else "",
            "author": adapted["author"] if isinstance(adapted["author"], str) else ""}


def _manifest_branch_map(forge_root: str | None) -> dict[str, tuple[str, str]]:
    """branch -> (program, row_id) from the forge manifests — the persistent
    join fallback. Unreadable/unparseable/off-shape manifests are skipped
    silently (absence is not failure); first manifest per branch wins."""
    out: dict[str, tuple[str, str]] = {}
    if not forge_root:
        return out
    try:
        program_entries = list(os.scandir(forge_root))
    except OSError:
        return out
    for prog_ent in program_entries:
        if not prog_ent.is_dir():
            continue
        try:
            row_entries = list(os.scandir(prog_ent.path))
        except OSError:
            continue
        for row_ent in row_entries:
            if not row_ent.is_dir():
                continue
            try:
                with open(os.path.join(row_ent.path, "manifest.json"),
                          "rb") as fh:
                    parsed = json.loads(fh.read().decode("utf-8"))
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            if not isinstance(parsed, dict):
                continue
            program = parsed.get("program")
            row_id = parsed.get("row_id")
            branch = parsed.get("branch")
            if (isinstance(program, str) and program
                    and isinstance(row_id, str) and row_id
                    and isinstance(branch, str) and branch):
                out.setdefault(branch, (program, row_id))
    return out


def collect_registry(config: TowerConfig, lane_rows) -> list[dict]:
    """The registry for one collect cycle. ``lane_rows`` carries the LIVE
    program-note rows as (program_name, row_id, branch|None, sess_token|None,
    expanded_repo_path|None) in document order — collect owns the note parse
    and ~-expansion. Returns the contract ``merges`` array, newest
    updated_at first (ties: number desc, then repo/host for cross-repo
    determinism)."""
    # Join maps: live rows first (document order, first wins), manifests only
    # fill branches no live row claims (decision (c)).
    live: dict[str, tuple[str, str | None, str | None]] = {}
    repo_paths: dict[str, str | None] = {}  # expanded path -> config name|None
    for rc in config.repos:
        repo_paths[rc.path] = rc.name
    for program, row_id, branch, sess, repo_path in lane_rows:
        if repo_path and repo_path not in repo_paths:
            repo_paths[repo_path] = None
        if branch and branch not in live:
            live[branch] = (program, row_id, sess)
    forge_root = None
    if config.pending_launch_path:
        forge_root = os.path.join(
            os.path.dirname(config.pending_launch_path), "forge")
    manifests = _manifest_branch_map(forge_root)

    def join(branch: str):
        hit = live.get(branch)
        if hit is not None:
            return (hit[0], hit[1], hit[2])
        m = manifests.get(branch)
        if m is not None:
            return (m[0], m[1], None)  # never guess a session from manifests
        return (None, None, None)

    out: list[dict] = []
    for path, config_name in repo_paths.items():
        if not os.path.isdir(path):
            continue  # a vanished checkout is absence, not failure
        host, slug = _probe_remote(config.network_cache, config, path,
                                   config.now_s)
        if host is None or slug is None:
            continue
        # Display name: the config name when the path is a configured repo,
        # else the slug's tail (the repo's own name on the forge — worktree
        # checkouts like cleo-a3-bp read as "cleo"), else the dir basename.
        name = (config_name or slug.rsplit("/", 1)[-1]
                or os.path.basename(path.rstrip("/")) or path)
        res = config.network_cache.fetch(
            (MR_LIST, path, host), _list_argv(host, slug), path,
            config.now_s, MERGES_TTL_S,
            config.network.backoff_base_s, config.network.backoff_max_s)
        if not res.ok:
            continue  # silent this cycle (module docstring: no lane to name)
        try:
            items = json.loads(res.stdout)
        except ValueError:
            continue
        if not isinstance(items, list):
            continue
        for obj in items:
            adapted = _adapt(obj, host)
            if adapted is None:
                continue
            entry = _entry(adapted, host, name, join)
            if entry is not None:
                out.append(entry)
    out.sort(key=lambda e: (-e["updated_at"], -e["number"], e["repo"], e["host"]))
    return out
