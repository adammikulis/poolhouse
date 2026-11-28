"""The listener both guest services run on: TLS 1.3 on one private address, nothing logged, no request echoed in an error."""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import ssl
import sys
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from poolhouse.graph.serve import ReplyHandler
from poolhouse.httpguard import allowed_address

__all__ = ["Quiet", "Server", "body", "private_address", "reply"]

logger = logging.getLogger("poolhouse.guest")
SILENCE_S = 30.0
MOST_CONNECTIONS = 32


def private_address(host: str) -> str:
    """``host`` when it is one IP address of this machine's networks (loopback, private, link-local or a tailnet's); `ValueError` for a
    name, for "every address" and for a public address."""
    found = ipaddress.ip_address(host.strip("[]"))
    if found.is_unspecified or found.is_multicast or allowed_address(str(found)):
        raise ValueError("guest access uses one private or Tailscale address, never a public one or all of them")
    return str(found)


class Quiet(ReplyHandler):
    """The shared silent handler as a guest service uses it: one request a connection, no server name, a silent client is dropped."""

    protocol_version = "HTTP/1.0"
    server_version = "poolhouse-guest"
    sys_version = ""
    timeout = SILENCE_S

    def answer(self) -> Any:
        raise NotImplementedError("a guest service answers in do_GET and do_POST")


def body(handler: BaseHTTPRequestHandler, most: int) -> bytes | None:
    """The request body when its length is declared and within ``most``; else None."""
    length = handler.headers.get("Content-Length", "")
    if handler.headers.get("Transfer-Encoding") or not length.isdigit() or len(length) > 12 or int(length) > most:
        return None
    found = handler.rfile.read(int(length))
    return found if len(found) == int(length) else None


def reply(handler: BaseHTTPRequestHandler, code: int, said: dict[str, Any]) -> None:
    raw = json.dumps(said).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(raw)


class Server(ThreadingHTTPServer):
    """A listener on ``address``. ``context`` gives the TLS settings of each new connection; None serves plain HTTP, which only a
    loopback address may. The handshake runs in the connection's own thread. A failure is logged as its kind alone."""

    daemon_threads = True

    def __init__(self, address: str, port: int, handler: type[BaseHTTPRequestHandler],
                 context: Callable[[], ssl.SSLContext] | None) -> None:
        address = private_address(address)
        if context is None and not ipaddress.ip_address(address).is_loopback:
            raise ValueError("only this computer is served without TLS")
        if ":" in address:
            self.address_family = socket.AF_INET6
        self.context = context
        self._places = threading.BoundedSemaphore(MOST_CONNECTIONS)
        super().__init__((address, port), handler)
        self.port = int(self.server_address[1])
        self._thread: threading.Thread | None = None

    def server_bind(self) -> None:
        # not `HTTPServer.server_bind`, which asks a name server what this address is called
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        self.server_name, self.server_port = str(self.server_address[0]), int(self.server_address[1])

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._places.acquire(blocking=False):   # strangers who open connections and say nothing get no more threads
            return self.shutdown_request(request)
        super().process_request(request, client_address)

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._places.release()

    def finish_request(self, request: Any, client_address: Any) -> None:
        if self.context is None:
            return super().finish_request(request, client_address)
        request.settimeout(SILENCE_S)
        with self.context().wrap_socket(request, server_side=True) as secured:
            super().finish_request(secured, client_address)

    def handle_error(self, request: Any, client_address: Any) -> None:
        # the standard handler prints the traceback, whose message may quote what was sent
        kind = sys.exc_info()[0]
        logger.info("a guest connection ended: %s", kind.__name__ if kind else "unknown")

    def start(self) -> None:
        self._thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True, name="guest")
        self._thread.start()

    def close(self) -> None:
        if self._thread is not None:
            self.shutdown()
        self.server_close()
