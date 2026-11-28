"""A browser context that cannot reach this machine or its network: every request a page makes
is checked, no WebSocket connects, what would send around those checks is taken away, and downloads
are off."""

from __future__ import annotations

import json
import logging
import urllib.parse
from typing import Any

from poolhouse.httpguard import Limits, Refused, resolve, split

__all__ = ["INLINE", "allowed", "install"]

logger = logging.getLogger(__name__)

INLINE = frozenset({"data", "blob", "about"})
"""Schemes that carry their own content and reach nowhere."""

UNROUTED = ("Worker", "SharedWorker", "RTCPeerConnection", "webkitRTCPeerConnection", "WebTransport")
"""What a page could reach the network through without passing a route: a worker's own sockets, and direct and datagram
connections."""
_REMOVE = ("for (const name of " + json.dumps(UNROUTED) + ") "
           "Object.defineProperty(globalThis, name, {value: undefined, writable: false, configurable: false});")


def allowed(url: str, limits: Limits | None = None) -> bool:
    """Whether a page may request ``url``: an inline scheme, or http(s) to a public address."""
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme in INLINE:
        return True
    try:
        _parts, host, port = split(url)
        resolve(host, port, limits or Limits())
    except Refused:
        return False
    return True


def install(context: Any, limits: Limits | None = None) -> list[str]:
    """Route every request of ``context`` through `allowed`; returns the list that refused
    URLs are appended to. Non-http schemes are refused, a WebSocket is answered here and never
    connected, and `UNROUTED` is taken from every frame. Install before the first page, on a
    context made with service workers blocked."""
    refused: list[str] = []

    def check(route: Any) -> None:
        url = route.request.url
        if allowed(url, limits):
            route.continue_()
            return
        refused.append(url)
        logger.warning("the browser was stopped from reaching %s", urllib.parse.urlsplit(url).netloc)
        route.abort("blockedbyclient")

    def unconnected(socket: Any) -> None:
        refused.append(socket.url)   # no connect_to_server: nothing leaves the browser

    context.route("**/*", check)
    context.route_web_socket("**/*", unconnected)
    context.add_init_script(_REMOVE)
    return refused
