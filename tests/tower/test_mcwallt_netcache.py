"""T-1 netcache tests: _run_cmd ok/error/exception paths (local spawns only)."""

import sys

from mc_wall.tower.netcache import _run_cmd


def test_mcwallt_run_cmd_ok(tmp_path):
    rc, out, err = _run_cmd([sys.executable, "-c", "print('mcwallt')"], cwd=str(tmp_path))
    assert (rc, out, err) == (0, "mcwallt\n", "")


def test_mcwallt_run_cmd_error_and_exceptions(tmp_path):
    # Non-zero exit code is captured, not raised.
    rc, out, _err = _run_cmd([sys.executable, "-c", "raise SystemExit(2)"],
                             cwd=str(tmp_path))
    assert rc == 2
    assert out == ""

    # Missing binary: no raise, failure result.
    assert _run_cmd(["mcwallt-no-such-bin"], cwd=str(tmp_path)) == (None, "", "")

    # Timeout: no raise, failure result.
    assert _run_cmd([sys.executable, "-c", "import time; time.sleep(5)"],
                    cwd=str(tmp_path), timeout_s=0.2) == (None, "", "")
