"""T4 — pending store: fsync'd atomic writes, quarantine, slot semantics.

Every mc_wall / harness import is INSIDE the test functions, mirroring
test_mcwalls_boot.py: at RED time each test must FAIL individually (exit 1)
instead of erroring at collection (exit 2).
"""

import dataclasses
import re
import stat


def _rec(**kw):
    from mc_wall.server.pending import PendingRecord

    base = dict(
        status="prompt-armed",
        row_id="secfix-w2-l7",
        lane_tag="[secfix W2-L7]",
        repo_root="/abs/repo",
        prompt_sha256="0" * 64,
        launch_click_ms=1000,
    )
    base.update(kw)
    return PendingRecord(**base)


def test_create_then_load_roundtrip():
    from mc_wall.server.pending import PENDING_FILENAME, PendingRecord, PendingStore
    from tests.server.mcwalls_harness import FakeClock, make_tmp_root

    clock = FakeClock(12345)
    store = PendingStore(make_tmp_root(), clock=clock)
    rec = _rec()
    store.create(rec)
    assert rec.updated_at_ms == 12345  # create stamps the clock

    fresh = PendingStore(store.state_dir, clock=FakeClock(99999))
    loaded = fresh.load()
    assert loaded == rec
    assert loaded.to_dict() == rec.to_dict()
    # Key order follows the spec schema exactly (dataclass field order), and
    # the record carries the full field set — checked against the class
    # itself so the pinned schema and this test can never drift apart.
    field_names = [f.name for f in dataclasses.fields(PendingRecord)]
    assert list(loaded.to_dict().keys()) == field_names

    mode = stat.S_IMODE((store.state_dir / PENDING_FILENAME).stat().st_mode)
    assert mode == 0o600

    # from_dict tolerance: unknown keys ignored, missing keys defaulted.
    assert PendingRecord.from_dict({"status": "x", "surprise": 1}) == PendingRecord(
        status="x"
    )


def test_write_atomic_leaves_no_tmp():
    from mc_wall.server.pending import PENDING_FILENAME, PendingStore
    from tests.server.mcwalls_harness import FakeClock, make_tmp_root

    store = PendingStore(make_tmp_root(), clock=FakeClock(1))
    store.create(_rec())
    store.update(lambda r: dataclasses.replace(r, status="await-birth"))
    leftovers = [
        p.name
        for p in store.state_dir.iterdir()
        if p.name.startswith(f"{PENDING_FILENAME}.tmp-")
    ]
    assert leftovers == []
    assert (store.state_dir / PENDING_FILENAME).is_file()


def test_update_mutates_under_lock():
    from mc_wall.server.pending import STATUS_AWAIT_BIRTH, PendingStore
    from tests.server.mcwalls_harness import FakeClock, make_tmp_root

    clock = FakeClock(100)
    store = PendingStore(make_tmp_root(), clock=clock)
    store.create(_rec())
    assert store.snapshot().updated_at_ms == 100

    clock.advance(50)
    out = store.update(lambda r: dataclasses.replace(r, status=STATUS_AWAIT_BIRTH))
    assert out is not None
    assert out.status == STATUS_AWAIT_BIRTH
    assert out.updated_at_ms == 150
    snap = store.snapshot()
    assert snap.status == STATUS_AWAIT_BIRTH
    assert snap.updated_at_ms == 150

    # mutate returning None -> no write, None returned, snapshot unchanged.
    assert store.update(lambda r: None) is None
    assert store.snapshot() == snap

    # The mutation was persisted, not just held in memory.
    assert PendingStore(store.state_dir).load().status == STATUS_AWAIT_BIRTH


def test_slot_occupied_semantics():
    from mc_wall.server.pending import (
        STATUS_AWAIT_BIRTH,
        STATUS_CLEARED,
        STATUS_FLAGGED,
        STATUS_GOAL_ARMED,
        STATUS_PROMPT_ARMED,
        PendingStore,
    )
    from tests.server.mcwalls_harness import FakeClock, make_tmp_root

    store = PendingStore(make_tmp_root(), clock=FakeClock(1))
    assert store.slot_occupied() is False  # nothing stored -> False

    for status in (STATUS_PROMPT_ARMED, STATUS_AWAIT_BIRTH, STATUS_GOAL_ARMED):
        store.create(_rec(status=status))
        assert store.slot_occupied() is True, status

    for status in (STATUS_CLEARED, STATUS_FLAGGED):
        store.create(_rec(status=status))
        assert store.slot_occupied() is False, status


def test_corrupt_file_quarantined():
    from mc_wall.server.pending import PENDING_FILENAME, PendingStore
    from tests.server.mcwalls_harness import make_tmp_root

    state_dir = make_tmp_root()
    (state_dir / PENDING_FILENAME).write_bytes(b"not json{")
    store = PendingStore(state_dir)
    assert store.load() is None
    assert store.snapshot() is None

    quarantined = [
        p.name
        for p in state_dir.iterdir()
        if p.name.startswith("pending.corrupt-")
    ]
    assert len(quarantined) == 1
    assert re.fullmatch(r"pending\.corrupt-\d+", quarantined[0])
    assert not (state_dir / PENDING_FILENAME).exists()  # original gone


def test_summary_triple():
    rec = _rec(flag=None)
    assert rec.summary() == {
        "status": rec.status,
        "row_id": rec.row_id,
        "flag": rec.flag,
    }
    assert set(rec.summary().keys()) == {"status", "row_id", "flag"}
