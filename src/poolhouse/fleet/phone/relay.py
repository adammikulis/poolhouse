"""The pool's side of a phone's model: an OpenAI chat request on the daemon's own model route, answered by the phone it names
(``poolhouse_model=phone:<id>``). The daemon has already checked the caller's credential. What the phone returns is passed on as a
model's text."""

from __future__ import annotations

import contextlib
import json
import time
from typing import Any

from .errors import ApiError
from .model import MOST_REQUEST, PREFIX

__all__ = ["PREFIX", "answer"]

ROUTE = "/v1/chat/completions"


def _chunk(ident: str, model: str, delta: dict[str, Any], finish: str | None = None) -> bytes:
    body = {"id": ident, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return f"data: {json.dumps(body)}\n\n".encode()


def answer(handler: Any, service: Any, route: str, model: str, body: bytes) -> bool:
    """Answer ``handler``'s request for ``model``; True once it has."""
    if service is None or route != ROUTE or handler.command != "POST":
        handler._send(404, {"error": "a phone model answers chat completions only"})
        return True
    try:
        if len(body) > MOST_REQUEST:
            raise ApiError(413, "too_large", f"A phone takes requests of at most {MOST_REQUEST} bytes.")
        asked = json.loads(body or b"{}")
        if not isinstance(asked, dict):
            raise ValueError
        work = service.models.ask(model[len(PREFIX):], asked, len(body))
    except ApiError as why:
        handler._send(why.status, {"error": why.message})
        return True
    except ValueError:
        handler._send(400, {"error": "the body is not a JSON object"})
        return True
    ident, name = f"chatcmpl-{work.id}", model
    if not asked.get("stream"):
        text = "".join(service.models.pieces(work))
        ended = work.finished or {"error": "The request ended."}
        if "error" in ended:
            handler._send(502, {"error": ended["error"]})
            return True
        handler._send(200, {"id": ident, "object": "chat.completion", "created": int(time.time()), "model": name,
                            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                         "finish_reason": ended["finish_reason"]}], "usage": ended["usage"]})
        return True
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.close_connection = True
    pieces = service.models.pieces(work)
    with contextlib.closing(pieces), contextlib.suppress(OSError):
        handler.wfile.write(_chunk(ident, name, {"role": "assistant"}))
        for piece in pieces:
            handler.wfile.write(_chunk(ident, name, {"content": piece}))
            handler.wfile.flush()
        ended = work.finished or {"error": "The request ended."}
        if "error" in ended:
            handler.wfile.write(f"data: {json.dumps({'error': {'message': ended['error']}})}\n\n".encode())
        else:
            handler.wfile.write(_chunk(ident, name, {}, ended["finish_reason"]))
        handler.wfile.write(b"data: [DONE]\n\n")
    return True
