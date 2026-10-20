"""Authenticated local model serving requests through the maintained hosting lifecycle."""

from __future__ import annotations

from .discovery import in_pool
from .session import parse_cookie


def route(handler, can_serve):
    if handler.path != '/ui/serving':
        return False
    ui = handler.ui
    if ui.serving is None:
        handler.send(501, {"error": "nothing on this daemon can run a model"})
        return True
    if handler.method == "GET":
        handler.send(200, {"running": [x.public() for x in ui.serving.live(force=True)],
                        "can_serve": can_serve()})
        return True
    if handler.method == "POST":
        return _start(handler, can_serve)
    if handler.method == "DELETE":
        ui.stop_serving(int(handler.body().get("port") or 0))
        handler.send(200, {"running": [x.public() for x in ui.serving.live(force=True)]})
        return True
    return False


def _start(handler, can_serve):
    ui = handler.ui
    if not can_serve():
        handler.send(501, {"error": "this install cannot run a model itself; a "
                                 "machine on your network can serve one instead"})
        return True
    req = handler.body()
    if (not isinstance(req, dict) or set(req) - {'path', 'context'}
            or not isinstance(req.get('path'), str) or not req['path']):
        handler.send(400, {'error': 'Choose an installed model by its canonical path.'})
        return True
    found = ui.models.library_model(req['path']) if ui.models else None
    if found is None:
        handler.send(404, {"error": "no such model on this machine"})
        return True
    sid = parse_cookie(handler.cookie)
    session = ui.sessions.get(sid)
    pooled = in_pool(ui.pool_key_path)
    def current():
        if session is not None:
            return ui.sessions.get(sid) is session
        return not pooled and not in_pool(ui.pool_key_path) and not ui.may_setup(
            handler.client_ip, handler.host_header, handler.header("X-Poolhouse-Setup", ""))
    try:
        context = req.get('context')
        if 'context' in req and type(context) is not int:
            raise ValueError('Context length must be a positive whole number.')
        served = ui.start_serving(found, context=context, current=current)
    except PermissionError:
        handler.send(403, {'error': 'Sign in again before starting this model.'})
        return True
    except (OSError, RuntimeError, ValueError) as exc:
        handler.send(400, {"error": str(exc)})
        return True
    handler.send(201, served.public())
    return True
