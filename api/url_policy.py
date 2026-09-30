"""Shared first half of every upstream URL check.

Callers keep their own host and path rules. This part rejects what no caller
may accept: oversized or control characters, credentials, non-default ports,
fragments and unexpected schemes. The returned URL always uses HTTPS so an
account's cookies or a signed link never travel over cleartext HTTP.
"""

import html
import re
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

_UNSAFE = re.compile(r"[\x00-\x20\x7f\\]")
MAX_URL_LENGTH = 16384


@dataclass(frozen=True)
class UrlMessages:
    invalid: str
    untrusted: str
    path: str = ""


def canonical_https_url(
    value,
    *,
    messages: UrlMessages,
    allowed: Callable[[str, SplitResult], bool],
    base: Optional[str] = None,
    unescape: bool = True,
    allow_http: bool = True,
    path: Optional[Callable[[str], bool]] = None,
) -> str:
    """Validate ``value`` and return it as a canonical HTTPS URL.

    ``allowed(host, parts)`` decides whether the (lower-case) host and path are
    trusted; a separate ``path`` check reports ``messages.path`` instead.
    """
    if not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH:
        raise ValueError(messages.invalid)
    if unescape:
        value = html.unescape(value)
    if _UNSAFE.search(value):
        raise ValueError(messages.invalid)
    if base:
        value = urljoin(base, value)
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise ValueError(messages.invalid) from None
    host = (parts.hostname or "").lower()
    schemes = {"https", "http"} if allow_http else {"https"}
    if (parts.scheme not in schemes or parts.username is not None
            or parts.password is not None or port not in (None, 443)
            or parts.fragment or not allowed(host, parts)):
        raise ValueError(messages.untrusted)
    if path is not None and not path(parts.path):
        raise ValueError(messages.path)
    return urlunsplit(("https", parts.netloc, parts.path, parts.query, ""))
