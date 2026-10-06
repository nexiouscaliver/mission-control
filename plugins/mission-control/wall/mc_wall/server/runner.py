"""Runner interface + macOS subprocess runner (pbcopy / open / osascript).

Every spawn carries an explicit timeout (wd1, wall-deadlock 2026-10-07): the
pasteboard, LaunchServices, and the notification center are GUI services —
under system duress they can wedge for minutes, and an unbounded wait here
hangs the handshake monitor tick and the POST side effects. A timed-out side
effect raises (TimeoutExpired) and is contained by the existing per-call
wrappers — fail loud, never hang.
"""

from __future__ import annotations

import subprocess

RUNNER_TIMEOUT_S = 5.0


class Runner:
    """Side-effect interface; tests substitute FakeRunner or inject a fake exec."""

    def copy(self, text: str) -> None: ...

    def open_url(self, url: str) -> None: ...

    def open_app(self, name: str) -> None: ...

    def notify(self, title: str, body: str) -> None: ...


def applescript_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def notification_script(title: str, body: str) -> str:
    return 'display notification "{}" with title "{}"'.format(
        applescript_escape(body), applescript_escape(title)
    )


class SubprocessRunner(Runner):
    def __init__(self, exec=None, timeout_s: float = RUNNER_TIMEOUT_S):
        self._exec = exec or subprocess.run
        self._timeout_s = timeout_s

    def copy(self, text: str) -> None:
        self._exec(["pbcopy"], input=text.encode("utf-8"), check=True,
                   timeout=self._timeout_s)

    def open_url(self, url: str) -> None:
        self._exec(["open", url], check=True, timeout=self._timeout_s)

    def open_app(self, name: str) -> None:
        self._exec(["open", "-a", name], check=True, timeout=self._timeout_s)

    def notify(self, title: str, body: str) -> None:
        self._exec(
            ["osascript", "-e", notification_script(title, body)], check=True,
            timeout=self._timeout_s
        )
