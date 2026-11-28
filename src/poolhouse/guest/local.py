"""The guest's end: an address on her own computer that her tools use, forwarding to the host over the pinned connection.

It listens on loopback only, wants a token, and refuses a browser page (an ``Origin`` header) and any other host name, so no web
page she visits can use it. It opens nothing for the host to call: the host can only answer what she sends. Nothing is written
anywhere; the reply is passed on as it comes, and whoever reads it must treat it as untrusted.
"""

from __future__ import annotations

import contextlib
import hmac
import http.client
import json
import os
import secrets
import ssl
import urllib.parse
from http.server import BaseHTTPRequestHandler
from typing import Any

from poolhouse.files import write_json
from poolhouse.fleet import tls
from poolhouse.guest import wire
from poolhouse.guest.requests import ROUTES
from poolhouse.guest.store import Record, Store

__all__ = ["Proxy", "connect"]

ANSWER_S = 900.0
MOST_BODY = 16 << 20


def connect(store: Store, record: Record, path: str, body: bytes | None = None,
            timeout: float = ANSWER_S) -> tuple[http.client.HTTPSConnection, http.client.HTTPResponse]:
    """The host's answer to a POST of ``body`` to ``path``, or to a GET without one. Only the certificate pinned at joining is accepted, at a private address."""
    last: Exception = OSError("no address is known for that computer")
    identity = store.identity()
    for address in record.at:
        where = urllib.parse.urlsplit("//" + address)
        context = tls.certificate_context(record.cert)
        context.load_cert_chain(str(identity.certfile), str(identity.keyfile))
        connection = None
        try:
            connection = http.client.HTTPSConnection(wire.private_address(where.hostname or ""), where.port, timeout=timeout,
                                                     context=context)
            connection.connect()
            if not hmac.compare_digest(connection.sock.getpeercert(binary_form=True) or b"", record.der):
                raise ssl.SSLError("the computer at that address is not the one joined with")
            connection.request("GET" if body is None else "POST", path, body, {"Content-Type": "application/json"} if body is not None else {})
            return connection, connection.getresponse()
        except (OSError, ValueError, http.client.HTTPException) as why:
            if connection is not None:
                connection.close()
            last = why
    raise OSError(f"the host did not answer ({type(last).__name__})")


class Proxy:
    """The local address for the host of ``record_id``. ``token`` is what a tool sends as its API key."""

    def __init__(self, record_id: str, *, store: Store | None = None, port: int = 0, token: str = "") -> None:
        self.store = store or Store()
        self.record_id, self.token = record_id, token or secrets.token_urlsafe(24)
        if self._record() is None:
            raise ValueError("no confirmed host has that id")
        proxy = self

        class Handler(wire.Quiet):
            def do_GET(self) -> None:
                proxy._forward(self, "GET")

            def do_POST(self) -> None:
                proxy._forward(self, "POST")

        self.server = wire.Server("127.0.0.1", port, Handler, None)
        self.port = self.server.port
        self.url = f"http://127.0.0.1:{self.port}"
        self._marker = self.store.folder("endpoints") / str(self.port)

    def _record(self) -> Record | None:
        found = self.store.record(self.record_id)
        return found if found is not None and found.role == "host" and found.active() else None

    def start(self) -> None:
        write_json(self._marker, {"pid": os.getpid(), "host": self.record_id})   # `is_guest_endpoint` reads it
        self.server.start()

    def close(self) -> None:
        self._marker.unlink(missing_ok=True)
        self.server.close()

    def _forward(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        body = wire.body(handler, MOST_BODY) if method == "POST" else None   # read first, so a refusal arrives and not a reset
        sent = handler.headers.get("Authorization", "").removeprefix("Bearer ") or handler.headers.get("x-api-key", "")
        if (handler.headers.get("Host", "") not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}")
                or handler.headers.get("Origin") is not None
                or not hmac.compare_digest(sent.encode(), self.token.encode())):
            return wire.reply(handler, 403, {"error": "this address is for programs given its token"})
        path = urllib.parse.urlsplit(handler.path).path
        if not (path == "/v1/models" if method == "GET" else path in ROUTES):
            return wire.reply(handler, 404, {"error": "a guest connection answers model calls only"})
        if method == "POST" and body is None:
            return wire.reply(handler, 413, {"error": "the request is too large, or does not say its length"})
        if (record := self._record()) is None:
            return wire.reply(handler, 403, {"error": "this computer no longer connects to that host"})
        try:
            connection, response = connect(self.store, record, path, body)
        except OSError as why:
            return wire.reply(handler, 502, {"error": str(why)})
        try:
            handler.send_response(response.status)
            handler.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))   # no other header of theirs
            handler.send_header("Connection", "close")
            handler.end_headers()
            with contextlib.suppress(OSError, http.client.HTTPException):
                while block := response.read1(1 << 13):
                    handler.wfile.write(block)
                    handler.wfile.flush()
        finally:
            connection.close()

    def models(self) -> list[Any]:
        """What the host says it shares."""
        record = self._record()
        if record is None:
            return []
        connection, response = connect(self.store, record, "/v1/models", timeout=20)
        try:
            return list(json.loads(response.read(1 << 16)).get("data") or [])
        finally:
            connection.close()
