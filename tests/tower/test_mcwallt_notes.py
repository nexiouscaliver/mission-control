"""T-2 notes-parser tests: AC-VOCAB-1/2, AC-NOTES-CORPUS, AC-NOTES-HEADERS,
AC-NOTES-SKIP, AC-NOTES-UNCFG (+ slug/artifacts helper coverage).

The corpus table below embeds the live vault note's prompt-log rows VERBATIM
(2026-09-19, orchestrator-held ground truth; fixtures only — no test reads the
live vault). FX1 is the real 7-cells-under-8-columns skip case.
"""

import os
import sqlite3

from mc_wall.tower import ProgramConfig, RepoConfig, TowerConfig, collect_state
from mc_wall.tower import contract
from tests.tower.conftest import mcwallt_make_note

HEADER_A_LINE = "| id | wave | lane | repo/branch | slug | base | session/MR artifacts | status |"
SEP_LINE = "|---|---|---|---|---|---|---|---|"

# Live vault note 2026-09-19, verbatim (header variant A + FX1 + W1-L1..L4 + W0-L0).
CORPUS_NOTE = """\
| id | wave | lane | repo/branch | slug | base | session/MR artifacts | status |
|---|---|---|---|---|---|---|---|
| FX1 | off-mode | fork: persona-gate evaluation (no repo/vault writes in fork) | — | — | anchor: fork sess_af73caf4; recorded by controller sess_9a690ab2 same turn | done |
| W1-L1 | W1 | L1 tower | ~/.zcode/mc-wall loop/mcwall-tower | mcwall-tower | d55cbde76ec884fc8f629228caa5fc06d6279e3e (tip verified 22:57) | forged 23:00 controller sess_9a690ab2; session id due at launch | forged |
| W1-L2 | W1 | L2 server+cli | ~/.zcode/mc-wall loop/mcwall-server | mcwall-server | d55cbde76ec884fc8f629228caa5fc06d6279e3e | forged 23:00 controller sess_9a690ab2 | forged |
| W1-L3 | W1 | L3 frontend (VISION) | ~/.zcode/mc-wall loop/mcwall-frontend | mcwall-frontend | d55cbde76ec884fc8f629228caa5fc06d6279e3e | forged 23:00 controller sess_9a690ab2 | forged |
| W1-L4 | W1 | L4 skill-edits + persona-gate | plugin cache 1.3.0 (plain lane) | n/a | plugin cache sha256s recorded by lane | forged 23:00 controller sess_9a690ab2 | forged |
| W0-L0 | W0 | L0 bootstrap | ~/.zcode/mc-wall main @ d55cbde | n/a (plain lane) | behavioral: dir absent, verified 22:31; created fresh (initial commit bb06a68) | session sess_2243e9a1 (title custom: "W0 - Bootstrap mc-wall repo…", 12 min run); operator base-recheck done at launch | done |
"""


def mcwallt_min_session_db(tmp_path, name="mcwallt_sessions.db"):
    """Minimal db satisfying the T-1 session-store probe (session table present)."""
    p = tmp_path / name
    con = sqlite3.connect(p)
    con.execute(
        "CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT, directory TEXT,"
        " time_updated INTEGER, time_created INTEGER, time_archived INTEGER)"
    )
    con.commit()
    con.close()
    return str(p)


def test_mcwallt_notes_status_vocab_all():
    from mc_wall.tower import notes
    vocab = ["forged", "launched", "done", "partial", "failed", "parked", "in-flight"]
    rows = [f"| W1-V{i} | W1 | L1 | n/a | n/a | n/a | sess_0000000{i} | {s} |"
            for i, s in enumerate(vocab)]
    parsed = notes.parse_note("\n".join([HEADER_A_LINE, SEP_LINE, *rows]))
    assert parsed.header_found is True
    assert parsed.skipped == 0
    assert [r.status_parsed for r in parsed.rows] == vocab  # each parses to itself
    assert [r.status_note for r in parsed.rows] == vocab


def test_mcwallt_notes_status_unparsed_preserved():
    from mc_wall.tower import notes
    # Unknown status: raw preserved verbatim incl. spaces, parsed UNPARSED.
    assert notes.parse_status("  done-ish  ") == ("  done-ish  ", "UNPARSED")
    # Empty cell: UNPARSED with the empty raw kept.
    assert notes.parse_status("") == ("", "UNPARSED")
    # Through a table row: trimmed raw in status_note, UNPARSED parsed.
    parsed = notes.parse_note("\n".join([
        HEADER_A_LINE, SEP_LINE,
        "| W1-U | W1 | L1 | n/a | n/a | n/a | sess_00000000 | launched-ish |",
    ]))
    assert parsed.rows[0].status_note == "launched-ish"
    assert parsed.rows[0].status_parsed == "UNPARSED"


def test_mcwallt_notes_corpus_rows(tmp_path):
    from mc_wall.tower import notes
    va = notes.VARIANT_A
    # The four §4.2-quoted corpus cells, verbatim:
    assert notes.parse_repo_branch("~/.zcode/mc-wall loop/mcwall-tower", va) == \
        ("~/.zcode/mc-wall", "loop/mcwall-tower")
    assert notes.parse_repo_branch("plugin cache 1.3.0 (plain lane)", va) == (None, None)
    assert notes.parse_repo_branch("~/.zcode/mc-wall main @ d55cbde", va) == \
        ("~/.zcode/mc-wall", "main")
    assert notes.parse_repo_branch("—", va) == (None, None)

    parsed = notes.parse_note(CORPUS_NOTE)
    assert parsed.header_found is True
    assert parsed.skipped == 1  # FX1: 7 cells under the 8-column header
    assert [r.row_id for r in parsed.rows] == ["W1-L1", "W1-L2", "W1-L3", "W1-L4", "W0-L0"]
    by_id = {r.row_id: r for r in parsed.rows}
    assert (by_id["W1-L1"].repo_token, by_id["W1-L1"].branch, by_id["W1-L1"].slug,
            by_id["W1-L1"].sess_token, by_id["W1-L1"].status_parsed) == \
        ("~/.zcode/mc-wall", "loop/mcwall-tower", "mcwall-tower", "sess_9a690ab2", "forged")
    assert (by_id["W1-L2"].repo_token, by_id["W1-L2"].branch, by_id["W1-L2"].slug,
            by_id["W1-L2"].sess_token, by_id["W1-L2"].status_parsed) == \
        ("~/.zcode/mc-wall", "loop/mcwall-server", "mcwall-server", "sess_9a690ab2", "forged")
    assert (by_id["W1-L3"].repo_token, by_id["W1-L3"].branch, by_id["W1-L3"].slug,
            by_id["W1-L3"].sess_token, by_id["W1-L3"].status_parsed) == \
        ("~/.zcode/mc-wall", "loop/mcwall-frontend", "mcwall-frontend", "sess_9a690ab2", "forged")
    assert (by_id["W1-L4"].repo_token, by_id["W1-L4"].branch, by_id["W1-L4"].slug,
            by_id["W1-L4"].sess_token, by_id["W1-L4"].status_parsed) == \
        (None, None, None, "sess_9a690ab2", "forged")
    assert (by_id["W0-L0"].repo_token, by_id["W0-L0"].branch, by_id["W0-L0"].slug,
            by_id["W0-L0"].sess_token, by_id["W0-L0"].status_parsed) == \
        ("~/.zcode/mc-wall", "main", None, "sess_2243e9a1", "done")

    # Collect-level: one entry-10 line, repo token mapped to the configured name.
    note = mcwallt_make_note(tmp_path, "mcwallt_corpus.md", CORPUS_NOTE.splitlines())
    cfg = TowerConfig(
        db_path=mcwallt_min_session_db(tmp_path),
        programs=(ProgramConfig(program="mcwallt-prog", tag="t", note_glob=note),),
        repos=(RepoConfig(name="mc-wall", path=os.path.expanduser("~/.zcode/mc-wall"),
                          host="gitlab"),),
    )
    state = collect_state(cfg)
    assert state["server"]["degraded"] == ["note rows skipped: 1"]
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    assert set(lanes) == {"W1-L1", "W1-L2", "W1-L3", "W1-L4", "W0-L0"}
    assert lanes["W1-L1"]["repo"] == "mc-wall"
    assert lanes["W0-L0"]["repo"] == "mc-wall"
    assert lanes["W1-L4"]["repo"] is None  # plain lane: no path token
    contract.assert_shape(state)


def test_mcwallt_notes_header_variants(tmp_path):
    from mc_wall.tower import notes
    # Variant A, case/whitespace-varied spelling.
    parsed_a = notes.parse_note("\n".join([
        "| ID | Wave | Lane | Repo/Branch | SLUG | base | Session/MR Artifacts | Status |",
        "|-|-|-|-|-|-|-|-|",
        "| W2-L1 | W2 | L1 | ~/.zcode/mc-wall loop/mcwall-tower | mcwall-tower | d55 | sess_00000000 | forged |",
    ]))
    assert parsed_a.header_found is True
    assert parsed_a.skipped == 0
    assert parsed_a.rows[0].slug == "mcwall-tower"

    # Variant B (documented future format, det-filter note): separate repo and
    # branch columns mapped BY NAME, varied spelling, no slug -> None.
    parsed_b = notes.parse_note("\n".join([
        "| id|wave |lane| repo |branch| base_sha |session/MR artifacts|status |",
        SEP_LINE,
        "| W2-L2 | W2 | L2 | ~/.zcode/mc-wall | loop/mcwall-server | d55 | forged sess_00000001 | launched |",
    ]))
    assert parsed_b.header_found is True
    r = parsed_b.rows[0]
    assert (r.repo_token, r.branch, r.slug, r.sess_token, r.status_parsed) == \
        ("~/.zcode/mc-wall", "loop/mcwall-server", None, "sess_00000001", "launched")

    # The det-filter note today: prose bullets only, no matching table.
    det_lines = [
        "## det-filter",
        "- bullet one",
        "- bullet two; no prompt-log table",
        "",
        "objective: filter the noise",
    ]
    parsed_d = notes.parse_note("\n".join(det_lines))
    assert parsed_d.header_found is False
    assert parsed_d.rows == []
    assert parsed_d.objective == "filter the noise"

    # Through collect: headerless note -> lanes=[] + entry 3, row otherwise populated.
    note = mcwallt_make_note(tmp_path, "mcwallt_detfilter.md", det_lines)
    cfg = TowerConfig(
        db_path=mcwallt_min_session_db(tmp_path),
        programs=(ProgramConfig(program="detfilter", tag="t", note_glob=note),),
    )
    state = collect_state(cfg)
    prog = state["programs"][0]
    assert prog["lanes"] == []
    assert prog["note_path"].endswith("mcwallt_detfilter.md")
    assert prog["note_mtime"] > 0
    assert prog["objective"] == "filter the noise"
    assert state["server"]["degraded"] == ["notes degraded: detfilter"]


def test_mcwallt_notes_malformed_rows_skipped(tmp_path):
    from mc_wall.tower import notes
    text = "\n".join([
        HEADER_A_LINE,
        SEP_LINE,
        "| W1-L1 | W1 | L1 | ~/.zcode/mc-wall loop/mcwall-tower | mcwall-tower | d55 | sess_00000000 | launched |",
        "| W1-S | W1 | short |",                                   # fewer cells than header
        "|  | W1 | L1 | n/a | n/a | n/a | sess_00000002 | done |",  # empty id cell
        "|---|---|---|---|---|---|---|---|",                       # mid-table separator
    ])
    parsed = notes.parse_note(text)  # no exception
    assert parsed.skipped == 3
    assert [r.row_id for r in parsed.rows] == ["W1-L1"]

    note = mcwallt_make_note(tmp_path, "mcwallt_malformed.md", text.splitlines())
    cfg = TowerConfig(
        db_path=mcwallt_min_session_db(tmp_path),
        programs=(ProgramConfig(program="mcwallt-prog", tag="t", note_glob=note),),
    )
    state = collect_state(cfg)
    assert state["server"]["degraded"] == ["note rows skipped: 3"]  # exactly once
    assert [l["row_id"] for l in state["programs"][0]["lanes"]] == ["W1-L1"]


def test_mcwallt_notes_slug_null_rules():
    from mc_wall.tower import notes
    parsed = notes.parse_note("\n".join([
        HEADER_A_LINE, SEP_LINE,
        "| W1-A | W1 | L | n/a | n/a | n/a | sess_00000000 | done |",
        "| W1-B | W1 | L | n/a | — | n/a | sess_00000001 | done |",
        "| W1-C | W1 | L | n/a |  | n/a | sess_00000002 | done |",
        "| W1-D | W1 | L | n/a | n/a (plain lane) | n/a | sess_00000003 | done |",
        "| W1-E | W1 | L | n/a | mcwall-server | n/a | merge !5 and #9; sess_00000004 | done |",
    ]))
    slugs = {r.row_id: r.slug for r in parsed.rows}
    assert slugs == {"W1-A": None, "W1-B": None, "W1-C": None, "W1-D": None,
                     "W1-E": "mcwall-server"}
    assert parsed.skipped == 0
    # Artifacts-cell first-match extraction (consumed by T-5): sess/bang/hash.
    row_e = parsed.rows[-1]
    assert (row_e.sess_token, row_e.mr_bang, row_e.mr_hash) == \
        ("sess_00000004", "!5", "#9")


def test_mcwallt_notes_unconfigured_repo(tmp_path):
    note = mcwallt_make_note(tmp_path, "mcwallt_plain_note.md", [
        HEADER_A_LINE, SEP_LINE,
        "| W1-L4 | W1 | L4 skill-edits + persona-gate | plugin cache 1.3.0 (plain lane) | n/a | plugin cache sha256s recorded by lane | forged 23:00 controller sess_9a690ab2 | forged |",
    ])
    cfg = TowerConfig(
        db_path=mcwallt_min_session_db(tmp_path),
        programs=(ProgramConfig(program="mcwallt-prog", tag="t", note_glob=note),),
        repos=(),
    )
    state = collect_state(cfg)
    lane = state["programs"][0]["lanes"][0]
    assert lane["repo"] is None
    assert lane["branch"] is None
    assert lane["slug"] is None
    assert lane["goal"] is None
    assert lane["manifest"] is None
    assert lane["signals"] == {"pushed": None, "mr": None}
    assert state["server"]["degraded"] == []  # config gap is absence, not failure
    contract.assert_shape(state)
