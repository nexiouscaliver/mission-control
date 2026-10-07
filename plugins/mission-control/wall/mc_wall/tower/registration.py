"""Registration primitives (rg1, v1.12.0): the deterministic writes behind
``bin/mc-wall register|deregister|list|bind`` — the operator's hands for
program registration and lane-session binding (idea note 2026-10-07,
decisions D1-D3).

Design invariants:

- **Backup-first, always.** Every wall.json or vault-note write is preceded by
  a timestamped copy under ``<wall_home>/backups/`` — the caller backs up, the
  transform never writes on its own.
- **Pure transforms.** ``register_transform`` / ``deregister_transform`` /
  ``bind_row`` take the current document and return the new one plus human
  change lines — no I/O, so tests pin the exact mutations.
- **Idempotent by construction.** Re-running a transform on its own output
  reports "already"/"no changes" and mutates nothing (the CLI skips the
  backup+write entirely when nothing changed).
- **Preserve everything untouched.** wall.json token/port/repos/extra keys
  survive verbatim; bind rewrites ONE cell of ONE row (existing MR refs and
  ``verify:ok`` stay).
- **The runtime wall stays read-only.** These primitives never talk to the
  server; per-poll config re-reads and note re-reads make the edits live
  within one ~5s poll (contract v2 — no restart).
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import time

from . import discovery, notes

# Same slug grammar as discovery's FILENAME_RE (single source: the filename
# regex remains the authority; this standalone form validates bare slugs).
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def slug_from_path(path: str) -> str | None:
    """``…/mission-control-<slug>-program.md`` -> ``<slug>``; None for any
    other filename (register refuses to guess a program name)."""
    m = discovery.FILENAME_RE.match(os.path.basename(path))
    return m.group("slug") if m else None


def note_search_dirs(data: dict) -> list[str]:
    """Directories a slug lookup scans: the declared note_globs' dirs (the
    same places discovery scans — ``discovery.search_dirs`` semantics over
    the RAW document, defensive against unvalidated data) with the canonical
    default as fallback."""
    dirs = set()
    for p in data.get("programs") or []:
        if isinstance(p, dict) and isinstance(p.get("note_glob"), str):
            d = os.path.dirname(os.path.expanduser(p["note_glob"]))
            if d:
                dirs.add(d)
    return sorted(dirs) or [os.path.expanduser(discovery.DEFAULT_SEARCH_DIR)]


def declared_repos_from_data(data: dict):
    """RepoConfig tuples for wall.json's repos[] (defensive: malformed rows
    are skipped, never raised) — the seed ``derived_repos_for_note`` merges
    against. Hermetic by design: no discovery run, no filesystem probes."""
    from .config import RepoConfig

    out = []
    for r in data.get("repos") or []:
        if isinstance(r, dict) and isinstance(r.get("name"), str) \
                and isinstance(r.get("path"), str) and isinstance(r.get("host"), str):
            out.append(RepoConfig(name=r["name"], path=os.path.expanduser(r["path"]),
                                  host=r["host"]))
    return out


def find_note_by_slug(slug: str, dirs: list[str]) -> str | None:
    """First existing ``mission-control-<slug>-program.md`` under ``dirs``
    (deterministic: directory order, fixed filename)."""
    for d in dirs:
        path = os.path.join(d, "mission-control-%s-program.md" % slug)
        if os.path.isfile(path):
            return path
    return None


def backup_file(path: str, backups_dir: str, label: str,
                now: float | None = None) -> str:
    """Copy ``path`` to ``<backups_dir>/<label>-<YYYYmmdd-HHMMSS>`` (a same-
    second collision appends ``-N``) and return the destination. The caller
    owns when; this owns where and the name."""
    os.makedirs(backups_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S",
                          time.localtime(time.time() if now is None else now))
    dest = os.path.join(backups_dir, "%s-%s" % (label, stamp))
    n = 0
    while os.path.exists(dest):
        n += 1
        dest = os.path.join(backups_dir, "%s-%s-%d" % (label, stamp, n))
    shutil.copy2(path, dest)
    return dest


def write_json_atomic(path: str, data: dict) -> None:
    """Write wall.json atomically (tmp + rename) preserving the existing file
    mode (install's 0600 contract) — a reader mid-poll never sees a torn file."""
    mode = 0o600
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        pass
    tmp = "%s.tmp-%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


# -- register (SC-1) ------------------------------------------------------


def register_transform(data: dict, slug: str, note_path: str,
                       derived_repos) -> tuple[dict, list[str], bool]:
    """Declare ``slug`` -> ``note_path`` in wall.json programs[] (tag = slug
    for a NEW entry; an existing entry's tag is never rewritten — tags are
    join keys), drop ``slug`` from ignore[], merge ``derived_repos``
    (RepoConfig tuples, from the note's row path tokens) into repos[].
    Returns (new_data, change_lines, changed); idempotent — an already
    correct document returns changed=False untouched."""
    out = copy.deepcopy(data)
    lines: list[str] = []
    programs = out.setdefault("programs", [])
    entry = next((p for p in programs
                  if isinstance(p, dict) and p.get("program") == slug), None)
    if entry is None:
        programs.append({"program": slug, "tag": slug, "note_glob": note_path})
        lines.append("programs[]: registered %s -> %s" % (slug, note_path))
    elif entry.get("note_glob") != note_path:
        lines.append("programs[]: repointed %s: %s -> %s"
                     % (slug, entry.get("note_glob"), note_path))
        entry["note_glob"] = note_path
    else:
        lines.append("programs[]: %s already registered" % slug)
    ignore = out.get("ignore")
    if isinstance(ignore, list) and slug in ignore:
        ignore.remove(slug)
        lines.append('ignore[]: removed %s (declared again)' % slug)
    repos = out.setdefault("repos", [])
    known = {r.get("name"): r.get("path") for r in repos if isinstance(r, dict)}
    for repo in derived_repos:
        ex_path = known.get(repo.name)
        if ex_path is None:
            repos.append({"name": repo.name, "path": repo.path,
                          "host": repo.host})
            known[repo.name] = repo.path
            lines.append("repos[]: added %s (%s, %s)"
                         % (repo.name, repo.host, repo.path))
        elif os.path.expanduser(str(ex_path)) != repo.path:
            lines.append("repos[]: skipped %s at %s — name already bound to %s"
                         % (repo.name, repo.path, ex_path))
    changed = json.dumps(out, sort_keys=True) != json.dumps(data, sort_keys=True)
    return out, lines, changed


# -- deregister (SC-2) ----------------------------------------------------


def deregister_transform(data: dict, slug: str) -> tuple[dict, list[str], bool]:
    """Remove ``slug``'s declaration AND add it to ignore[] — deregistration
    is REAL: a grammar-correct note left on disk must not silently
    re-register via discovery (D2). Idempotent: an already-removed +
    already-ignored document returns changed=False."""
    out = copy.deepcopy(data)
    lines: list[str] = []
    programs = out.get("programs") or []
    keep = [p for p in programs
            if not (isinstance(p, dict) and p.get("program") == slug)]
    if len(keep) != len(programs):
        lines.append("programs[]: removed declaration for %s" % slug)
    out["programs"] = keep
    ignore = out.get("ignore")
    if isinstance(ignore, list):
        ignored = slug in ignore
    else:
        ignore = out["ignore"] = []
        ignored = False
    if not ignored:
        ignore.append(slug)
        lines.append("ignore[]: added %s — discovery will not re-register it" % slug)
    changed = json.dumps(out, sort_keys=True) != json.dumps(data, sort_keys=True)
    return out, lines, changed


# -- bind (SC-3) ----------------------------------------------------------

BIND_BOUND = "bound"
BIND_ALREADY = "already"
BIND_UNKNOWN_ROW = "unknown-row"


def bind_row(text: str, row_id: str, sess_id: str) -> tuple[str, str]:
    """Write ``sess_id`` into ``row_id``'s session/MR-artifacts cell.

    The FIRST ``sess_`` token in the cell is the wall's binding (notes
    SESS_RE), so an existing DIFFERENT token is REPLACED (rebinding a row is
    a legitimate repair); MR refs and ``verify:ok`` are preserved verbatim. A
    null cell (``—`` / ``n/a`` / empty) becomes the bare token. Returns
    (new_text, status): ``already`` when the exact token is already the
    binding (idempotent no-op), ``unknown-row`` when ``notes.locate_row``
    finds no such prompt-log row."""
    lineno = notes.locate_row(text, row_id)
    if lineno is None:
        return (text, BIND_UNKNOWN_ROW)
    lines = text.split("\n")
    cells = notes._row_cells(lines[lineno - 1])
    cell = cells[6]
    if notes.SESS_RE.findall(cell) == [sess_id]:
        return (text, BIND_ALREADY)  # exactly this token already binds the row
    if cell.strip() in notes._NULL_CELL:
        tokens = [sess_id]           # a null cell becomes the bare token
    else:
        # Sess token FIRST (row convention: `sess_x verify:ok #21`), the
        # surviving non-session tokens after it in their original order.
        tokens = [t for t in cell.split() if not notes.SESS_RE.fullmatch(t)]
        tokens.insert(0, sess_id)
    cells[6] = " ".join(tokens) if tokens else sess_id
    lines[lineno - 1] = "| " + " | ".join(cells) + " |"
    return ("\n".join(lines), BIND_BOUND)


def derived_repos_for_note(note_path: str, declared_repos, run_git=None):
    """RepoConfigs for the note's row path tokens — the SAME derivation
    discovery runs for undeclared programs (one truth: ``discovery._derive_
    repos``), so a register-declared program's lanes keep their git signals.
    Failures come back as human-readable degraded lines, never exceptions."""
    try:
        with open(note_path, "rb") as fh:
            parsed = notes.parse_note(fh.read().decode("utf-8"),
                                      note_path=os.path.abspath(note_path))
    except (OSError, UnicodeDecodeError):
        return [], ["note unreadable — no repos derived"]
    slug = slug_from_path(note_path) or ""
    degraded: list[str] = []
    repos = discovery._derive_repos([(os.path.abspath(note_path), parsed, slug)],
                                    declared_repos,
                                    run_git or discovery._run_git, degraded)
    return list(repos), degraded
