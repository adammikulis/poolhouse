"""The model bridge: the one way a confined coding session reaches the model server (docs/design-isolated-launcher.md, 6.1).

A parent-run HTTP/1.1 service on a loopback port of its own, one per session. It allows a fixed table of methods and paths and answers
every other path 404 itself, so llama-server's slot, props, tool, metrics and adapter routes are not reachable; it reads the request with
the strict reader of `wire` and writes the request upstream itself, so the session's bytes are never the server's; it pins the model to
the leased alias; and it swaps the session's token for the server's key, which never leaves the parent. The server's own port is in no
profile. Responses pass through as bytes, never parsed for anything but their framing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import select
import socket
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from poolhouse.isolation import wire
from poolhouse.isolation.gate import check_on
from poolhouse.isolation.serving import IDLE_SECONDS, Server, tell
from poolhouse.json_objects import unique_object

__all__ = ["FORWARDED", "ROUTES", "Lease", "ModelBridge", "pin"]

ROUTES = frozenset({("POST", "/v1/messages"), ("POST", "/v1/messages/count_tokens"), ("POST", "/v1/chat/completions"),
                    ("POST", "/v1/responses"), ("GET", "/v1/models")})
"""The only methods and paths the bridge forwards (6.1). `GET /health` is answered by the bridge itself."""
FORWARDED = ("content-type", "accept", "anthropic-version", "user-agent")
"""The request fields that reach the server; every other is dropped, and the server's key is added."""
REMOVED = ("id_slot", "lora")
"""The fields of llama-server that choose a slot or an adapter, which a coding agent does not choose."""
ANSWERED = ("content-type", "content-length", "cache-control")
"""The response fields passed on; the transfer coding is passed on only as `chunked`."""
BEARER = re.compile(r"[Bb][Ee][Aa][Rr][Ee][Rr] +(\S+)")
STATUS = re.compile(rb"HTTP/1\.[01] ([1-5][0-9]{2})(?: [ -~]*)?")
JSON_TYPE = re.compile(r"application/json(?:\s*;\s*charset=utf-8)?", re.IGNORECASE)
MESSAGES = {
    "pinned": "This coding session may use only the model it was given.",
    "json": "The body is not one JSON object with each field once.",
    "type": "The body must be sent as application/json.",
    "auth": "The token is not this session's.",
    "route": "No such route.",
    "busy": "The model is answering as many requests as it has room for.",
    "down": "The model server did not answer.",
    "length": "A request with a body states its length.",
    "empty": "This route takes no body.",
}


def pin(body: bytes, alias: str) -> bytes:
    """``body`` as the server gets it: one JSON object with each field once (a duplicate would be read one way here and another there),
    its `model` set to ``alias`` when empty or absent and refused when it names another, and `id_slot` and `lora` removed."""
    def reject(constant: str) -> None:
        raise ValueError(constant)

    try:
        found = json.loads(body, object_pairs_hook=unique_object, parse_constant=reject)
    except (ValueError, RecursionError):
        raise wire.Refusal(400, MESSAGES["json"]) from None
    if not isinstance(found, dict):
        raise wire.Refusal(400, MESSAGES["json"])
    model = found.get("model", "")
    if model not in ("", alias):
        raise wire.Refusal(400, MESSAGES["pinned"])
    found["model"] = alias
    for name in REMOVED:
        found.pop(name, None)
    try:
        return json.dumps(found, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except ValueError:
        raise wire.Refusal(400, MESSAGES["json"]) from None


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Lease:
    """What the model server was leased as: the alias it answers to, the window it serves (in tokens) and the slots it has."""

    alias: str
    window: int
    slots: int


@dataclass(slots=True)
class Totals:
    """What the bridge counted, never a body: requests by route and status, the bytes answered, the routes refused."""

    requests: Counter[str]
    statuses: Counter[int]
    answered: int = 0
    refused: int = 0
    seconds: float = 0.0


class ModelBridge:
    """The bridge of one session: ``start()`` binds a loopback port and ``token`` is what the harness holds; the server's key stays here."""

    def __init__(self, upstream: str, lease: Lease, *, key: str = "", events: Callable[[str, dict[str, object]], None] | None = None,
                 idle: float = IDLE_SECONDS) -> None:
        check_on(upstream)
        parts = urlsplit(upstream)
        self._host, self._port = str(parts.hostname), int(parts.port or 80)
        self.alias, self.limit, self.idle, self.events = lease.alias, int(lease.window) * 4, idle, events
        self.token = secrets.token_urlsafe(32)
        self._key = key
        self._slots = threading.BoundedSemaphore(max(1, int(lease.slots)))
        self._totals = Totals(Counter(), Counter())
        self._lock = threading.Lock()
        self._server = Server(socket.AF_INET, ("127.0.0.1", 0), self._connection)
        self.port = 0

    def __repr__(self) -> str:
        return f"ModelBridge(port={self.port}, alias={self.alias!r})"

    def start(self) -> int:
        """Bind and serve; the port, which is the only one a profile names for the model."""
        self.port = int(self._server.start()[1])
        return self.port

    def stop(self) -> None:
        self._server.stop()

    def summary(self) -> dict[str, object]:
        """What was served, for the end-of-session record: counts by route and status, the bytes answered and the routes refused."""
        with self._lock:
            return {"requests": dict(self._totals.requests), "statuses": {str(k): v for k, v in self._totals.statuses.items()},
                    "bytes": self._totals.answered, "refused": self._totals.refused, "seconds": round(self._totals.seconds, 3)}

    def _count(self, route: str, status: int, size: int, began: float, *, refused: bool = False) -> None:
        with self._lock:
            self._totals.requests[route] += 1
            self._totals.statuses[status] += 1
            self._totals.answered += size
            self._totals.refused += int(refused)
            self._totals.seconds += time.monotonic() - began

    def _connection(self, sock: socket.socket) -> None:
        """One request, one answer, and the connection closed: a second request written behind the first is never read."""
        sock.settimeout(self.idle)
        began, route, buffer = time.monotonic(), "unread", bytearray()
        try:
            head = wire.read_head(sock, buffer)
            path, _ = wire.origin_form(head.target)
            route = path if (head.method, path) in ROUTES or (head.method, path) == ("GET", "/health") else "refused"
            status, size = self._handle(sock, buffer, head, path)
        except wire.Refusal as why:
            status, size = why.status, self._refuse(sock, why)
        self._count(route, status, size, began, refused=route == "refused")

    def _refuse(self, sock: socket.socket, why: wire.Refusal) -> int:
        body = json.dumps({"error": {"type": "poolhouse_refused", "message": why.why}}).encode()
        return wire.answer(sock, why.status, body)

    def _handle(self, sock: socket.socket, buffer: bytearray, head: wire.Head, path: str) -> tuple[int, int]:
        if head.method == "GET" and path == "/health":
            return 200, wire.answer(sock, 200, b'{"status":"ok"}')
        if (head.method, path) not in ROUTES:
            tell(self.events, "model-bridge", "isolation.probe", kind="route", digest=_digest(path))
            raise wire.Refusal(404, MESSAGES["route"])
        self._authenticate(head)
        post = head.method == "POST"
        length = wire.length_of(head, self.limit)
        if post and head.one("content-length") is None:
            raise wire.Refusal(411, MESSAGES["length"])
        if not post and length:
            raise wire.Refusal(400, MESSAGES["empty"])
        body = b""
        if post:
            if not JSON_TYPE.fullmatch((head.one("content-type") or "").strip()):
                raise wire.Refusal(400, MESSAGES["type"])
            body = pin(wire.read_body(sock, buffer, length), self.alias)
        if not self._slots.acquire(blocking=False):
            raise wire.Refusal(429, MESSAGES["busy"])
        try:
            return self._relay(sock, head, path, body)
        finally:
            self._slots.release()

    def _authenticate(self, head: wire.Head) -> None:
        """The session's own token in `Authorization` or `x-api-key`; a field that is present and wrong is refused even beside a right one."""
        given = []
        if (auth := head.one("authorization")) is not None:
            found = BEARER.fullmatch(auth.strip())
            given.append(found.group(1) if found else "")
        if (api := head.one("x-api-key")) is not None:
            given.append(api.strip())
        if not given or not all(hmac.compare_digest(each.encode(), self.token.encode()) for each in given):
            raise wire.Refusal(401, MESSAGES["auth"])

    def _relay(self, client: socket.socket, head: wire.Head, path: str, body: bytes) -> tuple[int, int]:
        """Write the request to the server in a form this module fixes, and pass its answer back as it comes."""
        named = f"[{self._host}]" if ":" in self._host else self._host
        lines = [f"{head.method} {path} HTTP/1.1", f"Host: {named}:{self._port}", "Connection: close"]
        lines += [f"{name}: {value}" for name in FORWARDED if (value := head.one(name)) is not None]
        if self._key:
            lines.append(f"Authorization: Bearer {self._key}")
        if head.method == "POST":
            lines.append(f"Content-Length: {len(body)}")
        try:
            upstream = socket.create_connection((self._host, self._port), timeout=self.idle)
        except OSError:
            raise wire.Refusal(502, MESSAGES["down"]) from None
        with upstream:
            upstream.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)
            upstream.settimeout(None)
            return self._pass(client, upstream)

    def _pass(self, client: socket.socket, upstream: socket.socket) -> tuple[int, int]:
        raw, ended = bytearray(), False
        while (end := raw.find(b"\r\n\r\n")) < 0:
            if len(raw) > wire.MAX_HEAD or not (more := upstream.recv(65536)):
                ended = True
                break
            raw.extend(more)
        if ended:
            raise wire.Refusal(502, MESSAGES["down"])
        status, fields = _answer_head(bytes(raw[:end]))
        rest = bytes(raw[end + 4:])
        sent = wire.send(client, _compose(status, fields))
        remaining = int(fields["content-length"]) if "content-length" in fields else None
        while True:
            if rest:
                if remaining is not None:
                    rest, remaining = rest[:remaining], remaining - len(rest[:remaining])
                if not wire.send(client, rest):
                    break
                sent += len(rest)
            if remaining == 0:
                break
            ready, _, _ = select.select([upstream, client], [], [])
            if client in ready and not _discard(client):
                break
            if upstream in ready:
                rest = upstream.recv(65536)
                if not rest:
                    break
        return status, sent


def _answer_head(raw: bytes) -> tuple[int, dict[str, str]]:
    """The status and the passed-on fields of the server's response head, or a 502: a response that is not one the bridge can frame."""
    lines = raw.split(b"\r\n")
    found = STATUS.fullmatch(lines[0])
    if found is None or int(found.group(1)) < 200:
        raise wire.Refusal(502, MESSAGES["down"])
    fields: dict[str, str] = {}
    for line in lines[1:]:
        name, colon, value = line.partition(b":")
        key, text = name.decode("latin-1").strip().lower(), value.decode("latin-1").strip()
        if colon and (key in ANSWERED or (key == "transfer-encoding" and text.lower() == "chunked")):
            fields[key] = text
    if "content-length" in fields and (not fields["content-length"].isdigit() or "transfer-encoding" in fields):
        raise wire.Refusal(502, MESSAGES["down"])
    return int(found.group(1)), fields


def _compose(status: int, fields: dict[str, str]) -> bytes:
    lines = [f"HTTP/1.1 {status} {wire.reason(status)}", "Connection: close"]
    lines += [f"{name.title()}: {value}" for name, value in fields.items()]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def _discard(sock: socket.socket) -> bool:
    """Read and drop what the client wrote while it was being answered; False when it has gone."""
    try:
        return bool(sock.recv(4096))
    except OSError:
        return False
