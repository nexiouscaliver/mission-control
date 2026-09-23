"""Wall-autonomy tests (goal mcwall-autonomy, matrix 1-11): the widened tag
grammar (bare vs titled split, pinned bit-for-bit), the ONE-scan products
split with its two pure views, and the sessions_unmapped exclusion holding for
BOTH paste forms. Pure-grammar tests pin parse_tag_line/parse_tags directly;
the scan test pins the products views read-only; the world tests pin the
exclusion through the full collect_state document. Sids are fresh full
uuid4-shaped constants per test (the VERIFIED live id format).
"""

from mc_wall.tower import ProgramConfig, TowerConfig, collect_state
from mc_wall.tower import zcode_db
from tests.tower.conftest import (MCWALLT_WORLD_NOW, MCWALLT_WORLD_UNMAPPED,
                                  mcwallt_clock, mcwallt_make_db, mcwallt_make_note,
                                  mcwallt_tag_input, mcwallt_titled_tag_input,
                                  mcwallt_world)


def test_wa_1_parse_bare_single_token_unchanged():
    # Bare form, bit-for-bit today's grammar: the raw bracket string, row None.
    assert zcode_db.parse_tags("Session title: [secfix]\nbody") == {"secfix"}
    assert zcode_db.parse_tag_line("Session title: [secfix]") == ("secfix", None)


def test_wa_1_parse_bare_whitespace_bracket_unchanged():
    # The pathological bare bracket [ ] stays the raw string " " (v1 behavior).
    assert zcode_db.parse_tag_line("Session title: [ ]") == (" ", None)
    assert zcode_db.parse_tags("Session title: [ ]") == {" "}


def test_wa_1_parse_titled_with_name():
    assert zcode_db.parse_tags("Session title: [secfix W1-L1] fix throttling") == {"secfix"}
    assert zcode_db.parse_tag_line(
        "Session title: [secfix W1-L1] fix throttling") == ("secfix", "W1-L1")


def test_wa_1_parse_titled_no_name():
    # The trailing name is OPTIONAL: title steering may trim the tail.
    assert zcode_db.parse_tag_line("Session title: [secfix W1-L1]") == ("secfix", "W1-L1")
    assert zcode_db.parse_tags("Session title: [secfix W1-L1]") == {"secfix"}


def test_wa_1_parse_titled_double_space_tolerance():
    # str.split() inside the bracket tolerates paste-normalized double spaces;
    # the tag-line prefix itself keeps exact single-space matching.
    assert zcode_db.parse_tag_line(
        "Session title: [secfix  W1-L1]  name") == ("secfix", "W1-L1")
    # Double spaces on OTHER lines of the same paste are irrelevant by
    # construction (the grammar is applied per line).
    assert zcode_db.parse_tags(
        "pasted  with  double  spaces\nSession title: [secfix  W1-L1]  name") == {"secfix"}


def test_wa_1_parse_titled_extra_bracket_tokens():
    # Further bracket tokens beyond tag + row id are ignored.
    assert zcode_db.parse_tag_line(
        "Session title: [secfix W1-L1 trailing junk]") == ("secfix", "W1-L1")


def test_wa_1_parse_bare_with_trailing_name():
    # Single-token bracket + a trailing name: bare product, NO binding (row id
    # None) — the name is a title tail, never a row id.
    assert zcode_db.parse_tag_line("Session title: [secfix] some name") == ("secfix", None)
    assert zcode_db.parse_tags("Session title: [secfix] some name") == {"secfix"}


def test_wa_1_parse_anchored_negatives():
    # Anchored negatives — regression pins of UNCHANGED behavior, asserted via
    # parse_tags (an empty set IS the zero-products proof), so the pin holds
    # identically before and after the grammar widening: the prefix is exact
    # (capitalized, single space), the match is line-anchored, and the bracket
    # needs at least one non-] character. The degenerate titled form
    # (whitespace-only bracket + non-empty name) also yields no product.
    for text in ("session title: [secfix]",           # lowercase prefix
                 "Session title:[secfix]",            # missing space after the colon
                 "mid text Session title: [secfix]",  # mid-line occurrence
                 "Session title: []",                 # empty bracket
                 "Session title: [ ] name"):          # degenerate titled
        assert zcode_db.parse_tags(text) == set()


def test_wa_1_scan_normalization_masters_compat(tmp_path):
    # Bare + titled pastes under one tag normalize to ONE program tag (set
    # size 1), so the masters exact-singleton rule keeps its meaning over
    # PROGRAM tags; the bindings view carries the titled (tag, row) pairs.
    NOW = 2_000_000_050.0

    def ms(age):
        return int((NOW - age) * 1000)

    a = "sess_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    b = "sess_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    sessions = [
        {"id": a, "title": "mcwallt lane tagged", "directory": "/mcwallt/a",
         "time_updated": ms(100), "time_created": ms(100)},
        {"id": b, "title": "mcwallt master tagged", "directory": "/mcwallt/b",
         "time_updated": ms(90), "time_created": ms(90)},
    ]
    inputs = [mcwallt_tag_input(a, ["secfix"], ms(100)),
              mcwallt_titled_tag_input(a, "secfix", "W1-L1", ms(100)),
              mcwallt_tag_input(b, ["secfix-master"], ms(90)),
              mcwallt_titled_tag_input(b, "secfix-master", "W0", ms(90))]
    db = mcwallt_make_db(tmp_path, sessions=sessions, inputs=inputs)
    con = zcode_db.open_db_ro(db)
    try:
        cur = con.cursor()
        assert zcode_db.scan_tags(cur, 0) == {a: {"secfix"}, b: {"secfix-master"}}
        assert zcode_db.scan_tag_bindings(cur, 0) == {a: {("secfix", "W1-L1")},
                                                      b: {("secfix-master", "W0")}}
    finally:
        con.close()
    # Through the full collect: B's bare + titled secfix-master pastes still
    # select it as master (the exact-singleton rule, intact).
    note = mcwallt_make_note(tmp_path, "mcwallt_note.md", [
        "| id | wave | lane | repo/branch | slug | base | session/MR artifacts | status |",
        "|---|---|---|---|---|---|---|---|",
        "| W1-L1 | W1 | L1 | n/a | n/a | n/a | n/a | done |",
    ])
    state = collect_state(TowerConfig(
        db_path=db,
        programs=(ProgramConfig(program="secfix", tag="secfix", note_glob=note,
                                master_tag="secfix-master"),),
        now_s=mcwallt_clock(NOW)))
    assert state["programs"][0]["master"]["session_id"] == b


def test_wa_1_unmapped_excludes_bare_configured(tmp_path, monkeypatch):
    # Regression pin of today's rule: a session whose ONLY tag is a configured
    # program tag (bare paste) never enumerates in sessions_unmapped.
    NOW = MCWALLT_WORLD_NOW

    def ms(age):
        return int((NOW - age) * 1000)

    s10 = "sess_44444444-4444-4444-8444-444444444444"
    cfg, _set = mcwallt_world(
        tmp_path, monkeypatch,
        extra_sessions=[{"id": s10, "title": "mcwallt s10", "directory": "/mcwallt/s10",
                         "time_updated": ms(60), "time_created": ms(60)}],
        extra_inputs=[mcwallt_tag_input(s10, ["secfix"], ms(60))])
    ids = {r["id"] for r in collect_state(cfg)["sessions_unmapped"]}
    assert s10 not in ids
    assert MCWALLT_WORLD_UNMAPPED in ids  # the unmapped view itself stays alive


def test_wa_1_unmapped_excludes_titled_configured(tmp_path, monkeypatch):
    # SC-2: the titled paste [secfix NOPE-L9] normalizes to the configured tag
    # {"secfix"} (size 1) — excluded exactly like the bare form, even though
    # NOPE-L9 matches no lane row.
    NOW = MCWALLT_WORLD_NOW

    def ms(age):
        return int((NOW - age) * 1000)

    s11 = "sess_45454545-4545-4545-8545-454545454545"
    cfg, _set = mcwallt_world(
        tmp_path, monkeypatch,
        extra_sessions=[{"id": s11, "title": "mcwallt s11", "directory": "/mcwallt/s11",
                         "time_updated": ms(60), "time_created": ms(60)}],
        extra_inputs=[mcwallt_titled_tag_input(s11, "secfix", "NOPE-L9", ms(60))])
    ids = {r["id"] for r in collect_state(cfg)["sessions_unmapped"]}
    assert s11 not in ids
    assert MCWALLT_WORLD_UNMAPPED in ids  # the unmapped view itself stays alive
