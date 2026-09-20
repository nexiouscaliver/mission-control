"""T2 — test harness, logging setup, server boot + self-test.

Imports of mc_wall modules (and of the harness, which is only written in the
implementation step) are deliberately INSIDE each test: with the implementation
absent, each test must FAIL individually (pytest exit 1) instead of erroring
at collection time (exit 2), so the TDD red receipt carries item-level
evidence. The harness is imported package-qualified because tests/ carries
__init__.py files (pytest package mode).
"""

import logging
import logging.handlers
import pathlib
import socket

import pytest


def _drop_wall_log_handlers(log_dir) -> None:
    # Same fd-leak guard as the harness serve() teardown: run_server /
    # setup_logging / create_server calls outside serve() attach a
    # RotatingFileHandler to the process-global "mc_wall.server" logger.
    logger = logging.getLogger("mc_wall.server")
    base = str(pathlib.Path(log_dir) / "wall.log")
    for handler in [x for x in logger.handlers if getattr(x, "baseFilename", None) == base]:
        logger.removeHandler(handler)
        handler.close()


def test_socket_binds_loopback_only():
    from tests.server.mcwalls_harness import serve

    with serve() as h:
        assert h.server.server_address[0] == "127.0.0.1"
        assert h.server.server_address[1] > 0


def test_ready_true_with_working_allowlist():
    from tests.server.mcwalls_harness import serve

    with serve() as h:
        assert h.server.ready is True


def test_broken_allowlist_fails_selftest():
    from mc_wall.server.app import create_server
    from tests.server.mcwalls_harness import make_tmp_root, make_web_dir

    log_dir = make_tmp_root() / "logs"
    try:
        # DIRECTLY, not via serve(): the harness asserts srv.ready, and this
        # configuration must reach that check with ready already False.
        srv = create_server(
            "t",
            port=0,
            web_dir=make_web_dir(),
            log_dir=log_dir,
            allow_hosts=frozenset(),
        )
        assert srv.ready is False
        with pytest.raises((ConnectionRefusedError, OSError)):
            conn = socket.create_connection(srv.server_address, timeout=2)
            conn.close()
    finally:
        _drop_wall_log_handlers(log_dir)


def test_port_busy_exits_1_one_error_line():
    from mc_wall.server.app import ServerConfig, run_server
    from tests.server.mcwalls_harness import make_tmp_root, make_web_dir

    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    port = busy.getsockname()[1]
    tmp = make_tmp_root()
    logs = tmp / "logs"
    try:
        cfg = ServerConfig(
            token="t",
            port=port,
            wall_home=tmp,
            web_dir=make_web_dir(),
            state_dir=tmp / "state",
            log_dir=logs,
        )
        assert run_server(cfg) == 1
        err_lines = [
            line
            for line in (logs / "wall.log").read_text(encoding="utf-8").splitlines()
            if "ERROR" in line
        ]
        assert len(err_lines) == 1
        assert str(port) in err_lines[0]
    finally:
        busy.close()
        _drop_wall_log_handlers(logs)


def test_rotating_handler_attached():
    from tests.server.mcwalls_harness import serve

    with serve() as h:
        handlers = [
            x
            for x in logging.getLogger("mc_wall.server").handlers
            if getattr(x, "baseFilename", None) == str(h.log_dir / "wall.log")
        ]
        assert len(handlers) == 1
        assert isinstance(handlers[0], logging.handlers.RotatingFileHandler)
        assert handlers[0].maxBytes == 5 * 1024 * 1024
        assert handlers[0].backupCount == 3


def test_redact_token_filter():
    from mc_wall.server.logging_setup import setup_logging
    from tests.server.mcwalls_harness import make_tmp_root

    logs = make_tmp_root() / "logs"
    try:
        logger = setup_logging(logs, "sekrit")
        logger.error("boom sekrit boom")
        for handler in logger.handlers:
            handler.flush()
        text = (logs / "wall.log").read_text(encoding="utf-8")
        assert "boom <redacted> boom" in text
        assert "sekrit" not in text
    finally:
        _drop_wall_log_handlers(logs)


def test_run_server_returns_3_when_never_ready(monkeypatch, tmp_path):
    import mc_wall.server.app as app

    class NeverReadyServer:
        ready = False

        def wait_shutdown(self):
            pass

        def server_close(self):
            pass

    monkeypatch.setattr(app, "create_server", lambda *args, **kwargs: NeverReadyServer())
    logs = tmp_path / "logs"
    try:
        cfg = app.ServerConfig(
            token="t",
            wall_home=tmp_path,
            web_dir=tmp_path / "web",
            state_dir=tmp_path / "state",
            log_dir=logs,
        )
        assert app.run_server(cfg) == 3
    finally:
        _drop_wall_log_handlers(logs)


def test_mcwallf_tower_config_built_once_across_requests(monkeypatch, tmp_path):
    import http.client as _hc

    import mc_wall.server.app as app
    import mc_wall.server.tower_boot as tower_boot
    from tests.server.mcwalls_harness import make_web_dir

    (tmp_path / "wall.json").write_text('{"token": "t"}', encoding="utf-8")
    monkeypatch.setenv("MC_WALL_HOME", str(tmp_path))  # hermetic: resolve_wall_home()
    # must NOT read the operator's real ~/.zcode/mc-wall/wall.json (a live
    # TowerConfig would run real git/glab spawns inside pytest; and on machines
    # without that file the test would be a permanent RED)
    monkeypatch.setenv("MC_WALL_DB", str(tmp_path / "no-db.sqlite"))  # never the real db
    calls = {"n": 0}
    real = tower_boot.build_tower_config

    def counting(wall_home):
        calls["n"] += 1
        return real(wall_home)

    monkeypatch.setattr(tower_boot, "build_tower_config", counting)
    monkeypatch.setattr(app, "_default_tower_config_box", [])
    logs = tmp_path / "logs"
    from mc_wall.server.app import create_server

    srv = create_server("t", port=0, web_dir=make_web_dir(), log_dir=logs)
    try:
        for _ in range(4):
            conn = _hc.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
            conn.request("GET", "/t/state", headers={"Host": "127.0.0.1"})
            r = conn.getresponse()
            r.read()
            conn.close()
            assert r.status == 200
        assert calls["n"] == 1, "tower config must be built at most once per process"
    finally:
        srv.shutdown()
        srv.server_close()
        _drop_wall_log_handlers(logs)
