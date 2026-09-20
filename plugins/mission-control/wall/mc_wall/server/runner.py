"""Runner interface + macOS subprocess runner (pbcopy / open / osascript)."""

from __future__ import annotations

import subprocess


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
    def __init__(self, exec=None):
        self._exec = exec or subprocess.run

    def copy(self, text: str) -> None:
        self._exec(["pbcopy"], input=text.encode("utf-8"), check=True)

    def open_url(self, url: str) -> None:
        self._exec(["open", url], check=True)

    def open_app(self, name: str) -> None:
        self._exec(["open", "-a", name], check=True)

    def notify(self, title: str, body: str) -> None:
        self._exec(
            ["osascript", "-e", notification_script(title, body)], check=True
        )
