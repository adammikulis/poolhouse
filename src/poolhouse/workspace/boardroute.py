"""The person's Board reads and posts through the local HTTP route.

``respond`` answers one request from the person's session on the node (`poolhouse.board.person`); ``serve`` runs it
on a loopback socket with the Board page and the poolhouse-ui assets. The page holds no token: the session is kept here, for
a browser that opened the page with the listener's session secret.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

from poolhouse import activity
from poolhouse.board import client
from poolhouse.board.session import Session
from poolhouse.fleet.onboard.web import Call, Listener, Reply as WebReply
from poolhouse.fleet.same_page import MAX_BODY, SAFE, Reply, checked, reply
from poolhouse.ui import assets
from poolhouse.workspace import board_page, plain

__all__ = ["COOKIE", "MAX_BODY", "PREFIX", "Request", "respond", "serve", "session_ok"]

PREFIX = "/board/"
COOKIE = "poolhouse_session"
POST_MAX = 32 * 1024
WAIT_MAX_S = 25


def _number(query: Mapping[str, list[str]], key: str, default: int, top: int) -> int:
    raw = (query.get(key) or [""])[0]
    return min(max(int(raw), 0), top) if raw.isdigit() and len(raw) < 17 else default


def _board(query: Mapping[str, list[str]]) -> str:
    name = (query.get("board") or [""])[0]
    if not plain.name_ok(name):
        raise ValueError("board is # then lowercase letters, digits, . _ -")
    return name


@dataclass(frozen=True, slots=True)
class Request:
    """One request: its method, target, lower-case-keyed headers, the port it came in on, and
    whether the host page has checked that the person is the one asking."""

    method: str
    target: str
    headers: Mapping[str, str] = field(default_factory=dict)
    port: int = 0
    signed_in: bool = True
    body: bytes = b""


def respond(session: Callable[[], Session], req: Request) -> Reply:
    """The answer to one request; ``session`` is the person's session on the board, made when the request is allowed."""
    parts = urlsplit(req.target)
    refused = checked(req.method, req.headers, req.port, parts.path in (PREFIX + "post", PREFIX + "ack"))
    if refused:
        return refused
    if not req.signed_in:
        return reply(401, {"error": "sign in first"})
    if not parts.path.startswith(PREFIX):
        return reply(404, {"error": "no such route"})
    try:
        s = session()
        query = parse_qs(parts.query, max_num_fields=8)
        route = parts.path[len(PREFIX):]
        if req.method == "POST":
            return reply(200, _ack(s, req.body) if route == "ack" else _post(s, req.body))
        if route == "file":
            return _file(s, (query.get("id") or [""])[0])
        return reply(200, _answer(s, route, query))
    except client.Denied as err:
        return reply(403, {"error": plain.line(err, 200)})
    except (ValueError, client.Invalid) as err:
        return reply(400, {"error": plain.line(err, 200)})
    except client.Quota as err:
        return reply(429, {"error": plain.line(err, 200)})
    except (client.NodeError, OSError) as err:
        return reply(503, {"error": plain.line(err, 200)})


def _file(s: Session, handle: str) -> Reply:
    """A file for the person: plain text as a download, never rendered; a binary file as its
    name, size and hash only."""
    meta, data = board_page.file(s, handle)
    if not meta["text"]:
        return reply(200, {"name": plain.line(meta["name"], 80), "size": meta["size"],
                           "type": meta["type"], "sha256": hashlib.sha256(data).hexdigest()})
    name = re.sub(r"[^A-Za-z0-9._-]", "_", str(meta["name"]))[:80] or "file"
    activity.record("board.file", subject=meta["id"], meta={"size": meta["size"]})
    return 200, {**SAFE, "Content-Type": "text/plain; charset=utf-8",
                 "Content-Disposition": f'attachment; filename="{name}.txt"'}, data


def _answer(s: Session, route: str, query: Mapping[str, list[str]]) -> Any:
    limit = _number(query, "limit", 100, 200)
    window = (limit, _number(query, "after", 0, 1 << 53), _number(query, "before", 0, 1 << 53))
    if route == "boards":
        return board_page.boards(s)
    if route == "agents":
        return board_page.agents(s)
    if route == "threads":
        found = _board(query)
        activity.record("board.view", subject=found)
        return board_page.threads(s, found, limit)
    if route == "messages":
        return board_page.read(s, _board(query), window)
    if route == "thread":
        return board_page.thread(s, _number(query, "root", 0, 1 << 53), window)
    if route == "dms":
        return board_page.conversations(s)
    if route == "dm":
        a, b = (query.get("a") or [""])[0], (query.get("b") or [""])[0]
        return board_page.dm(s, a, b, window)
    if route == "head":
        return board_page.head(s)
    if route == "wait":
        return board_page.wait(s, window[1], min(_number(query, "timeout", 20, WAIT_MAX_S), WAIT_MAX_S))
    raise ValueError("no such route")


def _post(s: Session, raw: bytes) -> dict[str, Any]:
    """The person's one write: a message to a board, thread or conversation."""
    if len(raw) > POST_MAX:
        raise ValueError("the message is too large")
    try:
        doc = json.loads(raw or b"{}")
    except ValueError as err:
        raise ValueError("the body is not JSON") from err
    if not isinstance(doc, dict) or not isinstance(doc.get("to"), str) \
            or not isinstance(doc.get("body"), str) or not doc["body"].strip():
        raise ValueError("a post needs a string `to` and a non-empty string `body`")
    if set(doc) - {"to", "body", "subject", "reply_to", "type"}:
        raise ValueError("unknown post field")
    kind, subject, reply_to = doc.get("type", "note"), doc.get("subject", ""), doc.get("reply_to", 0)
    if not isinstance(kind, str) or not isinstance(subject, str) or not isinstance(reply_to, int) \
            or isinstance(reply_to, bool):
        raise ValueError("type and subject are strings and reply_to is a message number")
    sent = board_page.post(s, doc)
    activity.record("board.post", subject=doc["to"] if doc["to"].startswith("#") else "dm", meta={"size": len(doc["body"])})
    return sent


def _ack(s: Session, raw: bytes) -> dict[str, Any]:
    if len(raw) > POST_MAX:
        raise ValueError("the request is too large")
    doc = json.loads(raw)
    if not isinstance(doc, dict) or set(doc) not in ({"board", "through"}, {"to", "through"}):
        raise ValueError("ack needs through and one board or recipient")
    if type(doc["through"]) is not int or doc["through"] <= 0:
        raise ValueError("through is a positive message number")
    if not isinstance(doc.get("board", doc.get("to")), str):
        raise ValueError("board or recipient must be text")
    return board_page.ack(s, doc)


def _page(req: Request) -> Reply:
    bad = checked(req.method, req.headers, req.port)
    if bad:
        return bad
    path = urlsplit(req.target).path
    if path in ("/", "/board"):
        return 200, {**SAFE, "Content-Type": "text/html; charset=utf-8",
                     "Content-Security-Policy": "default-src 'none'; script-src 'self' 'unsafe-inline'; "
                                                "style-src 'self' 'unsafe-inline'; connect-src 'self'"}, PAGE.encode()
    name = path.removeprefix("/ui/poolhouse-ui/")
    found = assets().get(name) if path.startswith("/ui/poolhouse-ui/") else None
    if found is None:
        return 404, SAFE, b""
    kind = "text/css" if name.endswith(".css") else "text/javascript" if name.endswith(".js") \
        else "application/json"
    return 200, {**SAFE, "Content-Type": f"{kind}; charset=utf-8"}, found.read_bytes()


def session_ok(headers: Mapping[str, str], secret: str) -> bool:
    """Whether the request's cookie holds the page server's session secret."""
    for part in headers.get("cookie", "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == COOKIE and secret and hmac.compare_digest(value.encode(), secret.encode()):
            return True
    return False


class _Route:
    """The dispatch function of a listener: the Board route, the page and the assets. The
    person's browser session is a secret made here; only a browser that opened the page with it
    holds the cookie the Board route requires."""

    def __init__(self, session: Callable[[], Session]) -> None:
        self.person_session = session
        self.port = 0
        self.session = secrets.token_urlsafe(24)

    def opened_with_session(self, call: Call) -> bool:
        """Whether the page was requested with this listener's session secret in its query."""
        given = parse_qs(urlsplit(call.path).query).get("session", [""])[0]
        return bool(given) and hmac.compare_digest(given.encode(), self.session.encode())

    def __call__(self, call: Call) -> WebReply:
        headers = {k.lower(): v for k, v in call.headers.items()}
        wanted = int(headers.get("content-length", "0") or 0) if headers.get("content-length", "0").isdigit() else 0
        body = call.body(POST_MAX) if call.method == "POST" and 0 < wanted <= POST_MAX else b""
        req = Request(call.method, call.path, headers, self.port, session_ok(headers, self.session), body)
        board = urlsplit(call.path).path.startswith(PREFIX)
        status, headers, body = respond(self.person_session, req) if board else _page(req)
        if status == 200 and not board and self.opened_with_session(call):
            headers["Set-Cookie"] = f"{COOKIE}={self.session}; HttpOnly; SameSite=Strict; Path=/"
        kind = headers.pop("Content-Type", "application/octet-stream")
        return WebReply(status, body, headers, kind)


PAGE = """<!doctype html><meta charset="utf-8"><title>Board</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/ui/poolhouse-ui/poolhouse-ui.css">
<body style="margin:0;background:var(--poolhouse-ui-bg)"><poolhouse-board endpoint="/board" style="height:100vh"></poolhouse-board>
<script type="module" src="/ui/poolhouse-ui/poolhouse-ui.js"></script>"""


def serve(session: Callable[[], Session], port: int = 0, *, routes: Callable[[Callable[[], Session]], _Route] = _Route) -> Listener:
    """A loopback listener for the Board page and its route; the caller calls ``start`` and
    ``stop``, and ``listener.session`` is the secret a browser opens the page with
    (``/?session=...``) to hold the cookie the route requires."""
    route = routes(session)
    listener = Listener(route, ("127.0.0.1", port))
    route.port = listener.port
    listener.session = route.session  # type: ignore[attr-defined]
    return listener
