"""The Agents panel's routes: a read of the local agents for the page, and start and stop for the
person's browser session only.

``respond`` answers one request. A POST changes something, so it needs the person's browser
session (the ``signed_in`` the host page establishes), a loopback Host and Origin, a request the
browser marks same-origin and a JSON body; a token in a header is not read at all."""

from __future__ import annotations

import functools
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from poolhouse import coding, harnessing, roles
from poolhouse.board import person
from poolhouse.board.session import Session
from poolhouse.errors import Denied
from poolhouse.fleet.onboard.web import Call, Listener, Reply as WebReply
from poolhouse.fleet.same_page import Reply, checked, reply
from poolhouse.graph.guard import host_ok, refusal
from poolhouse.workspace import (
    boardroute,
    localagent as la,
    localeffort as le,
    localharness,
    localloop,
    localmodel,
    localprofile as lp,
    localstart as ls,
    plain,
)
from poolhouse.workspace.boardroute import COOKIE, Request, session_ok

__all__ = ["PREFIX", "respond", "serve"]

PREFIX = "/agents/"
BODY_MAX = 4096
START_KEYS = {"model": str, "name": str, "role": str, "effort": str, "max_effort": str, "profile": str, "ctx": str, "project": str, "repo": str, "harness": str, "max_output_tokens": (int, type(None)), "task_caps": dict}
STOP_WAIT_S = 10.0


def _post_refusal(req: Request) -> Reply | None:
    found = refusal("POST", dict(req.headers), req.port)
    if found:
        return reply(found[0], {"error": found[1]})
    origin = req.headers.get("origin")
    if origin is None or not (urlsplit(origin).scheme == "http" and host_ok(urlsplit(origin).netloc, req.port)):
        return reply(403, {"error": "a request from another origin"})
    if req.headers.get("sec-fetch-site", "same-origin") != "same-origin":
        return reply(403, {"error": "a request from another site"})
    if not req.signed_in:
        return reply(401, {"error": "only the person's own browser session starts or stops an agent"})
    return None


def _model_view(*, coding: bool = False) -> dict[str, Any]:
    selected = lp.CODING if coding else lp.CHAT
    pick = localmodel.choose(localmodel.AUTO, selection=localmodel.Selection(
        search=False, coding=coding, context=selected.ctx))
    return {"name": plain.line(pick.name, 80), "size_bytes": pick.size_bytes, "verdict": pick.verdict,
            "ok": pick.ok, "problem": plain.line(pick.problem, 300), "hint": plain.line(pick.hint, 200)}


def _read(base: Path, route: str) -> Any:
    if route == "list":
        return {"agents": ls.listing(base), "roles": la.role_choices(), "default_role": roles.DEFAULT, "efforts": [*le.LEVELS, le.AUTO],
                "default_effort": le.DEFAULT, "default_max_effort": le.DEFAULT_MAX, "default_max_output_tokens": None, "default_task_caps": {},
                "orders_from": list(la.DEFAULT_ORDERS_FROM), "harnesses": [localharness.OWN, *coding.HARNESSES],
                "saved": [{key: (agent.extra.get("backlog", {}).get("repo", "") if key == "repo"
                                 else asdict(localloop.caps_of(agent)) if key == "task_caps"
                                 else getattr(agent, key)) for key in START_KEYS}
                          for name in la.names(base) if (agent := la.load(base, name)) is not None]}
    if route == "model":
        return {**_model_view(), "profiles": {"chat": _model_view(), "coding": _model_view(coding=True)}}
    raise ValueError("no such route")


def _typed(body: bytes, shape: Mapping[str, Any]) -> dict[str, Any]:
    if len(body) > BODY_MAX:
        raise ValueError("the body is too large")
    data = json.loads(body or b"{}")
    if not isinstance(data, dict) or set(data) - set(shape):
        raise ValueError(f"the body holds only {', '.join(shape)}")
    for key, value in data.items():
        if not isinstance(value, shape[key]):
            raise ValueError(f"{key} is the wrong type")
    return data


def _write(base: Path, session: Callable[[], Session], route: str, body: bytes) -> tuple[int, Any]:
    if route == "start":
        data = _typed(body, START_KEYS)
        profile = data.get("profile") or "coding"
        if profile == "coding":
            harnessing.require_isolated_execution()
        role = data.get("role") or (la.PLAN_AND_GO if profile == "coding" else roles.DEFAULT)
        context = lp.parse_ctx(data.get("ctx", "")) or lp.profile(profile).ctx
        try:
            starter = person.session(data["project"]) if data.get("project") else session()
            got = ls.start(starter, ls.Ask(data.get("model") or localmodel.AUTO, data.get("name", ""),
                                      role, data.get("effort") or le.DEFAULT,
                                      data.get("max_effort") or le.DEFAULT_MAX,
                                      profile, context,
                                      data.get("project", ""), harness=data.get("harness") or "pi",
                                      repo=data.get("repo", ""), max_output_tokens=data.get('max_output_tokens'),
                                      task_caps=data.get('task_caps', {})), base=base)
        except ls.Unavailable as err:
            return 409, {"error": plain.line(err.problem, 300), "hint": plain.line(err.hint, 200)}
        return 200, {"name": got.name, "pid": got.pid, "model": plain.line(got.model, 80),
                     "role": got.role, "already": got.already}
    if route in ("pause", "resume"):
        name = _typed(body, {"name": str}).get("name", "")
        return 200, ls.pause(base, name, route == "pause")
    if route == "stop":
        name = _typed(body, {"name": str}).get("name", "")
        done = ls.stop(base, name, wait_s=STOP_WAIT_S)
        return 200, {"name": done.name, "forced": done.forced, "lease_released": done.lease_released}
    raise ValueError("no such route")


def respond(base: Path, session: Callable[[], Session], req: Request, body: bytes = b"") -> Reply:
    """The answer to one request under ``/agents/``: GET ``list`` and ``model``; POST ``start`` and ``stop``."""
    path = urlsplit(req.target).path
    if not path.startswith(PREFIX):
        return reply(404, {"error": "no such route"})
    route = path[len(PREFIX):]
    try:
        if req.method == "POST":
            bad = _post_refusal(req)
            return bad or reply(*_write(base, session, route, body))
        bad = checked(req.method, req.headers, req.port)
        return bad or reply(200, _read(base, route))
    except Denied as err:
        return reply(403, {"error": plain.line(err, 200)})
    except (ValueError, KeyError) as err:
        return reply(400, {"error": plain.line(err, 200)})
    except OSError as err:
        return reply(500, {"error": plain.line(err, 200)})



AGENTS_PAGE = """<!doctype html><meta charset="utf-8"><title>Agents</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/ui/ml-ui/ml-ui.css">
<body style="margin:0;background:var(--ml-bg);padding:16px"><ml-agents endpoint="/agents"></ml-agents>
<script type="module" src="/ui/ml-ui/ml-ui.js"></script>"""


class _Route(boardroute._Route):
    """The Board's route with the Agents routes and page added; its start and stop routes need the
    same session cookie as the Board route."""

    def __init__(self, base: Path, session: Callable[[], Session]) -> None:
        super().__init__(session)
        self.base = base

    def __call__(self, call: Call) -> WebReply:
        headers = {k.lower(): v for k, v in call.headers.items()}
        path = urlsplit(call.path).path
        if path.startswith(PREFIX):
            req = Request(call.method, call.path, headers, self.port, session_ok(headers, self.session))
            body = call.body(BODY_MAX + 1) if call.method == "POST" else b""
            status, out, blob = respond(self.base, self.person_session, req, body)
            return WebReply(status, blob, out, out.pop("Content-Type", "application/json"))
        if path == "/agents":
            return self._page(call, headers)
        return super().__call__(call)

    def _page(self, call: Call, headers: dict[str, str]) -> WebReply:
        status, out, _ = boardroute._page(Request(call.method, "/", headers, self.port))
        if status == 200 and self.opened_with_session(call):
            out["Set-Cookie"] = f"{COOKIE}={self.session}; HttpOnly; SameSite=Strict; Path=/"
        blob = AGENTS_PAGE.encode() if status == 200 else b""
        return WebReply(status, blob, out, out.pop("Content-Type", "text/html; charset=utf-8"))


def serve(base: Path, port: int = 0) -> Listener:
    """A loopback listener for the Board and Agents pages and routes; ``listener.session`` is the
    secret the person's browser presents (open ``/agents?session=...`` once)."""
    return boardroute.serve(lambda: person.session(Path.cwd()), port, routes=functools.partial(_Route, base))
