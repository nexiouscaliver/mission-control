"""T4 — runner: applescript escaping + SubprocessRunner argv lists (AC-27).

Every mc_wall import is INSIDE the test functions, mirroring
test_mcwalls_boot.py: at RED time each test must FAIL individually (exit 1)
instead of erroring at collection (exit 2).
"""


def test_applescript_escapes_quotes_and_backslashes():
    from mc_wall.server.runner import applescript_escape, notification_script

    tag = '[fix "q" \\ u]'
    script = notification_script("t", tag)

    # Exactly one notification statement with one title clause.
    assert script.count("display notification") == 1
    assert script.count("with title") == 1

    # Two quoted segments: the substituted body and the title.
    _, _, rest = script.partition('display notification "')
    body_seg, _, tail = rest.partition('" with title "')
    title_seg = tail[: -1]  # drop the closing quote
    assert title_seg == "t"

    # Every `"` inside the substituted region is backslash-escaped.
    for i, ch in enumerate(body_seg):
        if ch == '"':
            assert i > 0 and body_seg[i - 1] == "\\"

    assert applescript_escape('a"b\\c') == 'a\\"b\\\\c'


def test_subprocess_runner_argv_lists():
    from mc_wall.server.runner import SubprocessRunner, notification_script

    calls = []

    def fake_exec(argv, **kwargs):
        calls.append((argv, kwargs))

    r = SubprocessRunner(exec=fake_exec)
    r.copy("x")
    r.open_url("u")
    r.open_app("ZCode")
    r.notify("t", "b")

    assert calls[0] == (["pbcopy"], {"input": b"x", "check": True})
    assert calls[1] == (["open", "u"], {"check": True})
    assert calls[2] == (["open", "-a", "ZCode"], {"check": True})
    assert calls[3] == (
        ["osascript", "-e", notification_script("t", "b")],
        {"check": True},
    )
    for argv, kwargs in calls:
        assert isinstance(argv, list)  # argv list, never a shell string
        assert kwargs.get("check") is True
        assert kwargs.get("shell") is not True  # never shell=True
