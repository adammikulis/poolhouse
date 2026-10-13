"""What an address can carry out of this machine: data after ``?`` or ``#``, a long encoded name or segment, an internal host."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import unquote, urlsplit

__all__ = ["file_path", "looks_like_url", "reason"]

WEB = frozenset({"http", "https"})
ENCODED = re.compile(r"[A-Za-z0-9+/_=-]{24,}")
HEX = re.compile(r"[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64}")
LABEL = 32
URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://|^data:", re.I)


def looks_like_url(text: str) -> bool:
    """Whether ``text`` is an address with a scheme."""
    return URL.match(text.strip()) is not None and not text.strip().startswith("--")


def _internal(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not ip.is_loopback and (ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _data_in(segment: str) -> bool:
    mixed = sum(bool(re.search(p, segment)) for p in ("[a-z]", "[A-Z]", "[0-9]")) >= 2
    return bool(ENCODED.fullmatch(segment)) and mixed and not HEX.fullmatch(segment)


def file_path(url: str) -> str:
    """The path a ``file:`` address names, empty for any other address."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return ""
    return unquote(parts.path) if parts.scheme.lower() == "file" else ""


def reason(url: str) -> str:
    """What about ``url`` could carry data out or reach something internal, empty when it carries nothing."""
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return "is an address that cannot be read"
    scheme = parts.scheme.lower()
    if scheme not in WEB:
        return f"uses {scheme or 'no'} instead of http or https"
    if parts.username or parts.password:
        return "has a user name or password in it"
    if parts.query or parts.fragment:
        return "carries data after a ? or a #"
    if _internal(host):
        return f"is an internal address ({host})"
    if any(len(label) >= LABEL for label in host.split(".")):
        return "has a long encoded host name"
    if re.search(r"%(?:0[0-9a-fA-F]|1[0-9a-fA-F])", parts.path):
        return "has encoded line breaks or control characters in its path"
    if any(_data_in(unquote(segment)) for segment in parts.path.split("/")):
        return "has a long encoded string in its path"
    return ""
