"""Final review Should-fixes: abnormal-input paths through CLI + entry + store.

1. open/status with NO wall.json: one clear "run mc-wall install" line (never
   a KeyError/traceback, never an accidental `open`), status pins its
   previously-accidental "http: failed" + rc 1.
2. install over a CORRUPT wall.json: the recovery command itself must heal
   (fresh token, rewritten file, mode 600) instead of crashing.
3. load_config with a bad port (null / non-numeric): one-line ValueError
   naming wall.json, never a TypeError traceback.
4. PendingStore.load over non-UTF-8 pending.json: quarantined + one WARNING
   (an escaping UnicodeDecodeError would crash boot -> launchd restart loop).
"""

import io
import json
import logging
import re
import stat


def test_open_without_wall_json():
    from tests.server import mcwalls_harness as H

    cli = H.load_cli()
    wh = H.make_tmp_root("mcwalls-wh-")
    home = H.make_tmp_root("mcwalls-home-")

    executor = H.FakeExecutor()
    out = io.StringIO()
    c = cli.Cli(str(wh), str(home), executor, stdout=out)
    assert c.open_wall() == 1
    lines = out.getvalue().splitlines()
    assert len(lines) == 1  # ONE clear line, no traceback
    assert "mc-wall install" in lines[0]
    assert executor.argvs == []  # nothing was opened
    assert not (wh / "chrome-profile").exists()  # no side effects at all

    # status with the same missing wall.json: pin the verdict shape.
    out2 = io.StringIO()
    c2 = cli.Cli(str(wh), str(home), H.FakeExecutor(), stdout=out2)
    assert c2.status() == 1
    text2 = out2.getvalue()
    assert "mc-wall install" in text2
    assert "http: failed" in text2


def test_install_over_corrupt_wall_json():
    from tests.server import mcwalls_harness as H

    cli = H.load_cli()
    wh = H.make_tmp_root("mcwalls-wh-")
    home = H.make_tmp_root("mcwalls-home-")
    (wh / "wall.json").write_bytes(b"\xff\xfe\x00 not json \x9c")

    executor = H.FakeExecutor()
    c = cli.Cli(str(wh), str(home), executor, stdout=io.StringIO())
    assert c.install() == 0  # the recovery command must not crash

    data = json.loads((wh / "wall.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"[A-Za-z0-9_-]{20,}", data["token"])
    assert data["port"] == 8765
    assert stat.S_IMODE((wh / "wall.json").stat().st_mode) == 0o600


def test_load_config_bad_port():
    import pytest

    from mc_wall.server.__main__ import load_config
    from tests.server import mcwalls_harness as H

    wh = H.make_tmp_root("mcwalls-wh-")
    for bad in (None, "abc"):  # "port": null -> TypeError; "abc" -> ValueError
        (wh / "wall.json").write_text(
            json.dumps({"token": "tok", "port": bad}), encoding="utf-8"
        )
        with pytest.raises(ValueError) as exc_info:
            load_config(wh)
        msg = str(exc_info.value)
        assert "\n" not in msg  # ONE clear line, not a traceback
        assert "wall.json" in msg


def test_non_utf8_pending_quarantined(caplog):
    from mc_wall.server.pending import PendingStore
    from tests.server import mcwalls_harness as H

    state = H.make_tmp_root("mcwalls-state-")
    (state / "pending.json").write_bytes(b"\xff\xfe garbage")
    store = PendingStore(state)
    with caplog.at_level(logging.WARNING, logger="mc_wall.server.pending"):
        assert store.load() is None
    quarantined = [
        p.name for p in state.iterdir() if p.name.startswith("pending.corrupt-")
    ]
    assert len(quarantined) == 1
    assert re.fullmatch(r"pending\.corrupt-\d+", quarantined[0])
    assert not (state / "pending.json").exists()  # moved, not copied
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "corrupt" in r.getMessage()
    ]
    assert len(warnings) == 1
