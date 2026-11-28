"""The phone listener: HTTPS (TLS 1.3 under one pinned certificate) on this computer's loopback, home-network and Tailscale
addresses, answering only connections from such addresses. `GET /phone/v1/hello` says who it is, `POST /phone/v1/link` takes a
code proof; every other request is signed by a linked phone's key (docs/phone-api.md, section 3) and checked here before anything
reads it."""

from __future__ import annotations

import contextlib
import ipaddress
import json
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import psutil

from poolhouse.graph.serve import ReplyHandler
from poolhouse.phone_mailbox import PHONE_ID, Phone, Refused, Signed, Store

from ..framing import HANDSHAKE_S, Limited, LimitedServer, Malformed, read_body
from ..onboard.lan import in_tailnet
from .errors import ApiError
from .link import Linking, TooMany

__all__ = ["PORT", "Gatekeeper", "Listener", "Window", "local_addresses", "reachable"]

PORT = 7450
MOST_BODY = 64 * 1024
MOST_CHUNK_BODY = 128 * 1024
MOST_CONNECTIONS = 48
MOST_PER_ADDRESS = 12
SKEW_MS = 120_000
MOST_SIGNED_A_MINUTE = 900
MOST_UNSIGNED_A_MINUTE = 60
STREAM = "/phone/v1/stream"
NONCE_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
HOME_NETWORKS = tuple(ipaddress.ip_network(each) for each in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"))
LINK_LOCAL = tuple(ipaddress.ip_network(each) for each in ("169.254.0.0/16", "fe80::/10"))


def reachable(address: str, *, caller: bool = False) -> bool:
    """Whether ``address`` is this computer, its home network or its Tailscale network: where the listener binds and who it hears.
    A link-local ``caller`` is heard too; nothing public ever is."""
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    networks = (*HOME_NETWORKS, *LINK_LOCAL) if caller else HOME_NETWORKS
    return ip.is_loopback or in_tailnet(str(ip)) or any(ip in network for network in networks if network.version == ip.version)


def local_addresses() -> list[str]:
    """This computer's loopback, home-network and Tailscale addresses, home network first."""
    found = [info.address.split("%")[0] for each in psutil.net_if_addrs().values() for info in each
             if info.family in (socket.AF_INET, socket.AF_INET6)]
    kept = sorted({a for a in found if reachable(a)}, key=lambda a: (ipaddress.ip_address(a).is_loopback, in_tailnet(a), ":" in a, a))
    return kept or ["127.0.0.1"]


class Window:
    """At most ``most`` things a ``span`` of seconds for each key."""

    def __init__(self, most: int, span: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.most, self.span, self.clock = most, span, clock
        self._held: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def admit(self, key: str) -> bool:
        with self._lock:
            now = self.clock()
            if len(self._held) > 1000:
                self._held = {k: v for k, v in self._held.items() if v and now - v[-1] < self.span}
            kept = [t for t in self._held.get(key, []) if now - t < self.span]
            if len(kept) < self.most:
                kept.append(now)
                self._held[key] = kept
                return True
            self._held[key] = kept
            return False


class Gatekeeper:
    """Admits a signed request: a linked phone's signature over this certificate, method, path and body, made now and once."""

    def __init__(self, store: Store, pin: str, clock: Callable[[], float] = time.time) -> None:
        self.store, self.pin, self.clock = store, pin, clock
        self.since_ms = int(clock() * 1000)
        """A request signed before the listener started is refused: its nonce is not remembered."""
        self._nonces: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()
        self.rate = Window(MOST_SIGNED_A_MINUTE)

    def admit(self, headers: Any, method: str, path: str, body: bytes) -> tuple[Phone, Signed]:
        phone, nonce = headers.get("Poolhouse-Phone", ""), headers.get("Poolhouse-Nonce", "")
        stamp, signature = headers.get("Poolhouse-Time", ""), headers.get("Poolhouse-Signature", "")
        if not PHONE_ID.match(phone) or phone not in self.store.phones():
            raise ApiError(401, "unlinked", "This phone is not linked to this computer, or it was removed.")
        refused = ApiError(401, "signature", "The signature, time or nonce was refused; read the computer's time and sign again.")
        if (not 16 <= len(nonce) <= 48 or not set(nonce) <= NONCE_CHARS or not stamp.isdigit() or len(stamp) > 16
                or len(signature) > 200):
            raise refused
        now, at = int(self.clock() * 1000), int(stamp)
        if abs(now - at) > SKEW_MS or at < self.since_ms - 2000:
            raise refused
        try:
            signed = Signed(self.pin, phone, method, path, at, nonce, body.decode("utf-8"), signature)
            found = self.store.signer(signed)
        except (UnicodeDecodeError, Refused) as why:
            raise refused from why
        with self._lock:
            self._nonces = {k: v for k, v in self._nonces.items() if now - v < 2 * SKEW_MS}
            if (phone, nonce) in self._nonces:
                raise refused
            self._nonces[(phone, nonce)] = at
        if not self.rate.admit(phone):
            raise ApiError(429, "slow_down", "Too many requests from this phone; wait a minute.")
        return found, signed


@dataclass
class Reply:
    status: int
    body: bytes
    content_type: str = "application/json"
    headers: dict[str, str] = field(default_factory=dict)
    stream: Iterator[bytes] | None = None
    length: int = 0


def _reply(status: int, value: dict[str, Any]) -> Reply:
    return Reply(status, json.dumps(value).encode())


class _Handler(Limited, ReplyHandler):
    timeout = 40.0

    def setup(self) -> None:
        """TLS first and always: this listener speaks nothing else, to loopback too."""
        self.request.settimeout(HANDSHAKE_S)
        self.request = self.server.context().wrap_socket(self.request, server_side=True)
        super(Limited, self).setup()

    def finish(self) -> None:
        """Close the TLS connection now: the server only holds the plain socket it was wrapped from."""
        try:
            super().finish()
        finally:
            with contextlib.suppress(OSError):
                self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()

    def _serve(self) -> None:
        self.close_connection = True
        reply = self.answer()
        if reply.stream is None:
            self._write(reply)
            return
        self.send_response(reply.status)
        self.send_header("Content-Type", reply.content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        with contextlib.suppress(OSError, ssl.SSLError):
            for piece in reply.stream:
                self.wfile.write(piece)
                self.wfile.flush()

    def _write(self, reply: Reply) -> None:
        self.send_response(reply.status)
        self.send_header("Content-Type", reply.content_type)
        self.send_header("Content-Length", str(len(reply.body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(reply.body)

    do_GET = do_POST = do_DELETE = _serve

    def answer(self) -> Reply:
        listener: Listener = self.server.listener
        try:
            return listener.answer(self)
        except ApiError as why:
            return _reply(why.status, {"ok": False, "error": {"code": why.code, "message": why.message}})
        except Malformed as why:
            code = "too_large" if why.status == 413 else "invalid"
            return _reply(why.status, {"ok": False, "error": {"code": code, "message": why.message}})


class _Server(LimitedServer):
    """One address of the listener: connections from outside the home network are closed unread, and one address holds only so many."""

    def __init__(self, address: tuple[str, int], context: Callable[[], ssl.SSLContext], listener: Listener) -> None:
        self.address_family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
        super().__init__(address, _Handler, most=MOST_CONNECTIONS, tls=context)
        self.listener = listener

    def verify_request(self, request: Any, client_address: Any) -> bool:
        return reachable(client_address[0], caller=True) and self.listener.enter(request, client_address[0])

    def shutdown_request(self, request: Any) -> None:
        self.listener.leave(request)
        super().shutdown_request(request)


class Listener:
    """The listening addresses and what answers on them. ``signed`` answers an admitted request, ``streamed`` makes its event stream,
    ``hello`` says who this computer is."""

    def __init__(self, gate: Gatekeeper, linking: Linking, signed: Callable[[Phone, Signed], tuple[int, Any]],
                 streamed: Callable[[Phone], Iterator[bytes]], hello: Callable[[], dict[str, Any]]) -> None:
        self.gate, self.linking, self.signed, self.streamed, self.hello = gate, linking, signed, streamed, hello
        self.context: Callable[[], ssl.SSLContext] | None = None
        self.port = 0
        self._servers: dict[str, _Server] = {}
        self._lock = threading.Lock()
        self._open: dict[int, str] = {}
        self.unsigned = Window(MOST_UNSIGNED_A_MINUTE)

    # -- connections -------------------------------------------------------------
    def enter(self, request: Any, address: str) -> bool:
        with self._lock:
            if sum(1 for each in self._open.values() if each == address) >= MOST_PER_ADDRESS:
                return False
            self._open[id(request)] = address
            return True

    def leave(self, request: Any) -> None:
        with self._lock:
            self._open.pop(id(request), None)

    # -- addresses ---------------------------------------------------------------
    def listen(self, addresses: list[str], port: int, context: Callable[[], ssl.SSLContext]) -> list[str]:
        """Listen on exactly ``addresses`` (those that `reachable` allows) at ``port``; the ones listening. An address that cannot be
        bound is left out."""
        self.context, self.port = context, self.port or port
        wanted = [a for a in addresses if reachable(a)]
        for address in [a for a in self._servers if a not in wanted]:
            self._close(address)
        for address in wanted:
            if address in self._servers:
                continue
            try:
                server = _Server((address, self.port), context, self)
            except OSError:
                continue
            self.port = server.server_address[1]
            self._servers[address] = server
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True, name="phone-listener").start()
        return sorted(self._servers)

    def _close(self, address: str) -> None:
        server = self._servers.pop(address)
        server.shutdown()
        server.server_close()

    def close(self) -> None:
        for address in list(self._servers):
            self._close(address)
        self.port = 0

    @property
    def addresses(self) -> list[str]:
        return sorted(self._servers)

    # -- requests ----------------------------------------------------------------
    def answer(self, handler: Any) -> Reply:
        method, path = handler.command, handler.path
        if handler.headers.get("Transfer-Encoding"):
            raise ApiError(400, "invalid", "Send the body with its length.")
        route = path.split("?")[0]
        most = MOST_CHUNK_BODY if route.startswith("/phone/v1/model/requests/") else MOST_BODY
        if route in ("/phone/v1/hello", "/phone/v1/link"):
            source = handler.client_address[0]
            if not self.unsigned.admit(source):
                raise ApiError(429, "slow_down", "Too many requests from this address; wait a minute.")
            if route.endswith("hello") and method == "GET":
                return _reply(200, {"ok": True, "result": self.hello()})
            if method != "POST" or not route.endswith("link"):
                raise ApiError(404, "missing", "There is no such request.")
            return _reply(200, {"ok": True, "result": self._link(source, read_body(handler, 4096))})
        body = read_body(handler, most)
        phone, signed = self.gate.admit(handler.headers, method, path, body)
        if route == STREAM and method == "GET":
            return Reply(200, b"", "text/event-stream", stream=self.streamed(phone))
        status, result = self.signed(phone, signed)
        return _reply(status, {"ok": True, "result": result})

    def _link(self, source: str, body: bytes) -> dict[str, Any]:
        try:
            asked = json.loads(body)
            if not isinstance(asked, dict):
                raise ValueError
        except ValueError:
            raise ApiError(400, "invalid", "The body is a JSON object.") from None
        try:
            phone, proof = self.linking.link(source, *(asked.get(key) for key in ("name", "key", "proof", "signature")))
        except TooMany as why:
            raise ApiError(429, "slow_down", str(why)) from why
        except Refused as why:
            raise ApiError(403, "refused", str(why)) from why
        return {"phone": phone.id, "computer": self.hello()["computer"], "words": phone.words, "proof": proof}
