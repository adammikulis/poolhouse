"""The egress proxy: the granted hosts of a session and no others (docs/design-isolated-launcher.md, 6.3).

A CONNECT-only HTTP proxy on a loopback port of its own, beside the model bridge. It answers only a client that presents the credential the launcher
minted for the session (`Proxy-Authorization`, carried in `HTTPS_PROXY`); every other caller gets one fixed 407 and the sentinel hears nothing. It
tunnels TLS end to end to port 443 of a host that is on the registry list (`contracts/egress-hosts.json`) and was named in the launch; every other
host, port, address literal, wildcard and plain HTTP request is refused. It resolves the name itself, refuses an address that is not on the public
internet (loopback, link-local, private, shared, documentation, multicast, reserved, and any of those wrapped in an IPv6 form), and connects to the
address it checked, so there is no second lookup to rebind. A tunnel sends as well as receives, so bytes are counted per host and per direction:
one tunnel is cut when the bytes it sent to a host pass what the registry records for one tunnel, and no tunnel of the session may send once the
bytes sent to the host pass the session's budget for it; a download costs nothing against either.
"""

from __future__ import annotations

import ipaddress
import json
import re
import secrets
import selectors
import socket
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from poolhouse.isolation import egress, wire
from poolhouse.isolation.serving import IDLE_SECONDS, Server, tell

__all__ = ["EgressProxy", "Tally", "Wiring", "blocked"]

AUTHORITY = re.compile(r"([A-Za-z0-9.*-]{1,253}):([0-9]{1,5})")
PORT = 443
USER = "poolhouse"
Resolve = Callable[..., list[tuple[object, object, object, object, tuple[object, ...]]]]
Dial = Callable[[str, int, float], socket.socket]
MESSAGES = {
    "method": "Only CONNECT to port 443 of a granted host is served.",
    "target": "The target is not a host name and port 443.",
    "host": "This coding session was not granted a route to that host.",
    "address": "The host does not resolve to an address on the public internet.",
    "bound": "The bytes sent to this host reached the bound for it.",
    "budget": "The bytes sent to this host reached the budget of this coding session for it.",
    "down": "The host did not answer.",
    "auth": "This proxy answers only the session it was made for.",
}
WRAPPED = (ipaddress.ip_network("::/96"), ipaddress.ip_network("::ffff:0:0:0/96"), ipaddress.ip_network("64:ff9b::/96"))
"""The IPv6 forms that carry an IPv4 address inside them and are no address of their own: the IPv4-compatible forms (`::a.b.c.d`), the
IPv4-translated forms (`::ffff:0:a.b.c.d`) and the NAT64 well-known prefix; each is blocked whole."""


def _dial(address: str, port: int, timeout: float) -> socket.socket:
    return socket.create_connection((address, port), timeout=timeout)


@dataclass(frozen=True, slots=True)
class Wiring:
    """How a name becomes an address and an address a connection: the two things a test replaces so that no connection leaves the machine."""

    resolve: Resolve = socket.getaddrinfo
    dial: Dial = _dial


@dataclass(slots=True)
class Tally:
    """What passed through the tunnels to one host: bytes each way, the tunnels made, the ones cut and the requests refused."""

    out: int = 0
    inbound: int = 0
    tunnels: int = 0
    cut: int = 0
    refused: int = 0


def blocked(address: str) -> bool:
    """Whether ``address`` is one a tunnel may never reach: anything that is not a public unicast address, or an IPv6 form that wraps one
    that is not (mapped, 6to4, Teredo, NAT64), and every IPv4-compatible or IPv4-translated form whole. A string that is not an address is
    blocked."""
    try:
        found = ipaddress.ip_address(address.partition("%")[0])
    except ValueError:
        return True
    inner: list[ipaddress.IPv4Address] = []
    if isinstance(found, ipaddress.IPv6Address):
        if any(found in network for network in WRAPPED):
            return True
        inner = [wrapped for wrapped in (found.ipv4_mapped, found.sixtofour) if wrapped is not None]
        if found.teredo is not None:
            inner += list(found.teredo)
    return any(blocked(str(each)) for each in inner) or not found.is_global or found.is_multicast or found.is_unspecified


class EgressProxy:
    """The proxy of one session. ``allowed`` is the list of hosts the launch granted, fixed now and never changed; each must be on the
    registry list; ``wiring`` is how names are resolved and addresses dialled. ``token`` is what the session's `HTTPS_PROXY` carries: it
    belongs to the launcher, which puts `proxy_url` into the environment of the harness and never prints it."""

    def __init__(self, allowed: Iterable[str] = (), *, registry: Mapping[str, egress.Host] | None = None, wiring: Wiring | None = None,
                 events: Callable[[str, dict[str, object]], None] | None = None, idle: float = IDLE_SECONDS) -> None:
        self.registry = dict(egress.hosts() if registry is None else registry)
        self.allowed = frozenset(allowed)
        unlisted = sorted(self.allowed - set(self.registry))
        if unlisted:
            raise ValueError(f"{len(unlisted)} host(s) are not on the registry list")
        held = wiring or Wiring()
        self.resolve, self.dial, self.events, self.idle = held.resolve, held.dial, events, idle
        self.token = secrets.token_urlsafe(32)
        self._expected = wire.basic(USER, self.token)
        self._tally = {name: Tally() for name in self.allowed}
        self._lock = threading.Lock()
        self._server = Server(socket.AF_INET, ("127.0.0.1", 0), self._connection, stopped=lambda: tell(self.events, "egress-proxy", "isolation.service_stopped"))
        self.port = 0

    def __repr__(self) -> str:
        return f"EgressProxy(port={self.port}, hosts={len(self.allowed)})"

    @property
    def proxy_url(self) -> str:
        """The value of `HTTPS_PROXY` for the harness: this port and the credential the proxy answers to."""
        return f"http://{USER}:{self.token}@127.0.0.1:{self.port}"

    def start(self) -> int:
        """Bind and serve; the port that `HTTPS_PROXY` names and the profile's one other loopback port."""
        self.port = int(self._server.start()[1])
        return self.port

    def stop(self) -> None:
        self._server.stop()

    def counts(self) -> dict[str, dict[str, int]]:
        """Per host, the bytes sent (``out``) and received (``in``), the tunnels made and cut and the requests refused: part of the
        end-of-session record, with the host names."""
        with self._lock:
            return {name: {"out": each.out, "in": each.inbound, "tunnels": each.tunnels, "cut": each.cut, "refused": each.refused}
                    for name, each in self._tally.items()}

    def _connection(self, client: socket.socket) -> None:
        client.settimeout(self.idle)
        buffer = bytearray()
        try:
            head = wire.read_head(client, buffer, tunnel=True, within=self.idle)
            if not wire.presented(head, self._expected):
                wire.answer(client, 407, json.dumps({"error": MESSAGES["auth"]}).encode(), extra=(wire.CHALLENGE,))
                return
            host = self._target(head)
            upstream = self._open(host)
        except wire.Refusal as why:
            wire.answer(client, why.status, json.dumps({"error": why.why}).encode())
            return
        with upstream:
            if wire.send(client, b"HTTP/1.1 200 Connection established\r\n\r\n"):
                self._tunnel(client, upstream, host, bytes(buffer))

    def _refuse(self, host: str, status: int, why: str) -> wire.Refusal:
        """The refusal to answer with, counted against ``host`` when it is one this session may name, and told to the sentinel."""
        with self._lock:
            if host in self._tally:
                self._tally[host].refused += 1
        tell(self.events, "egress-proxy", "isolation.probe", kind="egress", reason=why)
        return wire.Refusal(status, MESSAGES[why])

    def _target(self, head: wire.Head) -> str:
        """The granted host a CONNECT names, or a refusal: any other method, any target that is not ``host:443``, any host not granted."""
        if head.method != "CONNECT":
            raise self._refuse("", 405, "method")
        found = AUTHORITY.fullmatch(head.target)
        host = found.group(1).lower() if found else ""
        if not found or found.group(2) != str(PORT) or not egress.valid_name(host):
            raise self._refuse("", 403, "target")
        if host not in self.allowed:
            raise self._refuse("", 403, "host")
        with self._lock:
            spent = self._tally[host].out >= self.registry[host].session_bytes
        if spent:
            raise self._refuse(host, 403, "budget")
        return host

    def _open(self, host: str) -> socket.socket:
        """A connection to an address of ``host`` that was checked: the name is resolved once, and every address it gave must be public."""
        try:
            found = list(dict.fromkeys(str(info[4][0]) for info in self.resolve(host, PORT, type=socket.SOCK_STREAM)))
        except OSError:
            raise self._refuse(host, 502, "down") from None
        if not found or any(blocked(address) for address in found):
            raise self._refuse(host, 403, "address")
        for address in found:
            try:
                upstream = self.dial(address, PORT, self.idle)
            except OSError:
                continue
            upstream.settimeout(self.idle)
            with self._lock:
                self._tally[host].tunnels += 1
            return upstream
        raise self._refuse(host, 502, "down")

    def _allowance(self, host: str, wanted: int, sent: int) -> int:
        """How many of ``wanted`` bytes this tunnel may still send to ``host``: under what one tunnel may send (``sent`` so far) and under what
        the session may send over all of them; the bytes taken are counted."""
        with self._lock:
            each, listed = self._tally[host], self.registry[host]
            take = max(0, min(wanted, listed.bytes_out - sent, listed.session_bytes - each.out))
            each.out += take
            return take

    def _received(self, host: str, size: int) -> None:
        with self._lock:
            self._tally[host].inbound += size

    def _tunnel(self, client: socket.socket, upstream: socket.socket, host: str, first: bytes) -> None:
        """Copy both ways until either side ends, counting each direction; cut when the bytes this tunnel sent to ``host`` pass the bound of one
        tunnel, or the bytes of the session pass its budget."""
        sent = [0]
        if first and not self._forward(upstream, host, first, sent):
            return self._cut(host)
        with selectors.DefaultSelector() as watch:
            watch.register(client, selectors.EVENT_READ)
            watch.register(upstream, selectors.EVENT_READ)
            while True:
                try:
                    ready = watch.select(self.idle)
                except (OSError, ValueError):
                    return None
                if not ready:
                    return None
                for key, _ in ready:
                    each = key.fileobj
                    try:
                        data = each.recv(65536)
                    except OSError:
                        return None
                    if not data:
                        return None
                    if each is client:
                        if not self._forward(upstream, host, data, sent):
                            return self._cut(host)
                        continue
                    self._received(host, len(data))
                    if not wire.send(client, data):
                        return None

    def _forward(self, upstream: socket.socket, host: str, data: bytes, sent: list[int]) -> bool:
        """Send what the bounds allow of ``data`` upstream; False when a bound cut it short or the host stopped listening."""
        take = self._allowance(host, len(data), sent[0])
        sent[0] += take
        if take and not wire.send(upstream, data[:take]):
            return False
        return take == len(data)

    def _cut(self, host: str) -> None:
        with self._lock:
            self._tally[host].cut += 1
            out = self._tally[host].out
        tell(self.events, "egress-proxy", "isolation.egress_cut", host=host, out=out, bound=self.registry[host].bytes_out,
             budget=self.registry[host].session_bytes)
