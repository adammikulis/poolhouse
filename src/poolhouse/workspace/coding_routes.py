"""Authenticated browser Coding turns exposed through the declared Fleet extension."""
from __future__ import annotations

import json
import time
from contextlib import suppress

from poolhouse import coding, harnessing, hub, roles
from poolhouse.fleet import setup_context
from poolhouse.workspace import localagent, localmodel, localroute
from poolhouse.workspace.boardroute import Request
from poolhouse.workspace.coding_turns import Manager

LIMIT = 1048576


def catalogue() -> dict:
    can_start, enforcement_error = True, ""
    try:
        harnessing.require_isolated_execution()
    except ValueError as error:
        can_start, enforcement_error = False, str(error)
    installed = hub.discover(formats=("gguf",))
    preferred = localmodel.choose(installed=installed, selection=localmodel.Selection(
        coding=True, search=False, context=0))
    preferred_path = next((str(model.path) for model in installed if model.path and preferred.name == model.name), preferred.ref)
    context = 0
    if preferred.ok:
        with suppress(OSError, ValueError):
            context = localmodel.context_for(preferred)
    return {"can_start": can_start, "enforcement_error": enforcement_error,
            "harnesses": [{"name": name, "available": bool(harnessing.binary_for(name))} for name in coding.HARNESSES],
            "roles": localagent.role_choices(), "default_role": roles.DEFAULT,
            "context": context, "default_model": preferred_path, "model_problem": preferred.problem,
            "model_hint": preferred.hint, "models": [{"name": model.name, "ref": str(model.path) if model.path else model.id}
                                            for model in installed]}


def current_catalogue(request) -> dict:
    """Read advisory readiness and one current registered folder, never a resource grant."""
    context = setup_context.bind(request)
    result = catalogue()
    project = None
    if request.ui.projects is not None:
        try:
            project = request.ui.projects.coding_project(current=context.current)
        except PermissionError:
            raise
        except (OSError, ValueError) as error:
            raise ValueError("The selected coding folder is unavailable. Choose it again.") from error
    if context.current() is not True:
        raise PermissionError("Coding session access changed. Reopen this Poolhouse window.")
    return {**result, "selected_project": {"id": project.id, "title": project.name} if project else None}


def route(request) -> bool:
    prefix = "/ui/coding/"
    if not request.path.startswith("/ui/coding/"):
        return False
    headers = {key.lower(): value for key, value in request.handler.headers.items()}
    if request.method != "GET":
        refused = localroute._post_refusal(Request(request.method, request.path, headers,
                                       request.handler.server.server_address[1], True))
        if refused:
            code, _extra, raw = refused
            request.send(code, json.loads(raw))
            return True
    if request.ui.conversations is None:
        request.send(503, {"error": "saved conversations are unavailable"})
        return True
    if not hasattr(request.ui, "coding_turns"):
        request.ui.coding_turns = Manager(request.ui.conversations)
    manager = request.ui.coding_turns
    path = request.path[len(prefix):].strip("/").split("/")
    try:
        if path == ["catalogue"] and request.method == "GET":
            request.send(200, current_catalogue(request))
        elif len(path) == 2 and path[1] == "events" and request.method == "GET":
            _events(request, manager, path[0])
        elif len(path) == 2 and path[1] == "status" and request.method == "GET":
            if path[0] not in manager.turns and manager.store.get(path[0]) is None:
                raise ValueError("no such conversation")
            request.send(200, manager.status(path[0]))
        elif len(path) == 2 and path[1] in ("start", "cancel") and request.method == "POST":
            body = _body(request)
            if path[1] == "cancel":
                if body:
                    raise ValueError("cancel takes no fields")
                request.send(200, manager.cancel(path[0]))
            else:
                if set(body) != {"message"}:
                    raise ValueError("a coding turn takes only a message")
                request.send(202, manager.start(path[0], body["message"]).view())
        else:
            request.send(404, {"error": "no such Coding route"})
    except PermissionError as error:
        request.send(403, {"error": str(error)})
    except (OSError, ValueError, RuntimeError) as error:
        request.send(400, {"error": str(error)})
    return True


def _body(request) -> dict:
    length = request.header("Content-Length", "0")
    if not length.isascii() or not length.isdigit() or len(length) > 8 or int(length) > LIMIT:
        raise ValueError("invalid or excessive request length")
    body = json.loads(request.handler.rfile.read(int(length)))
    if not isinstance(body, dict):
        raise ValueError("the request must be an object")
    return body


def _events(request, manager, cid) -> None:
    turn = manager.turns.get(cid)
    if turn is None:
        request.send(404, {"error": "no active turn for this conversation"})
        return
    after = int(request.asked("after", "0"))
    handler = request.handler
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.close_connection = True
    try:
        while True:
            with turn.changed:
                events = [event for event in turn.events if event["sequence"] > after]
                if not events:
                    if turn.state in ("completed", "cancelled", "failed"):
                        return
                    turn.changed.wait(timeout=1)
            for event in events:
                handler.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
                after = event["sequence"]
            if not events:
                handler.wfile.write(f": heartbeat {time.monotonic()}\n\n".encode())
            handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError):
        return
