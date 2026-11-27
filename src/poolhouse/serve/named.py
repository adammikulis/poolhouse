"""The server a launcher is pointed at with ``--on``: whether this computer's key may go to it.

A key goes only to a server this computer can show is the one it started and keyed: the address is a plain ``http`` one of this computer's
loopback, the process of the lease record for the port is still that very process (its start time and command line), the key is bound to that
process, and it listens on the port at the address the requests are sent to. A server that passes is used at that address, however it was
written; one that does not is sent no key, and `Named.why` says what could not be shown.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass

from poolhouse import serverkeys
from poolhouse.listening import listens_on
from poolhouse.serve.leases import recorded_servers, same_process

__all__ = ["Named", "named"]


@dataclass(frozen=True, slots=True)
class Named:
    """The address to use for what was named, whether it is shown to be a server of this computer's, and why not when it is not. ``unreadable``
    marks text that is no address anything could be asked at; ``keyed`` a loopback port a key of this computer is bound to, where an address not
    shown to be that server is asked nothing."""

    url: str
    ours: bool
    why: str = ""
    unreadable: bool = False
    keyed: bool = False


def named(url: str) -> Named:
    """What ``url``, as the caller wrote it, is to this computer."""
    url = url.rstrip("/")
    unreadable = Named(url, False, "it is not an http address that can be asked, such as http://127.0.0.1:8080", True)
    if any(each.isspace() or not each.isprintable() for each in url):
        return unreadable
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return unreadable
    host = parts.hostname or ""
    if parts.scheme not in ("http", "https") or not host:
        return unreadable
    if host not in serverkeys.LOOPBACK:
        return Named(url, False, "it is not an address of this computer's loopback, and a server's key goes nowhere else")
    held = bool(port is not None and serverkeys.bound(port))

    def unshown(why: str) -> Named:
        return Named(url, False, why, keyed=held)

    written = f"[{host}]" if ":" in host else host
    # one spelling only: a user name, a backslash or a query is where two programs read two different hosts from one address
    if parts.scheme != "http" or port is None or parts.netloc.lower() != f"{written}:{port}" or parts.query or parts.fragment or "\\" in url:
        return unshown("it is not written as a plain http://127.0.0.1:PORT address")
    record = recorded_servers().get(port)
    if record is None:
        return unshown(f"no server record of this computer names port {port}")
    if record.get("unmanaged") or record.get("pending") or not same_process(record, strict=True):
        return unshown(f"the server this computer recorded for port {port} cannot be shown to be one it started that still runs")
    if serverkeys.bound(port) != record["pid"]:
        return unshown(f"no key this computer holds is bound to the server on port {port} (an earlier build started it, or its key was "
                       "replaced); `poolhouse-serve down` ends it, and the next launch starts one whose key this build holds")
    for address in ("127.0.0.1", "::1") if host == "localhost" else (host,):
        if listens_on(record["pid"], port, address):
            there = f"[{address}]" if ":" in address else address
            return Named(f"http://{there}:{port}{parts.path}", True)
    return unshown(f"the server this computer started does not listen at {written}:{port}, so what answers there is not it")
