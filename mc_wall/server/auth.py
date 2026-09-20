"""Auth primitives: Host allowlist (DNS-rebinding defense) + token check."""

from __future__ import annotations

import secrets
import typing

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})


def host_allowed(
    host_header: typing.Optional[str],
    allowed: typing.Iterable[str] = ALLOWED_HOSTS,
) -> bool:
    """True iff the Host header names an allowed host (any :port stripped)."""
    if host_header is None:
        return False
    name = host_header.strip().lower().rsplit(":", 1)[0]
    return name in allowed


def token_matches(
    provided: typing.Optional[str], expected: typing.Optional[str]
) -> bool:
    """Constant-time token comparison; None on either side never matches.

    Compared as UTF-8 bytes: compare_digest rejects non-ASCII str, and a raw
    request line can carry non-ASCII bytes in the token segment. The encoding
    is injective, so this stays constant-time and a non-ASCII segment can
    never match an ASCII token.
    """
    if provided is None or expected is None:
        return False
    return secrets.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    )
