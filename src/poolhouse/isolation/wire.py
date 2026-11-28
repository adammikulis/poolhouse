"""A strict HTTP/1.1 request reader for the services that face content nobody vouches for: the model bridge and the egress proxy of a
confined session, and the guarded browser's proxy (`poolhouse.scrape.browserguard`).

The reader is the only parser the bytes of a request pass through. What it accepts it re-serialises itself, so a second parser behind it
never sees the bytes the session sent, and a request that two parsers would read differently (smuggling) is refused here, before
anything is read further (docs/design-isolated-launcher.md, 6.1 "Request form"). A request is one head and, at most, one body of a stated
length; one request is read per connection and the connection is closed after its answer, so a second request written behind the first
is never read at all. Every refusal is a status and a fixed sentence: nothing the session sent is echoed back.
"""

from __future__ import annotations

import base64
import hmac
import re
import socket
import time
from dataclasses import dataclass
from http import HTTPStatus

__all__ = ["CHALLENGE", "MAX_HEAD", "MAX_HEADERS", "TARGET", "Head", "Refusal", "answer", "basic", "length_of", "origin_form", "parse_head",
           "presented", "read_body", "read_head", "reason", "send"]

MAX_HEAD = 65536
"""The most bytes a request's head may have: `http.client`'s limit on one line (`_MAXLINE`), an existing constant with no recorded reason."""
MAX_HEADERS = 100
"""The most header fields a request may have: `http.client`'s limit (`_MAXHEADERS`), an existing constant with no recorded reason."""
LINE = re.compile(rb"([A-Z]{3,7}) ([!-~]+) HTTP/1\.([01])")
NAME = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
TARGET = re.compile(r"/[A-Za-z0-9\-._~!$&'()*+,;=:@/%?]*")
"""An origin-form request-target: a path, then an optional query, of the characters RFC 3986 allows there and no more."""
FORBIDDEN_ESCAPES = re.compile(r"%(?:2[eEfF]|5[cC]|[01][0-9a-fA-F]|7[fF])")
"""The escapes of `.`, `/`, `\\`, a control character and DEL: a path that hides a dot segment or a separator is refused unread."""
CONTROL = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
ONCE = frozenset({"host", "authorization", "x-api-key", "content-type", "content-length", "anthropic-version", "accept", "user-agent",
                  "proxy-authorization"})
"""The fields a second copy of which would make a request mean two things; each may occur once."""
REFUSED_FIELDS = frozenset({"upgrade", "expect", "transfer-encoding"})
"""A request with any of these is refused: an upgrade leaves HTTP, `Expect` waits for a second answer, and a body with a transfer coding
has a second way to state its length (the bridge accepts a body only by `Content-Length`)."""
CHALLENGE = ("Proxy-Authenticate", 'Basic realm="poolhouse"')
"""The field a proxy's 407 asks for its credential with."""


def basic(user: str, secret: str) -> str:
    """The `Proxy-Authorization` value that carries ``user`` and ``secret``."""
    return "Basic " + base64.b64encode(f"{user}:{secret}".encode()).decode()


def presented(head: Head, expected: str) -> bool:
    """Whether a proxy request carries exactly ``expected`` (`basic`); a caller that does not is never told which part was wrong."""
    given = head.one("proxy-authorization")
    return given is not None and hmac.compare_digest(given.strip().encode(), expected.encode())


class Refusal(Exception):
    """A request that is refused: the status to answer with, the fixed sentence that says why (never a word the session chose) and the fixed
    fields to answer with (a `Retry-After`, say)."""

    def __init__(self, status: int, why: str, extra: tuple[tuple[str, str], ...] = ()) -> None:
        super().__init__(why)
        self.status, self.why, self.extra = status, why, extra


@dataclass(frozen=True, slots=True)
class Head:
    """One request's head: the method, the request-target as sent, and the fields (names in lower case) in the order sent."""

    method: str
    target: str
    fields: tuple[tuple[str, str], ...]

    def one(self, name: str) -> str | None:
        """The value of field ``name``, or None when it is absent; a field that occurs twice is `parse_head`'s refusal, never reached."""
        found = [value for key, value in self.fields if key == name]
        return found[0] if found else None


def parse_head(raw: bytes, *, tunnel: bool = False) -> Head:
    """The head in ``raw`` (without its closing blank line), or `Refusal` 400/431 naming what is wrong with it. A ``tunnel`` head is a
    CONNECT request, which clients write as HTTP/1.0 or HTTP/1.1 and need not give a Host field; any other request is HTTP/1.1 with one."""
    if CONTROL.search(raw):
        raise Refusal(400, "a request may not hold control characters")
    lines = raw.split(b"\r\n")
    if any(b"\r" in line or b"\n" in line for line in lines):
        raise Refusal(400, "a request ends each line with a carriage return and a line feed")
    start = LINE.fullmatch(lines[0])
    if start is None or (start.group(3) != b"1" and not tunnel):
        raise Refusal(400, "the request line is not a method, a target and HTTP/1.1")
    if len(lines) - 1 > MAX_HEADERS:
        raise Refusal(431, "the request has too many header fields")
    fields: list[tuple[str, str]] = []
    for line in lines[1:]:
        if line[:1] in (b" ", b"\t"):
            raise Refusal(400, "a header field may not be folded")
        name, colon, value = line.partition(b":")
        if not colon or not NAME.fullmatch(name):
            raise Refusal(400, "a header field is not a name, a colon and a value")
        fields.append((name.decode("ascii").lower(), value.strip(b" \t").decode("latin-1")))
    names = [name for name, _ in fields]
    if any(names.count(name) > 1 for name in ONCE) or ("host" not in names and not tunnel):
        raise Refusal(400, "a header field that may occur once occurs twice, or the Host field is missing")
    if REFUSED_FIELDS.intersection(names) or any(
            name == "connection" and "upgrade" in value.lower() for name, value in fields):
        raise Refusal(400, "an upgrade, an expectation or a transfer coding is not accepted")
    return Head(start.group(1).decode("ascii"), start.group(2).decode("ascii"), tuple(fields))


def origin_form(target: str) -> tuple[str, str]:
    """``(path, query)`` of an origin-form request-target, or `Refusal` 400 for anything else: an absolute-form or authority-form target,
    a ``//``, a ``..``, a backslash or a fragment, or the escape of a dot, a slash, a backslash or a control character."""
    if not TARGET.fullmatch(target) or target.startswith("//") or "//" in target or ".." in target or FORBIDDEN_ESCAPES.search(target):
        raise Refusal(400, "the request target is not a plain path")
    path, _, query = target.partition("?")
    return path, query


def _receive(sock: socket.socket, buffer: bytearray, deadline: float | None = None) -> None:
    if deadline is not None:
        left = deadline - time.monotonic()
        if left <= 0:
            raise Refusal(408, "the request was not finished in time")
        sock.settimeout(left)
    try:
        data = sock.recv(65536)
    except TimeoutError:
        raise Refusal(408, "the request was not finished in time") from None
    if not data:
        raise Refusal(400, "the connection ended inside a request")
    buffer.extend(data)


def read_head(sock: socket.socket, buffer: bytearray, *, tunnel: bool = False, within: float | None = None) -> Head:
    """Read until the end of a head and return it parsed; what follows it stays in ``buffer``. A socket with a timeout set gives a 408, and with
    ``within`` the whole head must arrive in that many seconds (a head that drips a byte at a time is not held open); the socket's own timeout is
    then put back."""
    end = buffer.find(b"\r\n\r\n")
    deadline = None if within is None else time.monotonic() + within
    kept = sock.gettimeout()
    try:
        while end < 0:
            if len(buffer) > MAX_HEAD:
                raise Refusal(431, "the request head is too long")
            if b"\n\n" in buffer:
                raise Refusal(400, "a request ends each line with a carriage return and a line feed")
            _receive(sock, buffer, deadline)
            end = buffer.find(b"\r\n\r\n")
    finally:
        if deadline is not None:
            sock.settimeout(kept)
    if end > MAX_HEAD:
        raise Refusal(431, "the request head is too long")
    raw = bytes(buffer[:end])
    del buffer[:end + 4]
    return parse_head(raw, tunnel=tunnel)


def length_of(head: Head, most: int) -> int:
    """The body length the head states, 0 when it states none, or `Refusal` 400/413: digits only, once, and no more than ``most``."""
    stated = head.one("content-length")
    if stated is None:
        return 0
    if not re.fullmatch(r"[0-9]{1,19}", stated):
        raise Refusal(400, "the Content-Length is not a number")
    if int(stated) > most:
        raise Refusal(413, "the request body is larger than this service takes")
    return int(stated)


def read_body(sock: socket.socket, buffer: bytearray, length: int) -> bytes:
    """Exactly ``length`` bytes of body; anything the client sent beyond them is dropped, never read as another request."""
    while len(buffer) < length:
        _receive(sock, buffer)
    body = bytes(buffer[:length])
    buffer.clear()
    return body


def reason(status: int) -> str:
    """The standard phrase of ``status`` (the standard library's own table), or a plain word for a status it has none for."""
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Status"


def send(sock: socket.socket, data: bytes) -> int:
    """Write ``data`` whole and return its size, or 0 when the other end is gone."""
    try:
        sock.sendall(data)
    except OSError:
        return 0
    return len(data)


def answer(sock: socket.socket, status: int, body: bytes, *, kind: str = "application/json", extra: tuple[tuple[str, str], ...] = ()) -> int:
    """Write one complete response in a form this module fixes (status line, three fields, the body) and return its size in bytes."""
    head = [f"HTTP/1.1 {status} {reason(status)}", f"Content-Type: {kind}", f"Content-Length: {len(body)}",
            "Connection: close", *(f"{name}: {value}" for name, value in extra)]
    return send(sock, ("\r\n".join(head) + "\r\n\r\n").encode("ascii") + body)
