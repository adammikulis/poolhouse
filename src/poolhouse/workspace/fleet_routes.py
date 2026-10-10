"""The person's Board behind the Fleet UI authorization guard, answered from the person's session on the node."""

import json
from pathlib import Path

from poolhouse.board import client, person
from poolhouse.fleet.same_page import checked
from poolhouse.workspace import boardroute


def _connect(request, root, audit):
    """Register the person's session on the project's board; it is the only thing a page can ask for here."""
    length = request.header('Content-Length', '0')
    if not length.isascii() or not length.isdigit() or len(length) > 8 or int(length) > 1024:
        raise ValueError('joining requires a bounded JSON object')
    if request.method != 'POST':
        request.send(405, {'error': 'joining requires POST'})
        return
    body = json.loads(request.handler.rfile.read(int(length)))
    if not isinstance(body, dict) or body:
        raise ValueError('joining accepts an empty JSON object')
    name = person.seat(Path(root)).name
    if audit:
        audit(name)
    request.send(200, {'connected': True, 'name': name})


def route(request, *, root=None, prefix="/ui/board/", audit=None) -> bool:
    """Answer one request of the Board page for the board of the project at ``root`` (the working directory when none);
    ``audit(name)`` is told when the person's session is registered."""
    if not request.path.startswith(prefix):
        return False
    where = Path(root) if root else Path.cwd()
    headers = {key.lower(): value for key, value in request.handler.headers.items()}
    if request.path == prefix + "connect":
        refused = checked(request.method, headers, request.handler.server.server_address[1], writes=True)
        if refused:
            request.send(403, {'error': 'the person request must come from this page'})
            return True
        try:
            _connect(request, where, audit)
        except (ValueError, UnicodeError) as error:
            request.send(400, {'error': str(error)})
        except client.Denied as error:
            request.send(403, {'error': str(error)})
        except (client.NodeError, OSError) as error:
            request.send(503, {'error': str(error)})
        return True
    raw = b""
    if request.method == "POST":
        size = headers.get("content-length", "0")
        if not size.isascii() or not size.isdigit() or len(size) > 8:
            request.send(400, {"error": "a post needs a valid Content-Length"})
            return True
        if int(size) > boardroute.POST_MAX:
            request.send(413, {"error": "the message is too large"})
            return True
        raw = request.handler.rfile.read(int(size))
    call = boardroute.Request(
        request.method, request.handler.path.replace(prefix, "/board/", 1),
        headers, request.handler.server.server_address[1], True, raw)
    status, extra, body = boardroute.respond(lambda: person.seat(where), call)
    request.handler.send_response(status)
    for key, value in extra.items():
        request.handler.send_header(key, value)
    request.handler.send_header("Content-Length", str(len(body)))
    request.handler.end_headers()
    if request.method != "HEAD":
        request.handler.wfile.write(body)
    return True
