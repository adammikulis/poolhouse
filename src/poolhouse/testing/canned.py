"""A plumbing server that says, in every reply, that it is not a model.

For tests of ports, queues, process handling and HTTP framing, where no model behaviour is under test. Nothing
here is inferred: the model id it reports is `CANNED_MODEL_ID`, every reply body begins with `CANNED_MARKER`, every
response carries the `X-Canned-Response` header, and the JSON metadata (``/props``, ``/v1/models``, ``usage``, the
embedding, rerank and logprob bodies) carries the marker too. There is no switch that turns any of it off.
`CannedServer.unmarked` drops only the text prefix, for a test whose subject is the framing text itself (JSON the
client must parse); the header, the model id and the metadata stay, and the reply says its body is unmarked.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from poolhouse.client.counters import Speculative
from poolhouse.http import Server, json_body
from poolhouse.testing.answers import answers_in
from poolhouse.testing.fakes import (
    CHAT_TEMPLATE,
    DRAFTING,
    LLAMA_SERVER_HELP,
    _import_root,
    _interpreter,
    metrics_text,
)

__all__ = [
    "CANNED_BODY_HEADER",
    "CANNED_HEADER",
    "CANNED_HEADER_VALUE",
    "CANNED_MARKER",
    "CANNED_MODEL_ID",
    "Canned",
    "CannedServer",
    "Turn",
    "canned_binary",
    "canned_from_argv",
    "canned_server",
    "marked",
    "unmarked",
]

CANNED_MODEL_ID = "CANNED-NOT-A-MODEL"
"""The one model id this server reports, on every route that names a model."""
CANNED_MARKER = "[CANNED RESPONSE, NOT A MODEL]"
"""What every reply body begins with, and the value of ``x_canned`` in the JSON metadata."""
CANNED_HEADER = "X-Canned-Response"
CANNED_HEADER_VALUE = "not-a-model"
"""On every response this server sends, errors and streams included."""
CANNED_BODY_HEADER = "X-Canned-Body"
"""Present, with the value ``unmarked``, only on a reply sent inside `CannedServer.unmarked`."""

CHAT = "/v1/chat/completions"
_LAUNCHER_NAME = "poolhouse-test-server"
_FIELD = "x_canned"


def marked(text: str) -> str:
    """``text`` as this server writes it: the marker, a space, then the text."""
    return f"{CANNED_MARKER} {text}" if text else CANNED_MARKER


def unmarked(text: str) -> str:
    """The text a canned reply carried, without the marker; text that does not begin with it raises ValueError."""
    if not text.startswith(CANNED_MARKER):
        raise ValueError(f"not a canned reply, it does not begin with {CANNED_MARKER}: {text[:60]!r}")
    return text.removeprefix(CANNED_MARKER).removeprefix(" ")


@dataclass(frozen=True, slots=True)
class Turn:
    """One scripted reply: ``text`` pieces, then ``calls`` as ``(name, arguments text)``."""

    text: tuple[str, ...] = ()
    calls: tuple[tuple[str, str], ...] = ()
    completion_tokens: int = 0


@dataclass(frozen=True, slots=True)
class Canned:
    """What the canned server holds and says.

    ``served_path`` is the weights path ``/props`` reports as ``model_path``; it is never a model id.
    ``counted`` is the draft head's counters (None: no head, no speculative lines on ``/metrics``);
    ``metrics`` False answers 501 there. ``answer`` is the text a completion carries, or a callable given the
    request body; ``pieces`` is how a streamed one is broken up and ``gap`` the seconds between.
    """

    served_path: str = "canned.gguf"
    context: int = 32768
    slots: int = 1
    metrics: bool = True
    draft: str = ""
    spec_type: str = ""
    draft_max: int | None = None
    counted: Speculative | None = None
    answer: Any = "hello"
    pieces: tuple[str, ...] = ()
    gap: float = 0.0
    build_info: str = "b7000-cannedbuild"


class CannedServer:
    """A canned llama-server look-alike on a real loopback socket; every reply says it is not a model.

    ``requests`` is every ``(method, path, body)`` it took; ``bodies`` the chat requests' JSON; ``saved`` and
    ``restored`` the slots the manager asked it to write out or read back; ``slots`` the rows ``/slots`` answers
    with. ``refuse`` maps a path to the status it answers there instead; ``overflow`` refuses that many chat
    requests as longer than the context. ``api_key``, when set, is the bearer key every route but ``/health``
    demands. With ``turns`` the chat replies follow them, then answer ``done``. ``disconnected`` is set when a
    reader hangs up mid-stream.
    """

    def __init__(self, canned: Canned | None = None, *, turns: Sequence[Turn] | None = None,
                 port: int = 0) -> None:
        self.canned = canned if canned is not None else Canned()
        self.turns = None if turns is None else list(turns)
        self.requests: list[tuple[str, str, bytes]] = []
        self.bodies: list[dict[str, Any]] = []
        self.saved: list[tuple[int, str]] = []
        self.restored: list[tuple[int, str]] = []
        self.slots = [{"id": n, "n_ctx": self.canned.context // max(self.canned.slots, 1),
                       "is_processing": False} for n in range(self.canned.slots)]
        self.refuse: dict[str, int] = {}
        self.overflow = 0
        self.api_key = ""
        self.bare = 0
        self.unmarked_replies = 0
        self.disconnected = threading.Event()
        self._httpd = Server(("127.0.0.1", port), _routes(self))
        self.port = int(self._httpd.server_address[1])
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        """Stop serving and give the port back."""
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    @contextmanager
    def unmarked(self) -> Iterator[CannedServer]:
        """Replies sent inside the block carry no text prefix, for a test whose subject is the framing text.

        The header, the model id and the metadata stay, and each such reply adds ``X-Canned-Body: unmarked``
        and ``x_canned_body`` to say so; ``unmarked_replies`` counts them.
        """
        self.bare += 1
        try:
            yield self
        finally:
            self.bare -= 1

    def record(self, *, pid: int = 0) -> dict[str, Any]:
        """This server as a row of `poolhouse.serve.process.every_server`."""
        return {"pid": pid or os.getpid(), "port": self.port, "defunct": False,
                "model": self.canned.served_path, "binary": "llama-server",
                "draft": self.canned.draft, "spec_type": self.canned.spec_type,
                "draft_max": self.canned.draft_max, "rss": 0}

    def sent_to(self, path: str) -> list[dict[str, Any]]:
        """The bodies posted to ``path``."""
        return [json_body(raw) for method, seen, raw in self.requests
                if method == "POST" and seen.partition("?")[0] == path]

    def said(self, text: str) -> str:
        """``text`` as a reply carries it: marked, except inside `unmarked`."""
        return text if self.bare else marked(text)

    def reply(self, payload: Any, *, status: int = 200) -> tuple[int, str, bytes]:
        """``(status, content type, body)`` of a JSON answer; a mapping gets the marker beside its fields."""
        if isinstance(payload, dict):
            payload = {**payload, _FIELD: CANNED_MARKER}
            if self.bare:
                payload["x_canned_body"] = "unmarked"
        return status, "application/json", json.dumps(payload).encode()

    def refused(self, path: str) -> tuple[int, str, bytes] | None:
        """What ``overflow`` or ``refuse`` says to answer at ``path``, or None where they say nothing."""
        bare, _, _ = path.partition("?")
        if self.overflow and bare == CHAT:
            return self.reply({"error": {"code": 400, "type": "exceed_context_size_error",
                                        "message": "the request exceeds the available context "
                                                   "size, try increasing it"}}, status=400)
        status = self.refuse.get(path) or self.refuse.get(bare)
        return self.reply({"error": f"refusing {path}"}, status=status) if status else None

    def answer_to(self, body: dict[str, Any]) -> str:
        """The text a completion carries before the marker, for the request ``body``."""
        said = self.canned.answer
        return str(said(body) if callable(said) else said)

    def props(self) -> dict[str, Any]:
        """``/props``: the fields `serving_params` and `drafting_of` read, and the canned model id."""
        held = self.canned
        return {"model_path": held.served_path, "model_alias": CANNED_MODEL_ID,
                "total_slots": held.slots, "endpoint_metrics": held.metrics,
                "build_info": held.build_info, "chat_template": CHAT_TEMPLATE,
                "default_generation_settings": {"n_ctx": held.context, "model": held.served_path,
                                                "seed": 0xFFFFFFFF}}

    def get(self, path: str) -> tuple[int, str, bytes]:
        """``(status, content type, body)`` for a GET of ``path``."""
        if (said := self.refused(path)) is not None:
            return said
        bare = path.partition("?")[0]
        if bare.startswith("/health"):
            return self.reply({"status": "ok"})
        if bare.startswith("/props"):
            return self.reply(self.props())
        if bare in ("/v1/models", "/models"):
            return self.reply({"object": "list", "data": [
                {"id": CANNED_MODEL_ID, "object": "model", "owned_by": "canned", _FIELD: CANNED_MARKER}]})
        if bare.startswith("/metrics"):
            if not self.canned.metrics:
                return 501, "text/plain", b"metrics endpoint is disabled\n"
            return 200, "text/plain", f"# {CANNED_MARKER}\n{metrics_text(self.canned.counted)}".encode()
        if bare == "/slots":
            return self.reply(self.slots)
        return self.reply({"error": f"no such route: {bare}"}, status=404)

    def post(self, path: str, body: dict[str, Any]) -> tuple[int, str, bytes]:
        """``(status, content type, body)`` for a POST of ``body`` to ``path``."""
        bare, _, query = path.partition("?")
        if bare == CHAT:
            self.bodies.append(body)
        if (said := self.refused(path)) is not None:
            self.overflow = max(0, self.overflow - 1)
            return said
        parts = [one for one in bare.split("/") if one]
        if bare == CHAT:
            return self.reply(self.completion(body))
        if bare in ("/completion", "/completions"):
            return self.reply({"model": CANNED_MODEL_ID, "content": self.said(self.answer_to(body)),
                              "stopped_limit": False, "truncated": False})
        if bare == "/tokenize":
            return self.reply({"tokens": list(range(len(str(body.get("content") or "").split())))})
        if bare == "/detokenize":
            return self.reply({"content": " ".join(str(n) for n in body.get("tokens") or [])})
        if bare in ("/v1/embeddings", "/embedding", "/embeddings"):
            return self.reply(self.embeddings(body))
        if bare in ("/v1/rerank", "/rerank"):
            return self.reply({"model": CANNED_MODEL_ID, "results": self.ranking(body)})
        if len(parts) == 2 and parts[0] == "slots" and parts[1].isdigit():
            return self.slot(int(parts[1]), query, body)
        return self.reply({"error": f"no such route: {bare}"}, status=404)

    def turn(self) -> Turn | None:
        """The next scripted `Turn` for a chat request, or None where the server follows ``answer``."""
        if self.turns is None:
            return None
        return self.turns.pop(0) if self.turns else Turn(text=("done",))

    def completion(self, body: dict[str, Any]) -> dict[str, Any]:
        """The chat completion for ``body``: a scripted turn's text and calls, else the canned answer."""
        turn = self.turn()
        text = "".join(turn.text) if turn else self.answer_to(body)
        message: dict[str, Any] = {"role": "assistant", "content": self.said(text)}
        if turn and turn.calls:
            message["tool_calls"] = _calls(turn)
        choice: dict[str, Any] = {"index": 0, "message": message,
                                  "finish_reason": "tool_calls" if turn and turn.calls else "stop"}
        if body.get("logprobs") or body.get("top_logprobs"):
            choice["logprobs"] = _logprobs()
        return {"model": CANNED_MODEL_ID, "choices": [choice], "usage": _usage(turn)}

    def embeddings(self, body: dict[str, Any]) -> dict[str, Any]:
        """One constant vector per input, in the OpenAI shape; the numbers say nothing about the text."""
        wanted = body.get("input", body.get("content", ""))
        count = len(wanted) if isinstance(wanted, list) else 1
        return {"object": "list", "model": CANNED_MODEL_ID, "usage": _usage(None),
                "data": [{"object": "embedding", "index": n, "embedding": [0.1, 0.2, 0.3]}
                         for n in range(count)]}

    def ranking(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        """Each document scores by its position, in document order, cut to the ``top_n`` first when asked."""
        docs = body.get("documents") or []
        rows = [{"index": n, "relevance_score": 1.0 / (n + 1), _FIELD: CANNED_MARKER}
                for n in range(len(docs))]
        top = body.get("top_n")
        return rows[:top] if isinstance(top, int) and top > 0 else rows

    def slot(self, which: int, query: str, body: dict[str, Any]) -> tuple[int, str, bytes]:
        """The answer to a slot save or restore, which it records."""
        action = dict(one.split("=", 1) for one in query.split("&") if "=" in one).get("action")
        kept = str(body.get("filename") or "")
        if action == "save":
            self.saved.append((which, kept))
        elif action == "restore":
            self.restored.append((which, kept))
        else:
            return self.reply({"error": f"unknown action: {action}"}, status=400)
        return self.reply({"id_slot": which, "filename": kept})

    def frames(self, body: dict[str, Any]) -> list[bytes]:
        """The ``data:`` frames of a streamed chat completion: the marker first, the pieces, then a final frame."""
        turn = self.turn()
        pieces = tuple(turn.text) if turn else (self.canned.pieces or (self.answer_to(body),))
        head = () if self.bare else (f"{CANNED_MARKER} ",)
        chunks = [_chunk({"content": piece}) for piece in (*head, *pieces)]
        chunks += [_chunk({"tool_calls": [call]}) for call in (_calls(turn) if turn else [])]
        chunks.append({**_chunk({}, "tool_calls" if turn and turn.calls else "stop"),
                       "usage": _usage(turn), _FIELD: CANNED_MARKER})
        return [b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks] + [b"data: [DONE]\n\n"]


def _chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
    choice: dict[str, Any] = {"index": 0, "delta": delta}
    if finish:
        choice["finish_reason"] = finish
    return {"model": CANNED_MODEL_ID, "choices": [choice]}


def _calls(turn: Turn) -> list[dict[str, Any]]:
    return [{"index": n, "id": f"call_{n}", "type": "function",
             "function": {"name": name, "arguments": arguments}}
            for n, (name, arguments) in enumerate(turn.calls)]


def _usage(turn: Turn | None) -> dict[str, Any]:
    spent = turn.completion_tokens if turn else 0
    return {"prompt_tokens": 0, "completion_tokens": spent, "total_tokens": spent, _FIELD: CANNED_MARKER}


def _logprobs() -> dict[str, Any]:
    """A logprob block in the shape llama-server writes, holding only the marker token."""
    token = {"token": CANNED_MARKER, "logprob": 0.0}
    return {"content": [{**token, "top_logprobs": [dict(token)]}], _FIELD: CANNED_MARKER}


class _Quiet(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    log_message = lambda self, *args: None  # noqa: E731


def _routes(canned: CannedServer) -> type[BaseHTTPRequestHandler]:
    """A handler class answering ``canned``'s routes, with the marker header on every response."""

    class _H(_Quiet):

        def _unauthorised(self) -> bool:
            """Answer 401 and return True when ``canned.api_key`` is set and not presented."""
            if not canned.api_key or self.path.startswith("/health"):
                return False
            if self.headers.get("Authorization") == f"Bearer {canned.api_key}":
                return False
            self._answer(*canned.reply({"error": "invalid api key"}, status=401))
            return True

        def do_GET(self) -> None:
            canned.requests.append(("GET", self.path, b""))
            if not self._unauthorised():
                self._answer(*canned.get(self.path))

        def do_POST(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length) if length else b""
            canned.requests.append(("POST", self.path, raw))
            if self._unauthorised():
                return
            body = json_body(raw)
            if (body.get("stream") and self.path.split("?")[0] == CHAT
                    and canned.refused(self.path) is None):
                canned.bodies.append(body)
                self._stream(canned.frames(body))
                return
            self._answer(*canned.post(self.path, body))

        def _marks(self) -> None:
            self.send_header(CANNED_HEADER, CANNED_HEADER_VALUE)
            if canned.bare:
                canned.unmarked_replies += 1
                self.send_header(CANNED_BODY_HEADER, "unmarked")

        def _answer(self, status: int, kind: str, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(payload)))
            self._marks()
            self.end_headers()
            self.wfile.write(payload)

        def _stream(self, frames: Iterable[bytes]) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self._marks()
            self.end_headers()
            try:
                for frame in frames:
                    self.wfile.write(frame)
                    self.wfile.flush()
                    if canned.canned.gap:
                        time.sleep(canned.canned.gap)
            except (BrokenPipeError, ConnectionResetError):
                canned.disconnected.set()

    return _H


@contextmanager
def canned_server(canned: Canned | None = None, *, turns: Sequence[Turn] | None = None,
                  port: int = 0) -> Iterator[CannedServer]:
    """A canned server on a real socket for the block, closed at the end."""
    server = CannedServer(canned, turns=turns, port=port)
    try:
        yield server
    finally:
        server.close()


# ---------------------------------------------------------------- as a binary

def _windows_canned(where: Path, name: str) -> Path:
    """Build a Windows executable launcher for the canned program."""
    from distlib.scripts import ScriptMaker

    maker = ScriptMaker(None, str(where))
    maker.variants = {""}
    maker.executable = sys.executable
    maker.script_template = maker.script_template.replace(
        "import sys\n", f"import sys\nsys.path.insert(0, {str(_import_root())!r})\n")
    return Path(maker.make(f"{name} = poolhouse.testing.canned:_canned_entry")[0])


def _canned_entry() -> int:
    """The Windows executable's entry: serve as `canned_from_argv` does."""
    return canned_from_argv(sys.argv[1:], where=Path(sys.argv[0]).parent)


def _arg(argv: list[str], *names: str) -> str:
    """The value after the first of ``names`` on a command line, or empty."""
    for at, one in enumerate(argv[:-1]):
        if one in names:
            return argv[at + 1]
    return ""


def canned_from_argv(argv: list[str], *, where: Path) -> int:
    """Serve a `CannedServer` on the ``--port`` in ``argv`` until the process is killed.

    ``--help`` prints ``help.txt`` from ``where``, else `LLAMA_SERVER_HELP`, and returns instead. Otherwise the
    argv is written to ``argv.json`` and the canned identity to ``canned.json`` in ``where``, and the marker is
    written to stderr, which is where the lease's log gets it.
    """
    if "--help" in argv:
        stated = where / "help.txt"
        sys.stdout.write(stated.read_text() if stated.is_file() else LLAMA_SERVER_HELP)
        return 0
    (where / "argv.json").write_text(json.dumps(argv))
    (where / "canned.json").write_text(json.dumps(
        {"version": 1, "model_id": CANNED_MODEL_ID, "marker": CANNED_MARKER}))
    context, slots, draft = (_arg(argv, "--ctx-size", "-c"), _arg(argv, "--parallel", "-np"),
                             _arg(argv, "--model-draft", "-md"))
    held = Canned(served_path=_arg(argv, "--model", "-m") or "canned.gguf",
                  context=int(context) if context.isdigit() else 4096,
                  slots=int(slots) if slots.isdigit() else 1, draft=draft,
                  spec_type=_arg(argv, "--spec-type"), counted=DRAFTING if draft else None,
                  answer=answers_in(where))
    port = _arg(argv, "--port")
    CannedServer(held, port=int(port) if port.isdigit() else 8080)
    sys.stderr.write(f"{CANNED_MARKER} canned plumbing server on port {port or 8080}; nothing here is a model\n")
    sys.stderr.flush()
    threading.Event().wait()
    return 0


_LAUNCHER = """#!{python}
import os, sys
sys.path.insert(0, {root!r})
# A test process must not be discovered and counted by the installed model broker.
# CPython reads its prefix from argv[0], and a name that is not an interpreter leaves it
# with no stdlib, so the child is told where its stdlib and its packages are.
if sys.orig_argv[0] != {title!r}:
    os.environ["PYTHONHOME"] = sys.base_prefix
    os.environ["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    os.execv({interpreter!r}, [{title!r}, __file__, *sys.argv[1:]])
from pathlib import Path

from poolhouse.testing.canned import canned_from_argv

raise SystemExit(canned_from_argv(sys.argv[1:], where=Path(__file__).parent))
"""


def canned_binary(where: Path, *, name: str = _LAUNCHER_NAME, help_text: str | None = None) -> Path:
    """An executable in ``where`` that answers ``--help`` with ``help_text`` and then serves.

    Started for real by ``Popen``, it binds the ``--port`` it was given, presents to a process scan as a test
    server, answers what `CannedServer` answers for the model path, context, slots and draft head its command
    line named, and says on stderr and in ``canned.json`` that it is not a model.
    """
    if help_text is not None:
        (where / "help.txt").write_text(help_text, encoding="utf-8")
    if os.name == "nt":
        return _windows_canned(where, name)
    path = where / name
    path.write_text(_LAUNCHER.format(python=sys.executable, interpreter=_interpreter(),
                                     root=str(_import_root()), title=_LAUNCHER_NAME))
    path.chmod(0o755)
    return path
