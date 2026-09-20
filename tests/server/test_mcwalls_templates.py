"""T1 — template files + template loader (mc_wall.server.templates).

Imports of mc_wall.server.templates are deliberately INSIDE each test: with the
implementation absent, each test must FAIL individually (pytest exit 1) instead
of erroring at collection time (exit 2), so the TDD red receipt carries
item-level evidence.
"""

import pytest


def test_render_is_plain_token_replacement():
    from mc_wall.server import templates

    # Plain token replacement, NOT str.format: "{}" and "%" must pass through
    # untouched (str.format would blow up on the stray brace / mangle the %).
    out = templates.render("a {x} b", {"x": "50%_off {}"})
    assert out == "a 50%_off {} b"


def test_prompt_block_is_identity():
    from mc_wall.server import templates

    # prompt_block.txt is exactly "{prompt_text}" with no trailing newline:
    # rendering must return the prompt verbatim (AC-17 depends on this).
    prompt = "PROMPT TEXT 42\nsecond line 100% {not-a-token}"
    assert templates.render_file("prompt_block.txt", {"prompt_text": prompt}) == prompt


def test_goal_block_tokens():
    from mc_wall.server import templates

    out = templates.render_file(
        "goal_block.txt",
        {"goal_text": "GOAL", "lane_tag": "[tag]", "repo_root": "/abs/repo"},
    )
    assert "{goal_text}" not in out
    assert "{lane_tag}" not in out
    assert "{repo_root}" not in out
    assert "GOAL" in out
    assert "[tag]" in out
    assert "/abs/repo" in out


def test_notification_sections():
    from mc_wall.server import templates

    title, body = templates.notification("match-found", {"lane_tag": "[x]"})
    assert len((title, body)) == 2
    assert "[x]" in body
    with pytest.raises(ValueError):
        templates.notification("nope", {})


def test_plist_and_run_sh_placeholders():
    from mc_wall.server import templates

    plist = templates.load("plist.template")
    assert "{label}" in plist
    assert plist.count("{wall_home}") == 3
    assert "CrashedOnly" in plist
    assert "RunAtLoad" in plist
    run_sh = templates.load("run_sh.template")
    assert 'PYTHONPATH="{repo_root}"' in run_sh
    assert "exec /opt/homebrew/bin/python3.14 -m mc_wall.server" in run_sh


def test_wrong_token_page_names_cli():
    from mc_wall.server import templates

    assert "mc-wall open" in templates.load("wrong_token.html")
