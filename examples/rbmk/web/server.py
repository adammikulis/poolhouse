#!/usr/bin/env python3
"""The RBMK control room app: a stdlib server on 127.0.0.1 that serves the room, streams live runs and serves recordings.

    python -m examples.rbmk [--port 8765] [--store DIR] [--wait SECONDS]
"""

from __future__ import annotations

import argparse
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import recordings
from .hub import DEFAULT_SPEED, Hub
from .jobs import Busy

STATIC = Path(__file__).resolve().parent / "static"
ROOM = Path(__file__).resolve().parents[1] / "room"
ASSETS = Path(__file__).resolve().parents[3] / "src" / "poolhouse" / "ui" / "assets"
SHARED_ASSETS = ("three.module.js", "three.core.js", "OrbitControls.js", "RoomEnvironment.js")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json", ".gz": "application/octet-stream",
         ".webp": "image/webp", ".png": "image/png"}
BODY_LIMIT = 64 << 10
UPLOAD_LIMIT = recordings.MAX_BYTES
KEEPALIVE_S = 10.0


class Refused(Exception):
    """A request the server will not carry out; ``status`` and the message go back to the page."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


def make_handler(hub: Hub, port_of: Any) -> type[BaseHTTPRequestHandler]:
    """A handler class bound to ``hub``; ``port_of()`` is the port the server listens on."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "rbmk-web"

        def log_message(self, format: str, *args: Any) -> None:
            return

        # ---- plumbing -------------------------------------------------------------------------
        def _origins(self) -> set[str]:
            port = port_of()
            return {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

        def _guard(self, post: bool) -> None:
            host = self.headers.get("Host", "")
            if host not in {o.removeprefix("http://") for o in self._origins()}:
                raise Refused(HTTPStatus.FORBIDDEN, f"host {host!r} is not this server")
            origin = self.headers.get("Origin")
            if origin is not None and origin not in self._origins():
                raise Refused(HTTPStatus.FORBIDDEN, f"origin {origin!r} may not use this server")
            if post and not self.headers.get("Content-Type", "").startswith("application/json"):
                raise Refused(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "POST bodies are application/json")

        def _send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _body(self, limit: int) -> bytes:
            size = int(self.headers.get("Content-Length") or 0)
            if size > limit:
                raise Refused(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"the body is over {limit} bytes")
            return self.rfile.read(size)

        def _arguments(self) -> dict[str, Any]:
            try:
                body = json.loads(self._body(BODY_LIMIT) or b"{}")
            except ValueError as exc:
                raise Refused(HTTPStatus.BAD_REQUEST, f"the body is not JSON: {exc}") from exc
            if not isinstance(body, dict):
                raise Refused(HTTPStatus.BAD_REQUEST, "the body must be a JSON object")
            return body

        def _dispatch(self, post: bool) -> None:
            try:
                self._guard(post)
                url = urlparse(self.path)
                (self._post if post else self._get)(url.path, {k: v[-1] for k, v in parse_qs(url.query).items()})
            except Refused as exc:
                self._json(exc.status, {"error": str(exc)})
            except Busy as exc:
                self._json(HTTPStatus.CONFLICT, {"error": str(exc)})
            except recordings.BadDocument as exc:
                self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})
            except (KeyError, FileNotFoundError) as exc:
                self._json(HTTPStatus.NOT_FOUND, {"error": f"not found: {exc.args[0] if exc.args else exc}"})
            except (ValueError, TypeError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def do_GET(self) -> None:
            self._dispatch(False)

        def do_POST(self) -> None:
            self._dispatch(True)

        # ---- routes ---------------------------------------------------------------------------
        def _get(self, path: str, query: dict[str, str]) -> None:
            parts = path.strip("/").split("/")
            if path == "/api/info":
                self._json(200, hub.info())
            elif path == "/api/recordings":
                self._json(200, recordings.listing(hub.folder))
            elif path == "/api/recording":
                doc = hub.recording(query["name"])
                self._json(200, {"label": recordings.label(doc), "doc": doc})
            elif path == "/api/summary":
                self._json(200, hub.summary())
            elif parts[:2] == ["api", "jobs"] and len(parts) == 4 and parts[3] == "events":
                resume = self.headers.get("Last-Event-ID")
                self._stream(hub.job(parts[2]), int(resume) + 1 if resume else int(query.get("after", 0)))
            elif parts[:2] == ["api", "jobs"] and len(parts) == 4 and parts[3] == "document":
                self._json(200, hub.document(parts[2], int(query.get("index", 0))))
            elif parts[:2] == ["api", "jobs"] and len(parts) == 3:
                self._json(200, hub.job(parts[2]).public())
            elif path.startswith("/assets/"):
                self._asset(path.removeprefix("/assets/"))
            elif path.startswith("/room/"):
                self._file(ROOM, path.removeprefix("/room/"), path)
            elif not path.startswith("/api/"):
                self._file(STATIC, "index.html" if path in ("/", "") else path.lstrip("/"), path)
            else:
                raise Refused(HTTPStatus.NOT_FOUND, f"no route {path}")

        def _post(self, path: str, query: dict[str, str]) -> None:
            if path == "/api/run":
                a = self._arguments()
                job = hub.start_run(str(a["scenario"]), str(a.get("controller", "model")), str(a.get("decider", "")),
                                    str(a.get("memory", "none")), float(a.get("speed", DEFAULT_SPEED)))
                self._json(200, job.public())
            elif path == "/api/stop":
                job = hub.job(str(self._arguments()["job"]))
                job.cancel.set()
                self._json(200, job.public())
            elif path == "/api/load":
                doc = hub.render(recordings.parse(self._body(UPLOAD_LIMIT)))
                self._json(200, {"label": recordings.label(doc), "doc": doc})
            else:
                raise Refused(HTTPStatus.NOT_FOUND, f"no route {path}")

        def _file(self, root: Path, name: str, path: str) -> None:
            target = (root / name).resolve()
            if root.resolve() not in target.parents or target.suffix not in TYPES or not target.is_file():
                raise Refused(HTTPStatus.NOT_FOUND, f"no page {path}")
            self._send(200, target.read_bytes(), TYPES[target.suffix])

        def _asset(self, name: str) -> None:
            if name not in SHARED_ASSETS:
                raise Refused(HTTPStatus.NOT_FOUND, f"no page /assets/{name}")
            self._file(ASSETS, name, f"/assets/{name}")

        def _stream(self, job: Any, after: int) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                while True:
                    events = job.since(after, KEEPALIVE_S)
                    for event in events:
                        self.wfile.write(f"id: {event['seq']}\ndata: {json.dumps(event)}\n\n".encode())
                    after += len(events)
                    if not events:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    if job.status != "running" and after >= len(job.events):
                        return
            except (BrokenPipeError, ConnectionResetError):
                return

    return Handler


def serve(hub: Hub, port: int = 8765) -> ThreadingHTTPServer:
    """A server for ``hub`` bound to 127.0.0.1 only; ``port`` 0 picks a free one."""
    box: dict[str, ThreadingHTTPServer] = {}
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(hub, lambda: box["s"].server_address[1]))
    server.daemon_threads = True
    box["s"] = server
    return server


def main(argv: list[str] | None = None) -> int:
    """Start the server and serve until interrupted."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--store", type=Path, default=None, help="the local run store to list and save runs in (default: the poolhouse state directory)")
    ap.add_argument("--wait", type=float, default=0.0, help="seconds a model run waits for the GPU hold (default: refuse at once)")
    args = ap.parse_args(argv)
    server = serve(Hub(args.store, args.wait), args.port)
    sys.stdout.write(f"RBMK control room: http://127.0.0.1:{server.server_address[1]}/\n")
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
    return 0

