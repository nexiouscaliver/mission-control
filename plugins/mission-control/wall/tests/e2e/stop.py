"""SIGTERM the recorded serve.py pid, await exit, verify the port is freed.

Does NOT delete the temp home (SC-7 restart reuses it); the sweep is the
orchestrator's separate step.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import signal
import socket
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import e2e_common  # noqa: E402


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not ours to signal


def _port_free(port: int) -> bool:
    try:
        conn = socket.create_connection(("127.0.0.1", port), timeout=0.5)
        conn.close()
        return False
    except OSError:
        return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="stop the mcwall-e2e server")
    parser.add_argument("--home", required=True)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)
    env = e2e_common.load_env(pathlib.Path(args.home))
    pid, port = int(env["pid"]), int(env["port"])
    if pid == os.getpid():
        print("e2e-stop: refusing to signal myself", file=sys.stderr)
        return 1
    how = "already"
    if _alive(pid):
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline and _alive(pid):
            time.sleep(0.25)
        if _alive(pid):
            how = "kill"
            os.kill(pid, signal.SIGKILL)
            time.sleep(1.0)
            if _alive(pid):
                print("e2e-stop: pid %d survived SIGKILL" % pid, file=sys.stderr)
                return 2
        else:
            how = "term"
    for _ in range(20):  # grace for the OS to release the listening socket
        if _port_free(port):
            break
        time.sleep(0.25)
    if not _port_free(port):
        print("e2e-stop: pid gone but port %d still occupied — lsof -ti tcp:%d"
              % (port, port), file=sys.stderr)
        return 3
    print("E2E-STOP PID %d EXITED %s" % (pid, how))
    print("E2E-STOP PORT %d FREE" % port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
