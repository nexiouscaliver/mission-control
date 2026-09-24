"""Wall-autonomy tests (goal mcwall-autonomy, matrix 1-28): the widened
tag grammar (bare vs titled split, pinned bit-for-bit), the ONE-scan products
split with its two pure views, the sessions_unmapped exclusion holding for
BOTH paste forms, the tag-driven lane binding (paste-primary, title
fallback, newest-wins ambiguity, stale-token/orphan/foreign-tag guards),
and the verify:ok token clearing the lane-level verify cue (queue row kept).
Pure-grammar tests pin parse_tag_line/parse_tags directly; the scan test pins
the products views read-only; the world tests pin the exclusion and the
binding through the full collect_state document. Sids are fresh full
uuid4-shaped constants per test (the VERIFIED live id format).
"""

import hashlib
import os
from pathlib import Path

from mc_wall.tower import ProgramConfig, TowerConfig, collect_state, contract
from mc_wall.tower import derive, notes, zcode_db
from tests.tower.conftest import (MCWALLT_WORLD_LANE, MCWALLT_WORLD_NOW,
                                  MCWALLT_WORLD_UNMAPPED, mcwallt_clock,
                                  mcwallt_make_db, mcwallt_make_note,
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


# --- matrix 12-22 + 26-28: tag-driven lane binding (goal mcwall-autonomy T2) ---

def test_wa_1_bind_paste_wires_lane_e2e(tmp_path, monkeypatch):
    # Matrix 12: a titled paste [secfix W1-L3] wires the token-less W1-L3 lane
    # end-to-end — the FULL session contract object (same shape as the
    # token-joined W1-L1), the sid absent from sessions_unmapped (joined ids
    # never enumerate), and the document still passing assert_shape.
    NOW = MCWALLT_WORLD_NOW

    def ms(age):
        return int((NOW - age) * 1000)

    s12 = "sess_46464646-4646-4466-8466-464646464646"
    cfg, _set = mcwallt_world(
        tmp_path, monkeypatch,
        extra_sessions=[{"id": s12, "title": "wa bind",
                         "time_updated": ms(200), "time_created": ms(200)}],
        extra_inputs=[mcwallt_titled_tag_input(s12, "secfix", "W1-L3", ms(200),
                                               name="fix throttling")])
    state = collect_state(cfg)
    contract.assert_shape(state)
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    assert lanes["W1-L3"]["session"] == {
        "id": s12, "title": "wa bind", "title_pending": False, "dir": "",
        "last_active_ago_s": 200, "parent_session_id": None}
    assert s12 not in {r["id"] for r in state["sessions_unmapped"]}


def test_wa_1_bind_title_fallback(tmp_path, monkeypatch):
    # Matrix 13: NO tag inputs at all — the session TITLE alone carries the
    # bracket and the title fallback binds W1-L3 (the sendGoalCommand path:
    # the title line never hits a sendText row).
    NOW = MCWALLT_WORLD_NOW

    def ms(age):
        return int((NOW - age) * 1000)

    s13 = "sess_47474747-4747-4477-8477-474747474747"
    cfg, _set = mcwallt_world(
        tmp_path, monkeypatch,
        extra_sessions=[{"id": s13, "title": "[secfix W1-L3] fix",
                         "time_updated": ms(3600), "time_created": ms(3600)}])
    state = collect_state(cfg)
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    assert lanes["W1-L3"]["session"]["id"] == s13


def test_wa_1_bind_title_durable_past_tag_window(tmp_path, monkeypatch):
    # Matrix 14: the only tag paste is 100 h old (outside the 72 h tag scan
    # window) while the session itself is 10 h old — the paste is invisible to
    # the scan, the title (24 h session window) still binds W1-L2: title
    # binding is DURABLE past the paste window.
    NOW = MCWALLT_WORLD_NOW

    def ms(age):
        return int((NOW - age) * 1000)

    s14 = "sess_48484848-4848-4488-8488-484848484848"
    cfg, _set = mcwallt_world(
        tmp_path, monkeypatch,
        extra_sessions=[{"id": s14, "title": "[secfix W1-L2] durable",
                         "time_updated": ms(10 * 3600), "time_created": ms(10 * 3600)}],
        extra_inputs=[mcwallt_titled_tag_input(s14, "secfix", "W1-L2", ms(100 * 3600))])
    state = collect_state(cfg)
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    assert lanes["W1-L2"]["session"]["id"] == s14


def test_wa_1_bind_title_window_expiry(tmp_path, monkeypatch):
    # Matrix 15: a title bracket on a session created 30 h ago (outside the
    # 24 h session window) never binds — W1-L3 stays null. The in-window
    # control twin (1 h old, same-shaped title on W1-L2) DOES bind, so the
    # null pins the window expiry, not a dead binding path.
    NOW = MCWALLT_WORLD_NOW

    def ms(age):
        return int((NOW - age) * 1000)

    s15 = "sess_49494949-4949-4499-8499-494949494949"
    s15_ctl = "sess_56565656-5656-4565-8565-565656565656"
    cfg, _set = mcwallt_world(
        tmp_path, monkeypatch,
        extra_sessions=[
            {"id": s15, "title": "[secfix W1-L3] expired",
             "time_updated": ms(30 * 3600), "time_created": ms(30 * 3600)},
            {"id": s15_ctl, "title": "[secfix W1-L2] in-window control",
             "time_updated": ms(3600), "time_created": ms(3600)}])
    state = collect_state(cfg)
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    assert lanes["W1-L3"]["session"] is None
    assert lanes["W1-L2"]["session"]["id"] == s15_ctl


def test_wa_1_bind_paste_beats_own_title(tmp_path, monkeypatch):
    # Matrix 16: paste-primary per session — S16 pastes [secfix W1-L2] while
    # its own title says [secfix W1-L3]: the paste binds W1-L2 and the
    # conflicting title is never consulted (W1-L3 stays unbound).
    NOW = MCWALLT_WORLD_NOW

    def ms(age):
        return int((NOW - age) * 1000)

    s16 = "sess_4a4a4a4a-4a4a-44aa-84aa-4a4a4a4a4a4a"
    cfg, _set = mcwallt_world(
        tmp_path, monkeypatch,
        extra_sessions=[{"id": s16, "title": "[secfix W1-L3] conflicting",
                         "time_updated": ms(300), "time_created": ms(300)}],
        extra_inputs=[mcwallt_titled_tag_input(s16, "secfix", "W1-L2", ms(300))])
    state = collect_state(cfg)
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    assert lanes["W1-L2"]["session"]["id"] == s16
    assert lanes["W1-L3"]["session"] is None


def test_wa_1_bind_token_precedence(tmp_path, monkeypatch):
    # Matrix 17: declared wins — W1-L1 carries the sess_9a690ab2 token and
    # keeps its token join; the S17 contender pasting [secfix W1-L1] stays
    # unbound AND invisible (Q5: excluded from sessions_unmapped by the
    # single-configured-tag rule). The control twin binding W1-L3 proves the
    # binding path was live in this very world — precedence, not absence,
    # kept W1-L1 on its declared session.
    NOW = MCWALLT_WORLD_NOW

    def ms(age):
        return int((NOW - age) * 1000)

    s17 = "sess_4b4b4b4b-4b4b-44bb-84bb-4b4b4b4b4b4b"
    s17_ctl = "sess_57575757-5757-4575-8575-575757575757"
    cfg, _set = mcwallt_world(
        tmp_path, monkeypatch,
        extra_sessions=[
            {"id": s17, "title": "mcwallt s17",
             "time_updated": ms(200), "time_created": ms(200)},
            {"id": s17_ctl, "title": "mcwallt s17 control",
             "time_updated": ms(200), "time_created": ms(200)}],
        extra_inputs=[mcwallt_titled_tag_input(s17, "secfix", "W1-L1", ms(200)),
                      mcwallt_titled_tag_input(s17_ctl, "secfix", "W1-L3", ms(200))])
    state = collect_state(cfg)
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    assert lanes["W1-L1"]["session"]["id"] == MCWALLT_WORLD_LANE
    assert lanes["W1-L3"]["session"]["id"] == s17_ctl
    assert s17 not in {r["id"] for r in state["sessions_unmapped"]}


def test_wa_1_bind_newest_wins_degraded_line(tmp_path, monkeypatch):
    # Matrix 18: two sessions paste [secfix W1-L3]; the newer time_updated
    # wins EVEN THOUGH its sid sorts LOWER (time_updated dominates the masters
    # key's final id term). The loser renders via the EXACT degraded line and
    # is enumerated in sessions_unmapped (invisible_drop empties its tag
    # view). Sids: s18a older/greater, s18b newer/lesser.
    NOW = MCWALLT_WORLD_NOW

    def ms(age):
        return int((NOW - age) * 1000)

    s18a = "sess_4d4d4d4d-4d4d-44dd-84dd-4d4d4d4d4d4d"
    s18b = "sess_4c4c4c4c-4c4c-44cc-84cc-4c4c4c4c4c4c"
    cfg, _set = mcwallt_world(
        tmp_path, monkeypatch,
        extra_sessions=[
            {"id": s18a, "title": "mcwallt s18a",
             "time_updated": ms(500), "time_created": ms(500)},
            {"id": s18b, "title": "mcwallt s18b",
             "time_updated": ms(100), "time_created": ms(100)}],
        extra_inputs=[mcwallt_titled_tag_input(s18a, "secfix", "W1-L3", ms(500)),
                      mcwallt_titled_tag_input(s18b, "secfix", "W1-L3", ms(100))])
    state = collect_state(cfg)
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    assert lanes["W1-L3"]["session"]["id"] == s18b
    assert ("tag bind degraded: secfix/W1-L3 newest wins, losers " + s18a) \
        in state["server"]["degraded"]
    assert s18a in {r["id"] for r in state["sessions_unmapped"]}


def test_wa_1_bind_tie_id_wins(tmp_path, monkeypatch):
    # Matrix 19: fully tied time_updated/time_created — the masters key's
    # final sid term decides: the lexicographically GREATEST sid binds and the
    # degraded line names the lesser one.
    NOW = MCWALLT_WORLD_NOW

    def ms(age):
        return int((NOW - age) * 1000)

    s19a = "sess_4e4e4e4e-4e4e-44ee-84ee-4e4e4e4e4e4e"  # lesser sid: the loser
    s19b = "sess_4f4f4f4f-4f4f-44ff-84ff-4f4f4f4f4f4f"  # greater sid: binds
    cfg, _set = mcwallt_world(
        tmp_path, monkeypatch,
        extra_sessions=[
            {"id": s19a, "title": "mcwallt s19a",
             "time_updated": ms(200), "time_created": ms(200)},
            {"id": s19b, "title": "mcwallt s19b",
             "time_updated": ms(200), "time_created": ms(200)}],
        extra_inputs=[mcwallt_titled_tag_input(s19a, "secfix", "W1-L3", ms(200)),
                      mcwallt_titled_tag_input(s19b, "secfix", "W1-L3", ms(200))])
    state = collect_state(cfg)
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    assert lanes["W1-L3"]["session"]["id"] == s19b
    assert ("tag bind degraded: secfix/W1-L3 newest wins, losers " + s19a) \
        in state["server"]["degraded"]


def test_wa_1_bind_ambiguous_session(tmp_path, monkeypatch):
    # Matrix 20 (Q3/Q7): one session pasting [secfix W1-L2] AND [secfix W1-L3]
    # claims two rows — it binds NOTHING, emits the ambiguous-session line,
    # and renders unmapped; with both pastes under the ONE configured program
    # tag there is NO entry-7 "join degraded: ambiguous tags" line (visibility
    # moved to the Q3 line).
    NOW = MCWALLT_WORLD_NOW

    def ms(age):
        return int((NOW - age) * 1000)

    s20 = "sess_50505050-5050-4505-8505-505050505050"
    cfg, _set = mcwallt_world(
        tmp_path, monkeypatch,
        extra_sessions=[{"id": s20, "title": "mcwallt s20",
                         "time_updated": ms(200), "time_created": ms(200)}],
        extra_inputs=[mcwallt_titled_tag_input(s20, "secfix", "W1-L2", ms(200)),
                      mcwallt_titled_tag_input(s20, "secfix", "W1-L3", ms(200))])
    state = collect_state(cfg)
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    assert lanes["W1-L2"]["session"] is None
    assert lanes["W1-L3"]["session"] is None
    assert f"tag bind degraded: ambiguous session {s20}" in state["server"]["degraded"]
    assert not any("join degraded: ambiguous tags" in d
                   for d in state["server"]["degraded"])
    assert s20 in {r["id"] for r in state["sessions_unmapped"]}


def test_wa_1_bind_foreign_tag_ignored(tmp_path, monkeypatch):
    # Matrix 21: [otherprog W9-L9] — an unconfigured tag pointing at an
    # unknown row never matches a lane (conditions b/c), so no binding and NO
    # tag-bind degraded line (absence is not failure); S21 still enumerates in
    # sessions_unmapped (unconfigured single tag — the pre-existing rule). The
    # control twin binding W1-L3 keeps the negative pin non-vacuous.
    NOW = MCWALLT_WORLD_NOW

    def ms(age):
        return int((NOW - age) * 1000)

    s21 = "sess_51515151-5151-4515-8515-515151515151"
    s21_ctl = "sess_58585858-5858-4585-8585-585858585858"
    cfg, _set = mcwallt_world(
        tmp_path, monkeypatch,
        extra_sessions=[
            {"id": s21, "title": "mcwallt s21",
             "time_updated": ms(60), "time_created": ms(60)},
            {"id": s21_ctl, "title": "mcwallt s21 control",
             "time_updated": ms(60), "time_created": ms(60)}],
        extra_inputs=[mcwallt_titled_tag_input(s21, "otherprog", "W9-L9", ms(60)),
                      mcwallt_titled_tag_input(s21_ctl, "secfix", "W1-L3", ms(60))])
    state = collect_state(cfg)
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    assert all(l["session"] is None or l["session"]["id"] != s21
               for l in lanes.values())
    assert lanes["W1-L3"]["session"]["id"] == s21_ctl
    assert not any("tag bind degraded" in d for d in state["server"]["degraded"])
    assert s21 in {r["id"] for r in state["sessions_unmapped"]}


def test_wa_1_bind_readonly_db(tmp_path, monkeypatch):
    # Matrix 22: with a titled paste in the world (a binding flows once T2
    # lands), the db file's BYTES and the directory listing are identical
    # before and after collect_state — the deterministic no-write proof
    # (byte-hash equality + no -wal/-shm sidecar files) under the world's
    # fixed clock. The goal/note files in tmp_path are never written after
    # the world build.
    NOW = MCWALLT_WORLD_NOW

    def ms(age):
        return int((NOW - age) * 1000)

    s22 = "sess_52525252-5252-4525-8525-525252525252"
    cfg, _set = mcwallt_world(
        tmp_path, monkeypatch,
        extra_sessions=[{"id": s22, "title": "mcwallt s22",
                         "time_updated": ms(200), "time_created": ms(200)}],
        extra_inputs=[mcwallt_titled_tag_input(s22, "secfix", "W1-L3", ms(200))])
    db = Path(cfg.db_path)
    before_hash = hashlib.sha256(db.read_bytes()).hexdigest()
    before_ls = sorted(os.listdir(tmp_path))
    collect_state(cfg)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before_hash
    assert sorted(os.listdir(tmp_path)) == before_ls


def test_wa_1_bind_stale_token_blocks_binding(tmp_path, monkeypatch):
    # Matrix 26 (critic finding 4): W1-L2's artifacts cell carries
    # sess_0deadbee — a well-formed 8-hex token that joins NOTHING. Condition
    # (d) evaluates the PARSED row before any join outcome, so the stale token
    # still blocks tag binding: W1-L2 stays null, S26 renders invisible (Q5:
    # absent from sessions_unmapped), and ZERO tag-bind degraded lines fire.
    # The control twin binding W1-L3 proves the blocking is the token, not a
    # dead binding path.
    NOW = MCWALLT_WORLD_NOW

    def ms(age):
        return int((NOW - age) * 1000)

    s26 = "sess_53535353-5353-4535-8535-535353535353"
    s26_ctl = "sess_59595959-5959-4595-8595-595959595959"
    rows = ["| W1-L2 | W1 | L2 | n/a | n/a | n/a | sess_0deadbee | launched |",
            "| W1-L3 | W1 | L3 | n/a | n/a | n/a | n/a | done |"]
    cfg, _set = mcwallt_world(
        tmp_path, monkeypatch, rows=rows,
        extra_sessions=[
            {"id": s26, "title": "mcwallt s26",
             "time_updated": ms(200), "time_created": ms(200)},
            {"id": s26_ctl, "title": "mcwallt s26 control",
             "time_updated": ms(200), "time_created": ms(200)}],
        extra_inputs=[mcwallt_titled_tag_input(s26, "secfix", "W1-L2", ms(200)),
                      mcwallt_titled_tag_input(s26_ctl, "secfix", "W1-L3", ms(200))])
    state = collect_state(cfg)
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    assert lanes["W1-L2"]["session"] is None
    assert lanes["W1-L3"]["session"]["id"] == s26_ctl
    assert s26 not in {r["id"] for r in state["sessions_unmapped"]}
    assert not any("tag bind degraded" in d for d in state["server"]["degraded"])


def test_wa_1_bind_orphan_claim_dropped(tmp_path, monkeypatch):
    # Matrix 27 (critic finding 4): the orphan sid pastes [secfix W1-L3] but
    # has NO session row — session_rows drops it BEFORE newest-wins, so the
    # healthy S27 binds as the sole surviving claim with NO degraded line.
    NOW = MCWALLT_WORLD_NOW

    def ms(age):
        return int((NOW - age) * 1000)

    s27 = "sess_54545454-5454-4545-8545-545454545454"
    orphan = "sess_orphan1111-1111-4111-8111-111111111111"
    cfg, _set = mcwallt_world(
        tmp_path, monkeypatch,
        extra_sessions=[{"id": s27, "title": "mcwallt s27",
                         "time_updated": ms(200), "time_created": ms(200)}],
        extra_inputs=[mcwallt_titled_tag_input(s27, "secfix", "W1-L3", ms(200)),
                      mcwallt_titled_tag_input(orphan, "secfix", "W1-L3", ms(200))])
    state = collect_state(cfg)
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    assert lanes["W1-L3"]["session"]["id"] == s27
    assert not any("tag bind degraded" in d for d in state["server"]["degraded"])


def test_wa_1_bind_title_prefixed_form(tmp_path, monkeypatch):
    # Matrix 28 (Q1): the title stored as the FULL line "Session title:
    # [secfix W1-L3] fix" — TITLE_TAG_RE's optional-prefix form — binds W1-L3
    # exactly like the bare-bracket form of matrix 13.
    NOW = MCWALLT_WORLD_NOW

    def ms(age):
        return int((NOW - age) * 1000)

    s28 = "sess_55555555-5555-4555-8555-555555555555"
    cfg, _set = mcwallt_world(
        tmp_path, monkeypatch,
        extra_sessions=[{"id": s28, "title": "Session title: [secfix W1-L3] fix",
                         "time_updated": ms(3600), "time_created": ms(3600)}])
    state = collect_state(cfg)
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    assert lanes["W1-L3"]["session"]["id"] == s28


# --- matrix 23-25: the verified token clears the verify cue (goal T3) ---------

def test_wa_1_verified_token_parses():
    # Matrix 23: the artifacts cell "!5; sess_9a690ab2; verify:ok" sets the
    # verified flag WITHOUT disturbing token extraction — the token cannot
    # collide with SESS_RE/BANG_RE/HASH_RE, so sess_token/mr_bang parse exactly
    # as in a token-less twin row (which stays verified=False, the defaulted
    # field). The token is a controller-only convention: incidental substring
    # occurrences are accepted by design, not ruled out.
    text = "\n".join([
        "| id | wave | lane | repo/branch | slug | base | session/MR artifacts | status |",
        "|---|---|---|---|---|---|---|---|",
        "| W1-L1 | W1 | L1 | n/a | n/a | n/a | !5; sess_9a690ab2; verify:ok | done |",
        "| W1-L2 | W1 | L2 | n/a | n/a | n/a | !5; sess_9a690ab2 | done |",
    ])
    rows = notes.parse_note(text).rows
    assert rows[0].verified is True
    assert rows[0].sess_token == "sess_9a690ab2"
    assert rows[0].mr_bang == "!5"
    assert rows[1].verified is False


def test_wa_1_verified_clears_suggest():
    # Matrix 24: verified=True short-circuits the cue (None) even past grace;
    # the keyword default preserves today's behavior for every existing
    # positional caller (omitted == explicit False).
    assert derive.derive_suggest_verify("done", 400, 300) is not None
    assert derive.derive_suggest_verify("done", 400, 300, verified=False) is not None
    assert derive.derive_suggest_verify("done", 400, 300, verified=True) is None


def test_wa_1_verified_e2e_clears_cue(tmp_path, monkeypatch):
    # Matrix 25: the full pipeline — control (default world: W1-L1 done,
    # token-joined, aged 400 s > grace 300 s) fires suggest_verify; the same
    # world with W1-L1's artifacts cell carrying verify:ok clears ONLY the lane
    # cue — the verify_queue row stays with the joined full db id in verify_cmd
    # (the operator's entry point, Q6 queue-unchanged pin).
    ctl_cfg, _set = mcwallt_world(tmp_path, monkeypatch)
    ctl_state = collect_state(ctl_cfg)
    ctl_lanes = {l["row_id"]: l for l in ctl_state["programs"][0]["lanes"]}
    assert ctl_lanes["W1-L1"]["suggest_verify"] is not None
    # Same world rebuilt (default name, files overwritten), W1-L1's artifacts
    # cell alone changed; the repo path is the default name's own derivation.
    repo = tmp_path / "mcwallt_world_repo"
    rows = [
        f"| W1-L1 | W1 | L1 | {repo} loop/mcwall-tower | mcwallt-slug | n/a | !5; sess_9a690ab2; verify:ok | done |",
        "| W1-L2 | W1 | L2 | plugin cache 1.3.0 (plain lane) | n/a | n/a | n/a | launched |",
        f"| W1-L3 | W1 | L3 | {repo} main | mcwallt-slug-3 | n/a | n/a | done |",
    ]
    cfg, _set = mcwallt_world(tmp_path, monkeypatch, rows=rows)
    state = collect_state(cfg)
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    assert lanes["W1-L1"]["suggest_verify"] is None
    w1l1 = [r for r in state["verify_queue"] if r["row_id"] == "W1-L1"]
    assert len(w1l1) == 1
    assert w1l1[0]["verify_cmd"] == f"/mission-control-verify {MCWALLT_WORLD_LANE}"


# --- MR-review pin: the newest-wins key is None-safe against SQL-NULL ts ------

def test_wa_1_bind_newest_wins_null_timestamp_safe(tmp_path, monkeypatch):
    # Two sessions paste [secfix W1-L3] for the token-less lane; one claimant
    # carries a SQL-NULL time_updated (mcwallt_make_db inserts it verbatim),
    # the other a real timestamp. The None-safe key prefix (ts is not None)
    # makes ANY real timestamp beat the NULL one — no exception out of max(),
    # the NULL claimant takes the loser's degraded line, and the bound lane
    # reports the winner's REAL age (200), not the 0 a NULL winner's
    # unknown-age convention would render.
    NOW = MCWALLT_WORLD_NOW

    def ms(age):
        return int((NOW - age) * 1000)

    s_null = "sess_60606060-6060-4606-8606-606060606060"
    s_real = "sess_61616161-6161-4616-8616-616161616161"
    cfg, _set = mcwallt_world(
        tmp_path, monkeypatch,
        extra_sessions=[
            {"id": s_null, "title": "mcwallt null ts",
             "time_updated": None, "time_created": ms(200)},
            {"id": s_real, "title": "mcwallt real ts",
             "time_updated": ms(200), "time_created": ms(200)}],
        extra_inputs=[mcwallt_titled_tag_input(s_null, "secfix", "W1-L3", ms(200)),
                      mcwallt_titled_tag_input(s_real, "secfix", "W1-L3", ms(200))])
    state = collect_state(cfg)
    lanes = {l["row_id"]: l for l in state["programs"][0]["lanes"]}
    assert lanes["W1-L3"]["session"]["id"] == s_real
    assert ("tag bind degraded: secfix/W1-L3 newest wins, losers " + s_null) \
        in state["server"]["degraded"]
    assert lanes["W1-L3"]["session"]["last_active_ago_s"] == 200
