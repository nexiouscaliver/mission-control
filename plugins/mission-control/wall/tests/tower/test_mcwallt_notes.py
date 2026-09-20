"""T-2 notes-parser tests: AC-VOCAB-1/2, AC-NOTES-CORPUS, AC-NOTES-HEADERS,
AC-NOTES-SKIP, AC-NOTES-UNCFG (+ slug/artifacts helper coverage).

The corpus table below embeds the live vault note's prompt-log rows VERBATIM
(2026-09-19, orchestrator-held ground truth; fixtures only — no test reads the
live vault). FX1 is the real 7-cells-under-8-columns skip case.
"""

import os

from mc_wall.tower import ProgramConfig, RepoConfig, TowerConfig, collect_state
from mc_wall.tower import collect as collect_module
from mc_wall.tower import contract, notes
from tests.tower.conftest import (mcwallt_clock, mcwallt_make_db, mcwallt_make_note,
                                  mcwallt_make_session_db)

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


def test_mcwallt_notes_status_vocab_all():
    vocab = ["forged", "launched", "done", "partial", "failed", "parked", "in-flight"]
    rows = [f"| W1-V{i} | W1 | L1 | n/a | n/a | n/a | sess_0000000{i} | {s} |"
            for i, s in enumerate(vocab)]
    parsed = notes.parse_note("\n".join([HEADER_A_LINE, SEP_LINE, *rows]))
    assert parsed.header_found is True
    assert parsed.skipped == 0
    assert [r.status_parsed for r in parsed.rows] == vocab  # each parses to itself
    assert [r.status_note for r in parsed.rows] == vocab


def test_mcwallt_notes_status_unparsed_preserved():
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


def test_mcwallt_notes_corpus_rows(tmp_path, monkeypatch):
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
    # Hermeticity (review B1): the verbatim corpus cell maps these lanes to the
    # REAL ~/.zcode/mc-wall repo, and T-4's goal wiring would read that repo's
    # live goal tree. Goal wiring is T-4's subject (covered fixture-only by
    # test_mcwallt_goals.py); this test owns the note parse — neutralize it.
    monkeypatch.setattr(collect_module, "_read_goals", lambda *args: None)
    # Same hermeticity rule for T-5: the corpus lanes carry real branches, and
    # the signals wiring would spawn real git/glab against the live checkout.
    # Signals are T-5's subject (covered fixture-only by
    # test_mcwallt_signals.py); neutralize them here too.
    monkeypatch.setattr(collect_module, "_read_signals", lambda *args: None)
    note = mcwallt_make_note(tmp_path, "mcwallt_corpus.md", CORPUS_NOTE.splitlines())
    cfg = TowerConfig(
        db_path=mcwallt_make_session_db(tmp_path),
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
        db_path=mcwallt_make_session_db(tmp_path),
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
        db_path=mcwallt_make_session_db(tmp_path),
        programs=(ProgramConfig(program="mcwallt-prog", tag="t", note_glob=note),),
    )
    state = collect_state(cfg)
    assert state["server"]["degraded"] == ["note rows skipped: 3"]  # exactly once
    assert [l["row_id"] for l in state["programs"][0]["lanes"]] == ["W1-L1"]


def test_mcwallt_notes_slug_null_rules():
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
        db_path=mcwallt_make_session_db(tmp_path),
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


def test_mcwallt_notes_find_note_vanish_race(tmp_path, monkeypatch):
    # R1 regression: a note vanishing between glob and stat must degrade that
    # ONE program (entry 3 + zeroed row), never zero the whole document.
    good_note = mcwallt_make_note(tmp_path, "mcwallt_good.md", [
        HEADER_A_LINE, SEP_LINE,
        "| W1-L1 | W1 | L1 | n/a | mcwallt-slug | n/a | sess_00000000 | done |",
    ])
    doomed = tmp_path / "mcwallt_doomed.md"
    doomed.write_text("objective: here a moment ago\n", encoding="utf-8")
    real_stat = os.stat

    def vanishing_stat(p, *args, **kwargs):
        if os.fspath(p) == str(doomed):
            raise FileNotFoundError(p)  # the glob -> stat race
        return real_stat(p, *args, **kwargs)

    monkeypatch.setattr(notes.os, "stat", vanishing_stat)
    cfg = TowerConfig(
        db_path=mcwallt_make_session_db(tmp_path),
        programs=(ProgramConfig(program="good", tag="t", note_glob=good_note),
                  ProgramConfig(program="doomed", tag="t", note_glob=str(doomed))),
    )
    state = collect_state(cfg)
    # The healthy program is untouched...
    assert [l["row_id"] for l in state["programs"][0]["lanes"]] == ["W1-L1"]
    # ...the doomed program takes the §4.2 zeroed row + entry 3...
    assert state["programs"][1] == {"program": "doomed", "note_path": "", "note_mtime": 0,
                                    "objective": "",
                                    "master": {"session_id": None, "title": None,
                                               "last_active_ago_s": None},
                                    "lanes": []}
    assert state["server"]["degraded"] == ["notes degraded: doomed"]
    # ...and the document was NOT zeroed by the internal-error backstop.
    assert "internal error: collect_state failed" not in state["server"]["degraded"]
    assert state["launch_pending"] is None
    contract.assert_shape(state)


def test_mcwallt_notes_undecodable_note(tmp_path):
    # Present but not decodable UTF-8: per-shape zeroed program row (§4.2) +
    # entry 3 — the same fail-open as any unreadable note, never an exception.
    bad = tmp_path / "mcwallt_bad_note.md"
    bad.write_bytes(b"notes: \xff\xfe not utf-8 \xfd\x00")
    cfg = TowerConfig(
        db_path=mcwallt_make_session_db(tmp_path),
        programs=(ProgramConfig(program="mcwallt-prog", tag="t", note_glob=str(bad)),),
    )
    state = collect_state(cfg)
    assert state["programs"][0] == {"program": "mcwallt-prog", "note_path": "",
                                    "note_mtime": 0, "objective": "",
                                    "master": {"session_id": None, "title": None,
                                               "last_active_ago_s": None},
                                    "lanes": []}
    assert state["server"]["degraded"] == ["notes degraded: mcwallt-prog"]
    contract.assert_shape(state)


def test_mcwallt_lane_session_variants(tmp_path):
    # AC-SESSION-1: the sess_ shorthand PREFIX join. The 1-hit db ids below are
    # the verified live-evidence ids (read-only queries, 2026-09-19); the note
    # tokens are the vault shorthands (first 8 hex).
    NOW = 2_000_000_000.0

    def ms(age):
        return int((NOW - age) * 1000)

    db = mcwallt_make_db(tmp_path, sessions=[
        # no title key -> NULL title: title_pending True
        {"id": "sess_3f3f3f3f-3f3f-43f3-83f3-3f3f3f3f3f3f", "directory": "/mcwallt/l0",
         "time_updated": ms(30), "time_created": ms(30)},
        # empty-string title: title_pending True
        {"id": "sess_9a690ab2-cde8-4e9a-bc4e-177fcc68545f", "title": "",
         "directory": "/mcwallt/l1", "time_updated": ms(120), "time_created": ms(120)},
        # set title: title_pending False
        {"id": "sess_2243e9a1-ef61-4f35-a05a-a4cd422abec6",
         "title": "MC - Managing multiple mission control sessions workflow",
         "directory": "/mcwallt/l2", "time_updated": ms(80), "time_created": ms(80)},
        # the ambiguous pair: one token prefix shared by two ids
        {"id": "sess_abcabcab-1111-4111-8111-111111111111", "title": "amb-1",
         "directory": "/mcwallt/l4a", "time_updated": ms(60), "time_created": ms(60)},
        {"id": "sess_abcabcab-2222-4222-8222-222222222222", "title": "amb-2",
         "directory": "/mcwallt/l4b", "time_updated": ms(60), "time_created": ms(60)},
    ])
    note = mcwallt_make_note(tmp_path, "mcwallt_lane_variants.md", [
        HEADER_A_LINE, SEP_LINE,
        "| W3-L0 | W3 | L0 | n/a | n/a | n/a | sess_3f3f3f3f | forged |",  # 1 hit, NULL title
        "| W3-L1 | W3 | L1 | n/a | n/a | n/a | sess_9a690ab2 | forged |",  # 1 hit, empty title
        "| W3-L2 | W3 | L2 | n/a | n/a | n/a | sess_2243e9a1 | forged |",  # 1 hit, set title
        "| W3-L3 | W3 | L3 | n/a | n/a | n/a | sess_00000000 | forged |",  # 0 hits
        "| W3-L4 | W3 | L4 | n/a | n/a | n/a | sess_abcabcab | forged |",  # 2 hits
        "| W3-L5 | W3 | L5 | n/a | n/a | n/a | n/a | forged |",            # no token
    ])
    cfg = TowerConfig(db_path=db,
                      programs=(ProgramConfig(program="mcwallt-prog", tag="t",
                                              note_glob=note),),
                      now_s=mcwallt_clock(NOW))
    state = collect_state(cfg)
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    # Exactly-1 prefix hits -> full session object, id = FULL db id.
    assert lanes["W3-L0"]["session"] == {"id": "sess_3f3f3f3f-3f3f-43f3-83f3-3f3f3f3f3f3f",
                                         "title": None, "title_pending": True,
                                         "dir": "/mcwallt/l0", "last_active_ago_s": 30}
    assert lanes["W3-L1"]["session"] == {"id": "sess_9a690ab2-cde8-4e9a-bc4e-177fcc68545f",
                                         "title": "", "title_pending": True,
                                         "dir": "/mcwallt/l1", "last_active_ago_s": 120}
    assert lanes["W3-L2"]["session"] == {"id": "sess_2243e9a1-ef61-4f35-a05a-a4cd422abec6",
                                         "title": "MC - Managing multiple mission control sessions workflow",
                                         "title_pending": False,
                                         "dir": "/mcwallt/l2", "last_active_ago_s": 80}
    # 0 hits -> session null, NO degraded entry (the row may predate launch).
    assert lanes["W3-L3"]["session"] is None
    # 2 hits -> session null + entry 9 for the raw token.
    assert lanes["W3-L4"]["session"] is None
    # No token parsed -> session null.
    assert lanes["W3-L5"]["session"] is None
    assert state["server"]["degraded"] == ["join ambiguous session: sess_abcabcab"]
    contract.assert_shape(state)
