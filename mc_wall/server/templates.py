"""Template directory + plain-token rendering for the Wall server."""

from __future__ import annotations

import configparser
import pathlib
import typing


def templates_dir() -> pathlib.Path:
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    return repo_root / "templates"


def load(name: str) -> str:
    return (templates_dir() / name).read_text(encoding="utf-8")


def render(template_text: str, mapping: dict) -> str:
    text = template_text
    for key, value in mapping.items():
        # None renders as "" — a literal "None" must never reach a clipboard block.
        text = text.replace("{" + str(key) + "}", "" if value is None else str(value))
    return text


def render_file(name: str, mapping: dict) -> str:
    return render(load(name), mapping)


def notification(section: str, mapping: dict) -> typing.Tuple[str, str]:
    # interpolation=None: template bodies carry {token} placeholders handled by
    # render() and must never be pre-processed by configparser's % interpolation.
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(load("notifications.ini"))
    try:
        title = parser[section]["title"]
        body = parser[section]["body"]
    except KeyError:
        raise ValueError("unknown-notification-section") from None
    return render(title, mapping), render(body, mapping)
