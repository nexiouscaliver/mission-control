"""Vault program-note parsing (spec §4.2): note discovery by glob (newest
mtime, lexicographic-path tie), prompt-log table detection over the CLOSED set
of header variants A/B/C (column names normalized: casefold + all whitespace
removed; the stored constants are POST-normalization so the comparison cannot
self-defeat), defensive row parsing with the explicit cell-count skip rule,
the closed status vocabulary, variant-A slug null rules, the repo/branch
whitespace-token grammar, artifacts-cell session-token / MR-ref / verified-
token extraction, the variant-C deps cell, and the objective line.
Contract v2 (wall-overhaul): off-grammar input is FAIL-VISIBLE — every
skipped row, off-vocabulary status, and missing/bolded objective line is
collected into ``NoteParse.defects`` as ``{"note_path", "line", "defect",
"row_id"}`` instead of silently disappearing. stdlib-only; ~ expansion
belongs to collect (it owns config), never here.

Wall-honesty (v1.11.1, W5-L5 — the 2026-10-07 lane-invisibility incident):
FOUR formerly silent classes are now defects — stray lane-shaped rows outside
any table (the incident's killer), blank lines INSIDE the table region
(tolerated with a defect so a template leftover can never zero a program),
extra-cell width mismatches (previously silently truncated), and non-empty
repo/branch cells parsing to None (previously silently dropped git signals).

Cells containing escaped pipes (``\\|``) get no special handling — the spec
defines no escape grammar and the corpus has none.
"""

import glob
import os
import re
from dataclasses import dataclass, field

VARIANT_A = 0  # combined repo/branch + slug columns (the live notes today)
VARIANT_B = 1  # separate repo/branch columns, no slug (documented future format)
VARIANT_C = 2  # variant A + a trailing deps column (wall-overhaul contract v2)

# Human names for defect strings ("extra cells under variant-A header").
VARIANT_NAME = {VARIANT_A: "A", VARIANT_B: "B", VARIANT_C: "C"}


def _norm(cell: str) -> str:
    """Column-name normalization: casefold with ALL internal whitespace removed."""
    return "".join(cell.split()).casefold()


# Header variants stored POST-normalization (plan F5): no internal spaces, so a
# live cell like "session/MR artifacts" normalizes to "session/mrartifacts".
HEADER_A = ("id", "wave", "lane", "repo/branch", "slug", "base",
            "session/mrartifacts", "status")
HEADER_B = ("id", "wave", "lane", "repo", "branch", "base_sha",
            "session/mrartifacts", "status")
HEADER_C = HEADER_A + ("deps",)  # contract v2: deps LAST, 9 cells
_HEADER_BY_VARIANT = {VARIANT_A: HEADER_A, VARIANT_B: HEADER_B, VARIANT_C: HEADER_C}
_HEADER_VARIANTS = {HEADER_A: VARIANT_A, HEADER_B: VARIANT_B, HEADER_C: VARIANT_C}

# Closed lane-status vocabulary (§4, assumption 3): exact after trim; anything
# else — including the empty cell — parses UNPARSED with the raw preserved.
VOCAB = frozenset({"forged", "launched", "done", "partial", "failed", "parked", "in-flight"})

SESS_RE = re.compile(r"sess_[0-9a-f]{8,}")  # vault shorthand: >= 8 hex after sess_
BANG_RE = re.compile(r"!\d+")               # gitlab MR ref
HASH_RE = re.compile(r"#\d+")               # github PR ref

# Controller-written verified flag: a substring test on the artifacts cell (a
# controller-only convention — incidental occurrences in free text are accepted
# by design; it cannot collide with the SESS_RE/BANG_RE/HASH_RE extractions).
VERIFY_TOKEN = "verify:ok"

# Branch vocabulary: any lowercase-kebab namespace prefix followed by a
# branch name (loop/<slug>, fix/<name>, backport/<name>, alarm-fixes/<name>,
# hsp/<name>, release/<tag>, ...), plus bare main/master. The prefix list
# grew twice (loop/ then fix/) before the hsp resurrection exposed real
# fleets of backport/ + alarm-fixes/ + hsp/ branches; the general namespaced
# form is the durable rule (wall-overhaul closeout follow-up, 2026-10-07).
_BRANCH_RE = re.compile(
    r"^([a-z][a-z0-9]*(?:-[a-z0-9]+)*/[A-Za-z0-9._-]+|main|master)$")
_SEP_CELL_RE = re.compile(r"^:?-+:?$")

# W5-L5 stray-row class: a lane row's first cell is a row ID (``W1-L0``,
# ``A9-L1`` — the prompt-log grammar's id shape), which distinguishes genuine
# stray lane rows from the notes' OTHER markdown tables (the Waves table's
# rows lead with wave NUMBERS). Without this shape test every markdown table
# in a program note would flood the defect surface.
ROW_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*-L\d+$")


@dataclass(frozen=True)
class NoteRow:
    row_id: str
    status_note: str           # status cell verbatim (as split+stripped)
    status_parsed: str         # VOCAB member or "UNPARSED"
    slug: str | None           # variant A/C cell value or None; variant B rows -> None
    repo_token: str | None     # raw path token from the cell (e.g. "~/.zcode/mc-wall"); None if n/a
    branch: str | None
    sess_token: str | None     # first sess_[0-9a-f]{8,} match in the artifacts cell
    mr_bang: str | None        # first "!\d+" match (gitlab ref)
    mr_hash: str | None        # first "#\d+" match (github ref)
    verified: bool = False     # VERIFY_TOKEN substring in the artifacts cell
    deps: tuple[str, ...] = ()  # variant C deps cell (row_ids); () under A/B


@dataclass(frozen=True)
class NoteParse:
    objective: str
    rows: list[NoteRow]
    header_found: bool
    skipped: int
    # Contract v2 item 2: fail-visible parse defects, each
    # {"note_path", "line" (1-based), "defect", "row_id" (None when unknown)}.
    defects: list[dict] = field(default_factory=list)


def find_note(note_glob: str) -> str | None:
    """0 matches -> None; >1 matches -> newest mtime, tie lexicographically
    smallest path (§4.2); never degrades — the caller owns entry 3. A match
    that vanishes between glob and stat (OSError) is dropped from candidacy;
    if EVERY match vanishes, None is returned — the caller's glob-miss path —
    so find_note never raises past a per-program fail-open guard."""
    matches = sorted(glob.glob(note_glob))
    if not matches:
        return None
    mtimes = {}
    for p in matches:
        try:
            mtimes[p] = os.stat(p).st_mtime
        except OSError:
            continue  # vanished between glob and stat: no longer a candidate
    if not mtimes:
        return None  # every match vanished -> same as a glob miss
    newest = max(mtimes.values())
    return min(p for p in mtimes if mtimes[p] == newest)


def parse_status(cell: str) -> tuple[str, str]:
    """(status_note verbatim, status_parsed): trimmed cell exactly in VOCAB
    else "UNPARSED" (the empty cell included)."""
    t = cell.strip()
    return (cell, t if t in VOCAB else "UNPARSED")


_NULL_CELL = {"n/a", "—", ""}


def _cell_is_null(cell: str) -> bool:
    return cell.strip() in _NULL_CELL


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
    """Variant-A/C slug cell: None for ``n/a`` / ``—`` / empty / a cell leading
    with ``n/a (`` (real corpus: ``n/a (plain lane)``); else verbatim."""
    if cell in {"n/a", "—", ""} or cell.startswith("n/a ("):
        return None
    return cell


def parse_deps(cell: str) -> tuple[str, ...]:
    """Variant-C deps cell -> row_id tuple: one or more row_ids separated by
    commas and/or whitespace; ``—`` / ``n/a`` / empty -> ()."""
    c = cell.strip()
    if c in {"—", "n/a", ""} or c.startswith("n/a ("):
        return ()
    return tuple(t for t in re.split(r"[,\s]+", c) if t and t not in {"—", "n/a"})


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
    artifacts 6, status 7, no slug -> None. Variant C = A plus deps at 8."""
    status_note, status_parsed = parse_status(cells[7])
    if variant == VARIANT_B:
        repo_token, _ = parse_repo_branch(cells[3], VARIANT_B)
        _, branch = parse_repo_branch(cells[4], VARIANT_B)
        slug = None
        deps: tuple[str, ...] = ()
    else:  # variants A and C share the A column layout
        repo_token, branch = parse_repo_branch(cells[3], VARIANT_A)
        slug = _parse_slug(cells[4])
        deps = parse_deps(cells[8]) if variant == VARIANT_C else ()
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
        verified=VERIFY_TOKEN in artifacts,
        deps=deps,
    )


def _objective(lines: list[str]) -> str:
    """Text after the first ``:`` (trimmed) of the first line whose trimmed
    lowercase form starts with ``objective``; ``""`` if none (§4.2)."""
    for line in lines:
        if line.strip().lower().startswith("objective"):
            return line.partition(":")[2].strip()
    return ""


_OBJ_NEAR_MISS_RE = re.compile(r"^[\*_\s]*objective\b", re.IGNORECASE)


def _objective_defect(lines: list[str]) -> dict | None:
    """Contract v2 declared-path objective check: a note whose objective did
    not parse gets ONE defect — naming the near-miss line when an objective
    was attempted but malformed (bolded/emphasized), else "objective missing"."""
    for n, line in enumerate(lines, start=1):
        if _OBJ_NEAR_MISS_RE.match(line.strip()):
            return {"line": n, "defect": "objective not parsed (bolded/malformed)",
                    "row_id": None}
    return {"line": 0, "defect": "objective missing", "row_id": None}


def _cell_defects(cells: list[str], variant: int, lineno: int,
                  note_path: str, row_id: str) -> list[dict]:
    """A4 cell-parse defects: a non-empty repo/branch cell that parses to None
    names the cell and what failed — the lane loses its git signals silently
    no longer. Null cells (``n/a`` / ``—`` / empty) never defect."""
    out: list[dict] = []
    if variant == VARIANT_B:
        repo_cell, branch_cell = cells[3], cells[4]
        if not _cell_is_null(repo_cell) and parse_repo_branch(repo_cell, variant)[0] is None:
            out.append({"note_path": note_path, "line": lineno,
                        "defect": f"repo cell '{repo_cell}' has no path token",
                        "row_id": row_id})
        if not _cell_is_null(branch_cell) and parse_repo_branch(branch_cell, variant)[1] is None:
            out.append({"note_path": note_path, "line": lineno,
                        "defect": f"branch cell '{branch_cell}' has no branch token",
                        "row_id": row_id})
        return out
    cell = cells[3]
    if _cell_is_null(cell):
        return out
    repo, branch = parse_repo_branch(cell, variant)
    if repo is None:
        out.append({"note_path": note_path, "line": lineno,
                    "defect": f"repo/branch cell '{cell}' has no repo path token",
                    "row_id": row_id})
    if branch is None:
        out.append({"note_path": note_path, "line": lineno,
                    "defect": f"repo/branch cell '{cell}' has no branch token",
                    "row_id": row_id})
    return out


def locate_row(text: str, row_id: str) -> int | None:
    """1-based line number of the prompt-log DATA row whose id cell equals
    ``row_id`` (first match, tables scanned in document order) — the
    write-side anchor ``bin/mc-wall bind`` edits against. Same header/variant
    detection as ``parse_note`` (one truth); a row id that appears only
    outside any table, or not at all, yields None (bind refuses unknown rows
    instead of editing a stray line)."""
    variant: int | None = None
    after_header = False
    for lineno, line in enumerate(text.split("\n"), start=1):
        if not line.strip().startswith("|"):
            variant = None
            after_header = False
            continue
        cells = _row_cells(line)
        if after_header and _is_separator(cells):
            after_header = False
            continue
        after_header = False
        detected = _header_variant(cells)
        if detected is not None:
            variant = detected
            after_header = True
            continue
        if variant is None:
            continue
        if _is_separator(cells):
            continue
        if cells and cells[0] == row_id and len(cells) >= len(_HEADER_BY_VARIANT[variant]):
            return lineno
    return None


def parse_note(text: str, note_path: str = "") -> NoteParse:
    """Parse one vault program note. A table starts at a line whose normalized
    cell-name sequence equals HEADER_A, HEADER_B or HEADER_C; the line
    immediately after a matched header is consumed as separator furniture when
    separator-shaped; data rows run until a non-table line. Malformed rows
    (fewer cells than the header, empty id, separator-shaped) are skipped,
    counted — no exception path exists — and (contract v2) recorded in
    ``defects`` with their 1-based line number and row_id when known. A row
    that parses with an off-vocabulary status keeps rendering AND carries a
    defect. Prose-bullet prompt logs are out of scope: a note with no matching
    header yields header_found=False, rows == [] (entry 3 is the caller's).

    Wall-honesty (v1.11.1, the 2026-10-07 incident): a BLANK line inside a
    table region — followed within the next 2 lines by another table line —
    no longer closes the table (rows beneath it parse) but records a
    "blank line inside prompt-log table" defect; a lane-shaped line (row-id
    first cell, >= 2 cells) OUTSIDE any table records a "lane row outside
    prompt-log table" defect instead of vanishing; a row with MORE cells than
    its header parses its known-prefix cells AND defects per row; a non-empty
    repo/branch cell parsing to None defects naming the cell."""
    lines = text.split("\n")
    rows: list[NoteRow] = []
    skipped = 0
    defects: list[dict] = []
    header_found = False
    variant: int | None = None
    after_header = False  # the next table line may be separator furniture
    table_open = False    # variant is not None OR a header/separator just ran

    def _table_line_ahead(idx: int) -> bool:
        # A2's look-ahead: another table line within the next 2 lines.
        for j in (idx + 1, idx + 2):
            if j < len(lines) and lines[j].strip().startswith("|"):
                return True
        return False

    for lineno, line in enumerate(lines, start=1):
        if not line.strip().startswith("|"):
            if line.strip() == "" and table_open and _table_line_ahead(lineno - 1):
                # A2: a blank INSIDE the table region (table continues within
                # 2 lines) — tolerate AND defect; yesterday this silently
                # closed the table and dropped every row beneath it.
                defects.append({"note_path": note_path, "line": lineno,
                                "defect": "blank line inside prompt-log table",
                                "row_id": None})
                continue
            variant = None      # a non-table line ends the open table
            after_header = False
            table_open = False
            continue
        cells = _row_cells(line)
        if after_header and _is_separator(cells):
            after_header = False  # furniture directly under the header: not counted
            table_open = True
            continue
        after_header = False
        detected = _header_variant(cells)
        if detected is not None:   # a table starts here (or a second table
            header_found = True    # begins after a prior table in the same note)
            variant = detected
            after_header = True
            table_open = True
            continue
        if variant is None:
            # A1: a lane-shaped line outside any table defects instead of
            # silently vanishing. Row-ID-shaped first cell + >= 2 cells (the
            # id shape distinguishes lane rows from other markdown tables);
            # separator furniture and non-prompt-log headers are not lane rows.
            if (len(cells) >= 2 and not _is_separator(cells)
                    and cells[0] and ROW_ID_RE.match(cells[0])):
                defects.append({"note_path": note_path, "line": lineno,
                                "defect": "lane row outside prompt-log table",
                                "row_id": cells[0]})
            continue
        table_open = True
        if _is_separator(cells):
            skipped += 1           # defensive skip: counted, never raised
            defects.append({"note_path": note_path, "line": lineno,
                            "defect": "row skipped: separator row mid-table",
                            "row_id": cells[0] if cells and cells[0] else None})
            continue
        header_len = len(_HEADER_BY_VARIANT[variant])
        if len(cells) < header_len:
            skipped += 1
            defects.append({"note_path": note_path, "line": lineno,
                            "defect": f"row skipped: {len(cells)} cells"
                                      f" < {header_len}",
                            "row_id": cells[0] if cells and cells[0] else None})
            continue
        if cells[0] == "":
            skipped += 1
            defects.append({"note_path": note_path, "line": lineno,
                            "defect": "row skipped: empty id cell", "row_id": None})
            continue
        if len(cells) > header_len:
            # A3: extra cells parse-truncate at the known prefix — no longer
            # silently: each over-wide row defects once.
            defects.append({"note_path": note_path, "line": lineno,
                            "defect": f"{len(cells) - header_len} extra cells"
                                      f" under variant-{VARIANT_NAME[variant]} header",
                            "row_id": cells[0]})
        row = _parse_row(cells, variant)
        defects.extend(_cell_defects(cells, variant, lineno, note_path, row.row_id))
        if row.status_parsed == "UNPARSED":
            defects.append({"note_path": note_path, "line": lineno,
                            "defect": f"status not in vocabulary:"
                                      f" {row.status_note.strip()!r}",
                            "row_id": row.row_id})
        rows.append(row)
    objective = _objective(lines)
    if objective == "":
        obj = _objective_defect(lines)
        if obj is not None:
            defects.append({"note_path": note_path, **obj})
    defects.sort(key=lambda d: d["line"])  # line order; stable within a line
    return NoteParse(objective=objective, rows=rows,
                     header_found=header_found, skipped=skipped,
                     defects=defects)
