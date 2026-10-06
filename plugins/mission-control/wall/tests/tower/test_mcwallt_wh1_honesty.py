"""W5-L5 wall-honesty invariants (shared contract pinned 2026-10-07): the
lane-invisibility incident's four silent-drop classes become visible defects —
stray lane rows outside any table (A1), blank lines inside the prompt-log
table tolerated WITH a defect (A2), extra-cell width mismatches defect per row
(A3), non-empty repo/branch cells that parse to None name the cell (A4) — plus
the forge-manifest absent-row alarm (A5: write-once manifests cross-checked
against the parse; survives table breaks of ANY cause) and session
conservation with orphan surfacing (A6: every window session is lane-bound,
unmapped, master, orphan-tagged, or background — anything else is a
conservation defect; tagged-but-unbound sessions surface in the new
``sessions_orphaned`` state key).

Every case is fixture-only (zero live reads); defect strings are pinned
verbatim — they are the operator-facing alarm surface.
"""

import json
import os

from mc_wall.tower import ProgramConfig, TowerConfig, collect_state, notes
from mc_wall.tower import contract
from tests.tower.conftest import (MCWALLT_WORLD_LANE, MCWALLT_WORLD_MASTER,
                                  MCWALLT_WORLD_SUBAGENT,
                                  MCWALLT_WORLD_UNMAPPED, mcwallt_make_db,
                                  mcwallt_make_note, mcwallt_world)

HEADER_A_LINE = "| id | wave | lane | repo/branch | slug | base | session/MR artifacts | status |"
SEP_LINE = "|---|---|---|---|---|---|---|---|"


# --- A1: stray lane rows outside any table -------------------------------------

def test_wh1_a1_stray_row_after_table_closed():
    # The charter's canonical stray: the table ended (prose closes it), then a
    # lane row follows. Yesterday: silently dropped. Today: a defect naming
    # the line and the row id.
    text = "\n".join([
        "objective: wh1 stray world",
        HEADER_A_LINE, SEP_LINE,
        "| W1-L1 | W1 | L1 | ~/repos/mc loop/x | s | n/a | n/a | done |",
        "",
        "Some prose closes the table.",
        "| W1-L9 | W1 | L9 | ~/repos/mc loop/y | s | n/a | n/a | forged |",
    ])
    parsed = notes.parse_note(text, note_path="/vault/wh1_stray.md")
    assert [r.row_id for r in parsed.rows] == ["W1-L1"]  # stray never parses as a row
    strays = [d for d in parsed.defects if d["defect"] == "lane row outside prompt-log table"]
    assert strays == [{"note_path": "/vault/wh1_stray.md", "line": 7,
                       "defect": "lane row outside prompt-log table", "row_id": "W1-L9"}]


def test_wh1_a1_stray_row_with_no_table_at_all():
    parsed = notes.parse_note("\n".join([
        "objective: no table here",
        "| W2-L5 | W2 | L5 | ~/repos/mc loop/z | s | n/a | n/a | forged |",
    ]))
    assert parsed.header_found is False
    assert parsed.rows == []
    assert [d["defect"] for d in parsed.defects] == ["lane row outside prompt-log table"]
    assert parsed.defects[0]["row_id"] == "W2-L5"


def test_wh1_a1_non_lane_markdown_tables_stay_unflagged():
    # W5-L5 decision: the stray class is ROW-ID-SHAPED first cells — program
    # notes carry other markdown tables (the Waves table's rows lead with wave
    # NUMBERS, not row ids) and those must not flood the defect surface.
    parsed = notes.parse_note("\n".join([
        "objective: wh1 waves table",
        "| wave | lane | package | owned files | depends on | status |",
        "|---|---|---|---|---|---|",
        "| 1 | W1-L0 | PR #14 unblock | fix branch files | — | done (merged b5a1c7b) |",
        "| 4 | W4-L4 | MR registry | tower files | PR #18 | forged |",
        "",
        "Prose after the waves table.",
    ]))
    assert parsed.defects == []


def test_wh1_a1_separator_and_headers_outside_table_not_stray():
    # Furniture (a separator line) and non-prompt-log headers outside a table
    # are not lane rows: no stray defect, parse unchanged.
    parsed = notes.parse_note("\n".join([
        "objective: wh1 furniture",
        "|---|---|---|",
        "| alpha | beta | gamma |",
    ]))
    assert parsed.defects == []


# --- A2: blank-line tolerance + defect (THE INCIDENT FIXTURE) -------------------

def test_wh1_a2_incident_shape_parses_all_rows_and_carries_defect():
    # EXPECT-1: the exact 2026-10-07 incident shape — header, separator, ONE
    # BLANK LINE, then valid rows. Yesterday this parsed ZERO rows silently;
    # today it parses ALL rows AND carries the blank-line defect.
    text = "\n".join([
        "objective: wh1 incident world",
        HEADER_A_LINE,       # line 2
        SEP_LINE,            # line 3
        "",                  # line 4 — the incident's blank line
        "| W1-L0 | 1 | L0 | ~/r fix/unmapped-recency-order | s | d939 | sess_00000001 | done |",
        "| W2-L1 | 2 | L1 | ~/r loop/wall-tower | s | b5 | sess_00000002 | done |",
        "| W2-L2 | 2 | L2 | ~/r loop/wall-web | s | b5 | sess_00000003 | done |",
    ])
    parsed = notes.parse_note(text, note_path="/vault/wh1_incident.md")
    assert parsed.header_found is True
    assert parsed.skipped == 0
    assert [r.row_id for r in parsed.rows] == ["W1-L0", "W2-L1", "W2-L2"]
    assert parsed.defects == [{"note_path": "/vault/wh1_incident.md", "line": 4,
                               "defect": "blank line inside prompt-log table",
                               "row_id": None}]


def test_wh1_a2_blank_between_rows_and_between_header_and_separator():
    # Blank tolerance covers the whole table region: header/separator gap and
    # row gaps alike, each blank defecting once.
    parsed = notes.parse_note("\n".join([
        "objective: gaps",
        HEADER_A_LINE,  # 2
        "",             # 3 blank between header and separator
        SEP_LINE,       # 4
        "| W1-L1 | W1 | L1 | ~/r loop/x | s | n/a | n/a | forged |",  # 5
        "",             # 6 blank between rows
        "| W1-L2 | W1 | L2 | ~/r main | s | n/a | n/a | done |",      # 7
    ]))
    assert [r.row_id for r in parsed.rows] == ["W1-L1", "W1-L2"]
    blanks = [d for d in parsed.defects if d["defect"] == "blank line inside prompt-log table"]
    assert [d["line"] for d in blanks] == [3, 6]


def test_wh1_a2_blank_then_two_prose_lines_closes_the_table():
    # The look-ahead bound: a blank followed within 2 lines by NO table line
    # closes the table normally (no defect for ordinary prose spacing), and a
    # later lane row is then a STRAY — the tolerant window is 2 lines, not
    # unbounded.
    parsed = notes.parse_note("\n".join([
        "objective: bounded window",
        HEADER_A_LINE, SEP_LINE,
        "| W1-L1 | W1 | L1 | ~/r loop/x | s | n/a | n/a | done |",
        "",              # 5 blank
        "prose line",    # 6
        "prose line",    # 7 (beyond the 2-line look-ahead)
        "| W1-L2 | W1 | L2 | ~/r main | s | n/a | n/a | done |",  # 8 stray
    ]))
    assert [r.row_id for r in parsed.rows] == ["W1-L1"]
    kinds = [(d["line"], d["defect"]) for d in parsed.defects]
    assert kinds == [(8, "lane row outside prompt-log table")]  # no blank defect


# --- A3: row-width mismatch defects ---------------------------------------------

def test_wh1_a3_extra_cells_parse_prefix_and_defect():
    # A 9-cell row under the 8-cell HEADER_A: the known-prefix cells parse AND
    # the row defects (yesterday: extra cells silently truncated — the
    # docstring admitted it).
    parsed = notes.parse_note("\n".join([
        "objective: width",
        HEADER_A_LINE, SEP_LINE,
        "| W1-L1 | W1 | L1 | ~/r loop/x | s | n/a | n/a | done | EXTRA |",
    ]))
    assert [r.row_id for r in parsed.rows] == ["W1-L1"]
    assert parsed.rows[0].status_parsed == "done"
    assert parsed.defects == [{"note_path": "", "line": 4,
                               "defect": "1 extra cells under variant-A header",
                               "row_id": "W1-L1"}]


def test_wh1_a3_mixed_widths_defect_per_row():
    parsed = notes.parse_note("\n".join([
        "objective: mixed widths",
        HEADER_A_LINE, SEP_LINE,
        "| W1-L1 | W1 | L1 | ~/r loop/x | s | n/a | n/a | done | A | B |",
        "| W1-L2 | W1 | L2 | ~/r main | s | n/a | n/a | done |",
        "| W1-L3 | W1 | L3 | ~/r main | s | n/a | n/a | done | C |",
    ]))
    width_defects = [d for d in parsed.defects if "extra cells" in d["defect"]]
    assert [(d["row_id"], d["defect"]) for d in width_defects] == \
        [("W1-L1", "2 extra cells under variant-A header"),
         ("W1-L3", "1 extra cells under variant-A header")]
    assert [r.row_id for r in parsed.rows] == ["W1-L1", "W1-L2", "W1-L3"]


def test_wh1_a3_extra_cells_under_variant_c():
    header_c = HEADER_A_LINE + " deps |"
    parsed = notes.parse_note("\n".join([
        "objective: width c",
        header_c, "|---|---|---|---|---|---|---|---|---|",
        "| W2-L1 | W2 | L1 | ~/r loop/x | s | n/a | n/a | forged | W1-L0 | SURPLUS |",
    ]))
    assert parsed.rows[0].deps == ("W1-L0",)
    assert [d["defect"] for d in parsed.defects if "extra cells" in d["defect"]] == \
        ["1 extra cells under variant-C header"]


# --- A4: cell-parse defects ------------------------------------------------------

def test_wh1_a4_annotation_cell_defects_naming_the_cell():
    # Charter-literal: a non-empty repo/branch cell that yields repo=None or
    # branch=None defects naming the cell and what failed. The corpus's "plain
    # lane" annotation cell is the canonical case — it now SAYS it carries no
    # git signals instead of silently losing them.
    parsed = notes.parse_note("\n".join([
        "objective: cells",
        HEADER_A_LINE, SEP_LINE,
        "| W1-L4 | W1 | L4 | plugin cache 1.3.0 (plain lane) | n/a | n/a | n/a | forged |",
    ]))
    assert [d["defect"] for d in parsed.defects] == [
        "repo/branch cell 'plugin cache 1.3.0 (plain lane)' has no repo path token",
        "repo/branch cell 'plugin cache 1.3.0 (plain lane)' has no branch token",
    ]
    assert all(d["row_id"] == "W1-L4" and d["line"] == 4 for d in parsed.defects)


def test_wh1_a4_partial_cell_defects_only_the_missing_half():
    parsed = notes.parse_note("\n".join([
        "objective: half",
        HEADER_A_LINE, SEP_LINE,
        "| W1-L1 | W1 | L1 | ~/repos/mc | s | n/a | n/a | done |",  # repo, no branch
    ]))
    assert [d["defect"] for d in parsed.defects] == \
        ["repo/branch cell '~/repos/mc' has no branch token"]


def test_wh1_a4_null_cells_never_defect():
    parsed = notes.parse_note("\n".join([
        "objective: nulls",
        HEADER_A_LINE, SEP_LINE,
        "| W1-A | W1 | A | n/a | n/a | n/a | n/a | done |",
        "| W1-B | W1 | B | — | n/a | n/a | n/a | done |",
        "| W1-C | W1 | C |  | n/a | n/a | n/a | done |",
    ]))
    assert parsed.defects == []


# --- A5: forge-manifest cross-check ----------------------------------------------

def _make_forge(tmp_path, program, row_ids):
    root = tmp_path / "forge" / program
    for rid in row_ids:
        d = root / rid
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(
            json.dumps({"row_id": rid, "program": program}), encoding="utf-8")
    return str(tmp_path / "forge")


def test_wh1_a5_manifest_absent_row_alarms_at_collect(tmp_path):
    # The alarm that would have caught the incident on day one: a forged row
    # whose manifest EXISTS under ~/.mc-wall/forge/<program>/ but whose row_id
    # is absent from the note parse defects — at line 0, so it sorts TOP of
    # the program's parse_defects. Manifests are write-once machine records;
    # this survives table breaks of ANY cause.
    forge_root = _make_forge(tmp_path, "wh1prog", ["W1-L1", "W1-L2"])
    note = mcwallt_make_note(tmp_path, "wh1_manifest_note.md", [
        "objective: manifest alarm",
        HEADER_A_LINE, SEP_LINE,
        "| W1-L1 | W1 | L1 | n/a | n/a | n/a | n/a | done |",  # W1-L2's row is GONE
    ])
    cfg = TowerConfig(
        db_path=mcwallt_make_db(tmp_path),
        programs=(ProgramConfig(program="wh1prog", tag="t", note_glob=note),),
        forge_root=forge_root)
    state = collect_state(cfg)
    prog = state["programs"][0]
    assert prog["parse_defects"] == [
        {"note_path": note, "line": 0,
         "defect": "forged row W1-L2 absent from note parse (manifest exists)",
         "row_id": "W1-L2"}]
    assert [l["row_id"] for l in prog["lanes"]] == ["W1-L1"]


def test_wh1_a5_manifest_alarm_survives_total_table_break(tmp_path):
    # EXPECT-3: a note whose table is destroyed (the incident's blank-line
    # shape with NO rows following) — every manifest row_id alarms.
    forge_root = _make_forge(tmp_path, "wh1prog", ["W1-L0", "W2-L1"])
    note = mcwallt_make_note(tmp_path, "wh1_broken_note.md", [
        "objective: broken table",
        HEADER_A_LINE, SEP_LINE,
        "",  # the incident's blank line; no rows survive beneath it
    ])
    cfg = TowerConfig(
        db_path=mcwallt_make_db(tmp_path),
        programs=(ProgramConfig(program="wh1prog", tag="t", note_glob=note),),
        forge_root=forge_root)
    state = collect_state(cfg)
    prog = state["programs"][0]
    assert [l["row_id"] for l in prog["lanes"]] == []
    manifest_defects = [d for d in prog["parse_defects"]
                        if "absent from note parse" in d["defect"]]
    assert [d["row_id"] for d in manifest_defects] == ["W1-L0", "W2-L1"]
    assert all(d["line"] == 0 for d in manifest_defects)


def test_wh1_a5_manifest_dir_absent_is_silent(tmp_path):
    # No forge dir, no manifests: absence is not failure (programs older than
    # the forge era never alarm).
    note = mcwallt_make_note(tmp_path, "wh1_noforge_note.md", [
        "objective: no forge",
        HEADER_A_LINE, SEP_LINE,
        "| W1-L1 | W1 | L1 | n/a | n/a | n/a | n/a | done |",
    ])
    cfg = TowerConfig(
        db_path=mcwallt_make_db(tmp_path),
        programs=(ProgramConfig(program="wh1prog", tag="t", note_glob=note),),
        forge_root=str(tmp_path / "forge" / "does-not-exist"))
    state = collect_state(cfg)
    assert state["programs"][0]["parse_defects"] == []


# --- A6: session conservation + orphan surfacing ----------------------------------

ORPHAN_SID = "sess_77770001-7777-4777-8777-777777777777"


def _orphan_session(age_s=200.0):
    from tests.tower.conftest import MCWALLT_WORLD_NOW, mcwallt_titled_tag_input
    ms = int((MCWALLT_WORLD_NOW - age_s) * 1000)
    return ({"id": ORPHAN_SID, "title": "[secfix W9-L9] wh1 orphan lane",
             "directory": "/wh1/orphan", "time_updated": ms, "time_created": ms},
            mcwallt_titled_tag_input(ORPHAN_SID, "secfix", "W9-L9", ms))


def test_wh1_a6_orphan_tagged_session_surfaces(tmp_path, monkeypatch):
    # EXPECT-4 (the incident's Layer-2, dead): a session tagged for a KNOWN
    # program (pasted titled tag) whose row binds to no lane — yesterday it
    # vanished (excluded from unmapped by its single configured tag, bound to
    # nothing). Today it surfaces in sessions_orphaned with the tag.
    extra_sess, extra_input = _orphan_session()
    cfg, _set = mcwallt_world(tmp_path, monkeypatch,
                              extra_sessions=[extra_sess], extra_inputs=[extra_input])
    state = collect_state(cfg)
    assert state["sessions_orphaned"] == [
        {"id": ORPHAN_SID, "title": "[secfix W9-L9] wh1 orphan lane",
         "tag": "secfix", "last_active_ago_s": 200}]
    # Conservation holds on the default world + the orphan: no defect line.
    assert not any("unaccounted" in line for line in state["server"]["degraded"])
    contract.assert_shape(state)


def test_wh1_a6_conservation_covers_every_window_session(tmp_path, monkeypatch):
    # The accounting identity on the full fixture world: every window session
    # is lane-bound, unmapped, master, orphan-tagged, or background — and the
    # doc carries enough ids to check it from the payload alone.
    cfg, _set = mcwallt_world(tmp_path, monkeypatch)
    state = collect_state(cfg)
    accounted = set()
    for prog in state["programs"]:
        for lane in prog["lanes"]:
            if lane["session"] is not None:
                accounted.add(lane["session"]["id"])
        if prog["master"]["session_id"] is not None:
            accounted.add(prog["master"]["session_id"])
    accounted |= {r["id"] for r in state["sessions_unmapped"]}
    accounted |= {r["id"] for r in state["sessions_orphaned"]}
    # The fixture db's window ids (world sessions + nothing else). The
    # subagent is the background-excluded class: hidden from the doc BY DESIGN
    # (the web collapses workflow actors) — the conservation pass accounts it
    # on the tower side; the payload check covers the visible classes.
    window = {MCWALLT_WORLD_MASTER, MCWALLT_WORLD_LANE, MCWALLT_WORLD_UNMAPPED,
              MCWALLT_WORLD_SUBAGENT}
    assert MCWALLT_WORLD_SUBAGENT.startswith("sess_subagent_")  # the class proof
    assert (window - {MCWALLT_WORLD_SUBAGENT}) <= accounted
    assert not any("unaccounted" in line for line in state["server"]["degraded"])


def test_wh1_a6_unaccountable_session_defects(tmp_path, monkeypatch):
    # The tripwire: if a future change excludes a session from unmapped
    # WITHOUT accounting for it, conservation fails LOUD. Simulated by
    # patching the adapter's unmapped_rows to drop the untagged session.
    from mc_wall.tower import zcode_db
    real_unmapped = zcode_db.unmapped_rows

    def dropping_unmapped(cur, now_s, factor, session_window_s, joined_ids,
                          tag_map, configured_tags):
        rows = real_unmapped(cur, now_s, factor, session_window_s, joined_ids,
                             tag_map, configured_tags)
        return [r for r in rows if r["id"] != MCWALLT_WORLD_UNMAPPED]

    monkeypatch.setattr(zcode_db, "unmapped_rows", dropping_unmapped)
    cfg, _set = mcwallt_world(tmp_path, monkeypatch)
    state = collect_state(cfg)
    assert "conservation defect: 1 sessions unaccounted" in state["server"]["degraded"]


def test_wh1_a6_state_carries_orphans_and_root_defects_schema3(tmp_path, monkeypatch):
    # Contract: sessions_orphaned + root parse_defects live at the state root;
    # the root aggregation feeds the web's defect strip (the tower previously
    # carried defects ONLY per program — the web pinned a root key it never
    # emitted; closed at v1.11.1). Schema 3 (additive root keys; merges[] from
    # W4-L4 rides the same version when absorbed). The default world's W1-L2
    # "plain lane" row contributes its two A4 cell defects; nothing else.
    cfg, _set = mcwallt_world(tmp_path, monkeypatch)
    state = collect_state(cfg)
    assert state["schema_version"] == 3
    assert state["sessions_orphaned"] == []
    assert [(d["row_id"], d["defect"]) for d in state["parse_defects"]] == [
        ("W1-L2", "repo/branch cell 'plugin cache 1.3.0 (plain lane)' has no repo path token"),
        ("W1-L2", "repo/branch cell 'plugin cache 1.3.0 (plain lane)' has no branch token")]
    contract.assert_shape(state)
    assert "sessions_orphaned" in contract.SHAPES["state"]
    assert "orphaned_row" in contract.SHAPES


def test_wh1_a6_root_defects_flatten_program_defects(tmp_path, monkeypatch):
    # Root parse_defects = the flattened per-program defects (doc order), so
    # the web's existing defect strip renders tower defects without a second
    # parser. Uses the incident shape so multiple layers (blank defect + cell
    # defect + manifest alarm) appear together at the root.
    forge_root = _make_forge(tmp_path, "secfix", ["W1-L1"])
    repo = tmp_path / "mcwallt_world_repo"
    rows = [
        f"| W1-L1 | W1 | L1 | {repo} loop/mcwall-tower | mcwallt-slug | n/a | sess_9a690ab2 | done |",
        "| W1-L2 | W1 | L2 | plugin cache 1.3.0 (plain lane) | n/a | n/a | n/a | launched |",
        f"| W1-L3 | W1 | L3 | {repo} main | mcwallt-slug-3 | n/a | n/a | done |",
    ]
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, forge_root=forge_root, rows=rows)
    # Inject the incident's blank line between the separator and the first
    # row (mcwallt_world builds the table contiguous).
    note_path = cfg.programs[0].note_glob
    with open(note_path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    assert lines[2].startswith("|---")
    lines.insert(3, "")
    with open(note_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    state = collect_state(cfg)
    root_defects = state["parse_defects"]
    assert {"note_path": os.path.abspath(note_path), "line": 4,
            "defect": "blank line inside prompt-log table", "row_id": None} in root_defects
    # The plain-lane row's A4 defects surface at the root too.
    assert any("has no repo path token" in d["defect"] for d in root_defects)
