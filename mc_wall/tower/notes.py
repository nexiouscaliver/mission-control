"""Vault program-note parsing (spec §4.2): note discovery by glob (newest
mtime, lexicographic-path tie), prompt-log table detection over the CLOSED set
of header variants A/B (column names normalized: casefold + all whitespace
removed; the stored constants are POST-normalization so the comparison cannot
self-defeat), defensive row parsing with the explicit cell-count skip rule,
the closed status vocabulary, variant-A slug null rules, the repo/branch
whitespace-token grammar, artifacts-cell session-token / MR-ref extraction,
and the objective line. stdlib-only; ~ expansion belongs to collect (it owns
config), never here.
"""

import glob
import os
import re
from dataclasses import dataclass

VARIANT_A = 0  # combined repo/branch + slug columns (the live notes today)
VARIANT_B = 1  # separate repo/branch columns, no slug (documented future format)


def _norm(cell: str) -> str:
    """Column-name normalization: casefold with ALL internal whitespace removed."""
    return "".join(cell.split()).casefold()


# Header variants stored POST-normalization (plan F5): no internal spaces, so a
# live cell like "session/MR artifacts" normalizes to "session/mrartifacts".
HEADER_A = ("id", "wave", "lane", "repo/branch", "slug", "base",
            "session/mrartifacts", "status")
HEADER_B = ("id", "wave", "lane", "repo", "branch", "base_sha",
            "session/mrartifacts", "status")
_HEADER_BY_VARIANT = {VARIANT_A: HEADER_A, VARIANT_B: HEADER_B}
_HEADER_VARIANTS = {HEADER_A: VARIANT_A, HEADER_B: VARIANT_B}

# Closed lane-status vocabulary (§4, assumption 3): exact after trim; anything
# else — including the empty cell — parses UNPARSED with the raw preserved.
VOCAB = frozenset({"forged", "launched", "done", "partial", "failed", "parked", "in-flight"})

SESS_RE = re.compile(r"sess_[0-9a-f]{8,}")  # vault shorthand: >= 8 hex after sess_
BANG_RE = re.compile(r"!\d+")               # gitlab MR ref
HASH_RE = re.compile(r"#\d+")               # github PR ref

_BRANCH_RE = re.compile(r"^(loop/[A-Za-z0-9._-]+|main|master)$")
_SEP_CELL_RE = re.compile(r"^:?-+:?$")


@dataclass(frozen=True)
class NoteRow:
    row_id: str
    status_note: str           # status cell verbatim (as split+stripped)
    status_parsed: str         # VOCAB member or "UNPARSED"
    slug: str | None           # variant A cell value or None; variant B rows -> None
    repo_token: str | None     # raw path token from the cell (e.g. "~/.zcode/mc-wall"); None if n/a
    branch: str | None
    sess_token: str | None     # first sess_[0-9a-f]{8,} match in the artifacts cell
    mr_bang: str | None        # first "!\d+" match (gitlab ref)
    mr_hash: str | None        # first "#\d+" match (github ref)


@dataclass(frozen=True)
class NoteParse:
    objective: str
    rows: list[NoteRow]
    header_found: bool
    skipped: int


def find_note(note_glob: str) -> str | None:
    """0 matches -> None; >1 matches -> newest mtime, tie lexicographically
    smallest path (§4.2); never degrades — the caller owns entry 3."""
    matches = sorted(glob.glob(note_glob))
    if not matches:
        return None
    mtimes = {p: os.stat(p).st_mtime for p in matches}
    newest = max(mtimes.values())
    return min(p for p in matches if mtimes[p] == newest)


def parse_status(cell: str) -> tuple[str, str]:
    """(status_note verbatim, status_parsed): trimmed cell exactly in VOCAB
    else "UNPARSED" (the empty cell included)."""
    t = cell.strip()
    return (cell, t if t in VOCAB else "UNPARSED")


def parse_repo_branch(cell: str, variant: int) -> tuple[str | None, str | None]:
    """Repo/branch cell grammar -> (repo_token, branch).

    Null cells (``n/a``, ``—``, empty after strip) -> (None, None). Otherwise
    whitespace tokens: repo = the first token starting ``/`` or ``~/`` (a path;
    ``~`` expansion happens in collect, which owns config); branch = the first
    token matching ``loop/<slug>`` / ``main`` / ``master``; parenthesized and
    every other token are annotations (ignored). The same token grammar serves
    variant B: its separate repo and branch columns are each routed through
    this function (single-token cells parse naturally), keeping the null rules
    identical.
    """
    c = cell.strip()
    if c in {"n/a", "—", ""}:
        return (None, None)
    repo = branch = None
    for tok in c.split():
        if repo is None and (tok.startswith("/") or tok.startswith("~/")):
            repo = tok
        elif branch is None and _BRANCH_RE.match(tok):
            branch = tok
    return (repo, branch)


def _parse_slug(cell: str) -> str | None:
    """Variant-A slug cell: None for ``n/a`` / ``—`` / empty / a cell leading
    with ``n/a (`` (real corpus: ``n/a (plain lane)``); else verbatim."""
    if cell in {"n/a", "—", ""} or cell.startswith("n/a ("):
        return None
    return cell


def _row_cells(line: str) -> list[str]:
    stripped = line.strip()
    return [c.strip() for c in stripped.strip("|").split("|")]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(_SEP_CELL_RE.match(c) for c in cells)


def _header_variant(cells: list[str]) -> int | None:
    return _HEADER_VARIANTS.get(tuple(_norm(c) for c in cells))


def _parse_row(cells: list[str], variant: int) -> NoteRow:
    """Column access is guarded by the caller's cell-count check — no
    positional IndexError is reachable. Variant A indices: id 0, repo/branch 3,
    slug 4, artifacts 6, status 7. Variant B: id 0, repo 3, branch 4,
    artifacts 6, status 7, no slug -> None."""
    status_note, status_parsed = parse_status(cells[7])
    if variant == VARIANT_A:
        repo_token, branch = parse_repo_branch(cells[3], VARIANT_A)
        slug = _parse_slug(cells[4])
    else:
        repo_token, _ = parse_repo_branch(cells[3], VARIANT_B)
        _, branch = parse_repo_branch(cells[4], VARIANT_B)
        slug = None
    artifacts = cells[6]
    sess = SESS_RE.search(artifacts)
    bang = BANG_RE.search(artifacts)
    ref = HASH_RE.search(artifacts)
    return NoteRow(
        row_id=cells[0],
        status_note=status_note,
        status_parsed=status_parsed,
        slug=slug,
        repo_token=repo_token,
        branch=branch,
        sess_token=sess.group(0) if sess else None,
        mr_bang=bang.group(0) if bang else None,
        mr_hash=ref.group(0) if ref else None,
    )


def _objective(lines: list[str]) -> str:
    """Text after the first ``:`` (trimmed) of the first line whose trimmed
    lowercase form starts with ``objective``; ``""`` if none (§4.2)."""
    for line in lines:
        if line.strip().lower().startswith("objective"):
            return line.partition(":")[2].strip()
    return ""


def parse_note(text: str) -> NoteParse:
    """Parse one vault program note. A table starts at a line whose normalized
    cell-name sequence equals HEADER_A or HEADER_B; the line immediately after
    a matched header is consumed as separator furniture when separator-shaped;
    data rows run until a non-table line. Malformed rows (fewer cells than the
    header, empty id, separator-shaped) are skipped and counted — no exception
    path exists. Prose-bullet prompt logs are out of scope: a note with no
    matching header yields header_found=False, rows == [] (entry 3 is the
    caller's)."""
    lines = text.split("\n")
    rows: list[NoteRow] = []
    skipped = 0
    header_found = False
    variant: int | None = None
    after_header = False  # the next table line may be separator furniture
    for line in lines:
        if not line.strip().startswith("|"):
            variant = None      # a non-table line ends the open table
            after_header = False
            continue
        cells = _row_cells(line)
        if after_header and _is_separator(cells):
            after_header = False  # furniture directly under the header: not counted
            continue
        after_header = False
        detected = _header_variant(cells)
        if detected is not None:   # a table starts here (or a second table
            header_found = True    # begins after a prior table in the same note)
            variant = detected
            after_header = True
            continue
        if variant is None:
            continue               # stray table line outside any table
        if (_is_separator(cells)
                or len(cells) < len(_HEADER_BY_VARIANT[variant])
                or cells[0] == ""):
            skipped += 1           # defensive skip: counted, never raised
            continue
        rows.append(_parse_row(cells, variant))
    return NoteParse(objective=_objective(lines), rows=rows,
                     header_found=header_found, skipped=skipped)
