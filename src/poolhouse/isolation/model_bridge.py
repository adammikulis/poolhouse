"""The model bridge: the one way a confined coding session reaches the model server (docs/design-isolated-launcher.md, 6.1).

A parent-run HTTP/1.1 service on a loopback port of its own, one per session. It allows a fixed table of methods and paths and answers
every other path 404 itself, so llama-server's slot, props, tool, metrics and adapter routes are not reachable; it reads the request with
the strict reader of `wire` and writes the request upstream itself, so the session's bytes are never the server's; it pins the model to
the leased alias; and it swaps the session's token for the server's key, which never leaves the parent. The server's own port is in no
profile. Responses pass through as bytes, never parsed for anything but their framing. No more requests are with the server than it has slots:
the others wait their turn, each for as long as its client stays, and a client that goes away leaves the line or frees its slot at once.

Each prompt, as it goes to the server, is compared with those before it (`prompt_watch`): one that rewrites history the server has read is said,
and `ModelBridge.look` tells that, whether a compaction is being asked for and, from the server's ``/slots``, how far it is in reading.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import re
import secrets
import selectors
import socket
import threading
import time
import traceback
from collections import Counter, deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from urllib.parse import urlsplit

from poolhouse import http
from poolhouse.isolation import prompt_watch, wire
from poolhouse.isolation.gate import check_on
from poolhouse.isolation.serving import IDLE_SECONDS, Server, tell
from poolhouse.json_objects import unique_object

__all__ = ["FORWARDED", "ROUTES", "Lease", "ModelBridge", "pin"]

ROUTES = frozenset({("POST", "/v1/messages"), ("POST", "/v1/messages/count_tokens"), ("POST", "/v1/chat/completions"),
                    ("POST", "/v1/responses"), ("GET", "/v1/models")})
"""The only methods and paths the bridge forwards (6.1). `GET /health` is answered by the bridge itself."""
FORWARDED = ("content-type", "accept", "anthropic-version", "user-agent")
"""The request fields that reach the server; every other is dropped, and the server's key is added."""
COMMON = ("model", "messages", "max_tokens", "stream", "temperature", "top_p", "tools", "tool_choice", "stop", "seed")
FIELDS: dict[str, tuple[str, ...]] = {
    "/v1/messages": (*COMMON, "system", "stop_sequences", "top_k", "thinking", "metadata", "chat_template_kwargs"),
    "/v1/messages/count_tokens": ("model", "messages", "system", "tools", "tool_choice", "thinking", "chat_template_kwargs"),
    "/v1/chat/completions": (*COMMON, "max_completion_tokens", "stream_options", "parallel_tool_calls", "response_format", "frequency_penalty",
                             "presence_penalty", "n", "reasoning_effort", "logprobs", "top_logprobs", "chat_template_kwargs"),
    "/v1/responses": ("model", "input", "instructions", "max_output_tokens", "stream", "temperature", "top_p", "tools", "tool_choice",
                      "parallel_tool_calls", "reasoning", "text", "chat_template_kwargs"),
}
"""The only top-level fields of a request that reach the server, by route: what the three message routes of the harnesses take. Every other field
is dropped, a slot (`id_slot`, `slot_id`), an adapter (`lora`) and the server's own cache switch (`cache_prompt`) among them, and none is added by
being unknown to a list kept here (the list is the allow-list, never a deny-list)."""
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
SWITCHES: dict[str, tuple[object, ...]] = {"enable_thinking": (True, False), "preserve_thinking": (True, False), "reasoning_effort": EFFORTS}
"""The only values of `chat_template_kwargs` that reach the server, by name: the template's thinking switches, which a harness turns thinking off
by. A template reads any name it is given, so every other is dropped."""
MEDIA = frozenset({"image_url", "image", "input_image", "input_audio", "audio", "audio_url", "file", "input_file", "video_url", "document"})
"""The content parts that carry a picture, a sound, a video or a file: each is read for a place the server could fetch from."""
LOCATIONS = ("image_url", "audio_url", "video_url", "file_url", "url")
DEPTH = 24
PROMPTS = frozenset({"/v1/messages", "/v1/chat/completions", "/v1/responses"})
"""The routes whose body holds a prompt."""
PROGRESS_EVERY_S = 10.0
"""Seconds between two reads of the server's ``/slots`` while a request is with it: the reply is made between two batches of its work."""
REWRITES = 50
"""The rewrites of one session that are kept, the newest."""
LEFT = 499
"""The status counted for a request whose client went away before it was answered; nothing is written to it."""
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
    "remote": "A picture, a sound or a file must be sent as its own bytes (a data: URL), never as an address.",
    "down": "The model server did not answer.",
    "length": "A request with a body states its length.",
    "empty": "This route takes no body.",
    "fault": "The bridge could not read this request; it is not forwarded.",
}


def _media(value: object, depth: int = 0) -> list[dict]:
    """Every content part under ``value`` that carries a picture, a sound, a video or a file (`MEDIA`), at any depth a message nests them."""
    if depth > DEPTH:
        raise wire.Refusal(400, MESSAGES["json"])
    found: list[dict] = []
    if isinstance(value, dict):
        kind = value.get("type")
        if isinstance(kind, str) and kind in MEDIA:   # a tool's schema has a property named `type` whose value is a dict, a list unhashable here
            found.append(value)
        found += [part for each in value.values() if isinstance(each, (dict, list)) for part in _media(each, depth + 1)]
    elif isinstance(value, list):
        found += [part for each in value for part in _media(each, depth + 1)]
    return found


def _remote(part: dict) -> bool:
    """Whether a media part names a place the server could fetch from (a URL that is not `data:`, a file the server holds) and not bytes it was sent."""
    for key in LOCATIONS:
        held = part.get(key)
        named = held.get("url") if isinstance(held, dict) else held
        if isinstance(named, str) and not named.startswith("data:"):
            return True
    source = part.get("source")
    return "file_id" in part or (isinstance(source, dict) and (source.get("type") in ("url", "file") or "url" in source or "file_id" in source))


def pin(body: bytes, alias: str, route: str) -> bytes:
    """``body`` as the server gets it: one JSON object with each field once (a duplicate would be read one way here and another there), no
    constant that is not a number (`NaN`, `Infinity`), only the fields ``route`` takes (`FIELDS`, an allow-list) and of the template's own only the
    `SWITCHES`, its `model` set to ``alias`` when empty or absent and refused when it names another, and no picture, sound, video or file named by
    anything but its own bytes (a URL the server fetched would be a request made from the one process that has the network)."""
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
    found = {name: value for name, value in found.items() if name in FIELDS[route]}
    found["model"] = alias
    if "chat_template_kwargs" in found:
        asked = found.pop("chat_template_kwargs")
        # the type is matched too: 1 equals True, and a template would read it as another thing
        switches = {name: value for name, value in (asked.items() if isinstance(asked, dict) else ())
                    if isinstance(value, (bool, str)) and value in SWITCHES.get(name, ())}
        if switches:
            found["chat_template_kwargs"] = switches
    if any(_remote(part) for part in _media(found)):
        raise wire.Refusal(400, MESSAGES["remote"])
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

    @classmethod
    def of(cls, alias: str, window: int, slots: int) -> Lease:
        """One leased model as the bridge pins it; the launcher makes it here and not by naming the class, so that a call of `Lease(` stays what
        the server starts gate counts: a lease the broker grants."""
        return cls(alias, window, slots)


@dataclass(slots=True)
class Totals:
    """What the bridge counted, never a body: requests by route and status, the bytes answered, the routes refused."""

    requests: Counter[str]
    statuses: Counter[int]
    answered: int = 0
    refused: int = 0
    seconds: float = 0.0


class _Turns:
    """The model server's slots, handed to requests in the order they asked for one. A request waits for its turn for as long as its client stays:
    nothing else ends the wait but the bridge's stop, and the server is never asked for more than it has slots."""

    def __init__(self, slots: int) -> None:
        self._free, self._closed = max(1, slots), False
        self._line: deque[socket.socket] = deque()
        """For each request that waits, the oldest first, the socket a byte written to ends its wait."""
        self._lock = threading.Lock()

    def take(self, client: socket.socket) -> bool:
        """Wait for a slot; False when ``client`` has gone, or the bridge has stopped, before there was one for it."""
        with self._lock:
            if self._closed:
                return False
            if self._free:
                self._free -= 1
                return True
            woken, wake = socket.socketpair()
            self._line.append(wake)
        with woken, wake:
            stayed = False
            try:
                stayed = _next(client, woken) is not None
            finally:
                given = self._leave(wake, stayed)
        return given

    def _leave(self, wake: socket.socket, stayed: bool) -> bool:
        """Take one request out of the wait; whether it goes on with a slot. One given a slot as its client went hands it on."""
        with self._lock:
            if wake in self._line:
                self._line.remove(wake)
                return False
            if self._closed:
                return False
        if not stayed:
            self.release()
        return stayed

    def release(self) -> None:
        with self._lock:
            if self._line:
                self._line.popleft().send(b"1")
            else:
                self._free += 1

    def close(self) -> None:
        """End every wait, with no slot."""
        with self._lock:
            self._closed = True
            while self._line:
                self._line.popleft().send(b"0")


class ModelBridge:
    """The bridge of one session: ``start()`` binds a loopback port and ``token`` is what the harness holds; the server's key stays here.
    ``watch`` compares the session's prompts; for a conversation that goes on from another bridge the caller sets that one's before `start`."""

    def __init__(self, upstream: str, lease: Lease, *, key: str = "", events: Callable[[str, dict[str, object]], None] | None = None,
                 idle: float = IDLE_SECONDS) -> None:
        check_on(upstream)
        parts = urlsplit(upstream)
        self._host, self._port = str(parts.hostname), int(parts.port or 80)
        self.alias, self.window, self.limit, self.idle, self.events = lease.alias, int(lease.window), int(lease.window) * 4, idle, events
        self.token = secrets.token_urlsafe(32)
        self._key = key
        self._turns = _Turns(int(lease.slots))
        self._totals = Totals(Counter(), Counter())
        self._lock = threading.Lock()
        self._busy = 0
        self.watch = prompt_watch.Watch()
        self._upstream_url = upstream.rstrip("/")
        self._served = self._compacting = self._compactions = 0
        self._rewrites: list[str] = []
        self._markers: tuple[str, ...] = ()
        """What tells the newest request that is with the server from another's in its ``/slots``; held only while it is there."""
        self._progress: prompt_watch.Progress | None = None
        self._asked_at: float | None = None
        self._upstreams: set[socket.socket] = set()
        self._stopped = False
        self._server = Server(socket.AF_INET, ("127.0.0.1", 0), self._connection,
                              stopped=lambda: tell(self.events, "model-bridge", "isolation.service_stopped"))
        self.port = 0

    def __repr__(self) -> str:
        return f"ModelBridge(port={self.port}, alias={self.alias!r})"

    def start(self) -> int:
        """Bind and serve; the port, which is the only one a profile names for the model."""
        self.port = int(self._server.start()[1])
        return self.port

    def stop(self) -> None:
        """End every wait for a slot and every request that is with the server, and stop serving."""
        self._turns.close()
        with self._lock:
            self._stopped = True
            for upstream in self._upstreams:
                with contextlib.suppress(OSError):
                    upstream.shutdown(socket.SHUT_RDWR)
        self._server.stop()

    def busy(self) -> int:
        """How many requests are with the model server now, those that wait for one of its slots among them."""
        with self._lock:
            return self._busy

    def look(self) -> prompt_watch.Look:
        """What is known now of the requests: whether all of them wait for a slot, whether one with the server asks for a compaction's summary,
        how far the server is in reading it, as last heard (the question is put again from a thread of its own, so this never waits), and every
        rewrite seen."""
        with self._lock:
            now = time.monotonic()
            if self._served and (self._asked_at is None or now - self._asked_at >= PROGRESS_EVERY_S):
                self._asked_at = now
                threading.Thread(target=self._ask_progress, daemon=True).start()
            return prompt_watch.Look(bool(self._busy) and not self._served, bool(self._compacting), self._progress if self._served else None,
                                     tuple(self._rewrites), self._compactions)

    def _ask_progress(self) -> None:
        """Keep what the server's ``/slots`` says of the request it is at work on; a server that does not say leaves it unknown."""
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        options = http.Options(headers=headers, timeout=self.idle, follow_redirects=False)   # the key goes to the server and nowhere it names
        try:
            found = http.request_json(f"{self._upstream_url}/slots", method="GET", options=options)
        except (OSError, ValueError, RuntimeError):
            return
        with self._lock:
            if self._served:
                self._progress = prompt_watch.progress(found, self._markers)

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
        except (TypeError, ValueError, KeyError, AttributeError, IndexError, RecursionError):   # a fault in reading a request: answered, never a bare reset
            traceback.print_exc()
            status, size = 500, self._refuse(sock, wire.Refusal(500, MESSAGES["fault"]))
        self._count(route, status, size, began, refused=route == "refused")

    def _refuse(self, sock: socket.socket, why: wire.Refusal) -> int:
        body = json.dumps({"error": {"type": "poolhouse_refused", "message": why.why}}).encode()
        return wire.answer(sock, why.status, body, extra=why.extra)

    def _handle(self, sock: socket.socket, buffer: bytearray, head: wire.Head, path: str) -> tuple[int, int]:
        if head.method == "GET" and path == "/health":
            return 200, wire.answer(sock, 200, b'{"status":"ok"}')
        if (head.method, path) not in ROUTES:
            tell(self.events, "model-bridge", "isolation.probe", kind="route", digest=_digest(path))
            raise wire.Refusal(404, MESSAGES["route"])
        self._authenticate(head)
        post = head.method == "POST"
        length = wire.length_of(head, self.limit)
        if not post and path == "/v1/models" and not length:
            return 200, wire.answer(sock, 200, json.dumps(self._models()).encode())
        if post and head.one("content-length") is None:
            raise wire.Refusal(411, MESSAGES["length"])
        if not post and length:
            raise wire.Refusal(400, MESSAGES["empty"])
        body = b""
        if post:
            if not JSON_TYPE.fullmatch((head.one("content-type") or "").strip()):
                raise wire.Refusal(400, MESSAGES["type"])
            body = pin(wire.read_body(sock, buffer, length), self.alias, path)
        with self._lock:
            self._busy += 1
        try:
            if not self._turns.take(sock):
                return LEFT, 0
            try:
                with self._watched(path, body):
                    return self._relay(sock, head, path, body)
            finally:
                self._turns.release()
        finally:
            with self._lock:
                self._busy -= 1

    @contextlib.contextmanager
    def _watched(self, path: str, body: bytes) -> Iterator[None]:
        """The block while the request has its slot: its prompt is compared, in the order the server is asked, and counted as being read."""
        seen = self.watch.sent(path, body) if path in PROMPTS else prompt_watch.Seen()
        with self._lock:
            self._served += 1
            self._compacting += seen.compaction
            self._compactions += seen.compaction
            self._markers = seen.markers
            if seen.rewrite:
                self._rewrites = [*self._rewrites, seen.rewrite][-REWRITES:]
        try:
            yield
        finally:
            with self._lock:
                self._served -= 1
                self._compacting -= seen.compaction
                if not self._served:
                    self._progress, self._asked_at, self._markers = None, None, ()

    def _models(self) -> dict[str, object]:
        """The one model of this session as the lease names it: its alias and its window, and nothing the server would say of itself (the path
        of its file, the account it runs under, its size)."""
        return {"object": "list", "data": [{"id": self.alias, "object": "model", "type": "model", "display_name": self.alias, "owned_by": "poolhouse",
                                             "context_window": self.window, "created": 0}], "has_more": False, "first_id": self.alias,
                "last_id": self.alias}

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
            with self._lock:
                if self._stopped:
                    return LEFT, 0
                self._upstreams.add(upstream)
            try:
                upstream.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)
                upstream.settimeout(None)
                return self._pass(client, upstream)
            finally:
                with self._lock:
                    self._upstreams.discard(upstream)

    def _pass(self, client: socket.socket, upstream: socket.socket) -> tuple[int, int]:
        """Pass the server's answer on as it comes. The client is watched from the first byte awaited, which for a long prompt is minutes: once it
        has gone the connection to the server is closed, so the server stops working for it and the slot is free."""
        raw = bytearray()
        while (end := raw.find(b"\r\n\r\n")) < 0:
            if len(raw) > wire.MAX_HEAD:
                raise wire.Refusal(502, MESSAGES["down"])
            if (more := _next(client, upstream)) is None:
                return LEFT, 0
            if not more:
                raise wire.Refusal(502, MESSAGES["down"])
            raw.extend(more)
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
            if remaining == 0 or not (rest := _next(client, upstream)):
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


def _next(client: socket.socket, source: socket.socket) -> bytes | None:
    """What ``source`` says next (empty at its end), waited for as long as ``client`` stays; None once the client has gone."""
    with selectors.DefaultSelector() as watch:
        watch.register(source, selectors.EVENT_READ)
        watch.register(client, selectors.EVENT_READ)
        while True:
            ready = {key.fileobj for key, _ in watch.select()}
            if client in ready and not _discard(client):
                return None
            if source in ready:
                return source.recv(65536)


def _discard(sock: socket.socket) -> bool:
    """Read and drop what the client wrote while it was being answered; False when it has gone."""
    try:
        return bool(sock.recv(4096))
    except OSError:
        return False
