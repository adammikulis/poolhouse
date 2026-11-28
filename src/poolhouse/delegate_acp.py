"""TRIAL, NOT WIRED: a delegate driven over the Agent Client Protocol (ACP v1) on the pinned OpenCode's ``opencode acp``, in the same confinement.

Nothing in the product imports this module. It takes the place of two things of `poolhouse.delegate_confined` for as long as `Driver.installed`
holds: what the box is started with (`Driver.renderer`) and how its pipes are read (`Driver.follow`); everything else of a delegate (the
confinement, the plugin that asks the hook, the record, the self-check, the publication) is the product's own. A computer with no sandbox is not
covered. What the trial found is in ``docs/delegate-acp-trial.md``.

``python -m poolhouse.delegate_acp start --task TEXT [...]`` is ``poolhouse-delegate start`` with this driver.

The protocol is spoken on the harness's input and output: ``initialize``, ``session/new`` or ``session/resume``, ``session/prompt``, and
``session/cancel`` on a stop. The client offers the agent no file, terminal or form method and refuses every permission it asks for, since each
would be served outside the box. What the agent says is a box's data: it is turned into the events the runner already reads.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import queue
import sys
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any

from poolhouse import delegate, delegate_confined, delegate_run, isolation, jobs, opencode

__all__ = ["Driver", "Talk", "argv_of", "preload", "run", "start", "titled"]

PROTOCOL = 1
CANCEL_WAIT_S = 5.0
"""Seconds a stop waits for the agent to answer the cancel, which carries what the turn had spent: the release's own wait for a turn to wind
down."""
INITIALIZE, SESSION, PROMPT = 1, 2, 3
"""The ids of the client's three requests."""
REASONS = {"end_turn": "stop", "max_tokens": "length", "max_turn_requests": "tool-calls", "cancelled": "cancelled", "refusal": "refusal"}
"""The runner's word for each of the protocol's stop reasons."""

SERVE = """\
PATH="${1%/*}:$PATH"
export PATH
# $2 is the Unix socket the server listens on where the box binds no address, and $3 what each OpenCode process loads to use it.
if [ -n "$2" ]; then BUN_INSPECT_PRELOAD=$3; export BUN_INSPECT_PRELOAD; fi
OPENCODE_LOG_LEVEL=WARN
export OPENCODE_LOG_LEVEL
if [ -n "$4" ]; then
  # The protocol refuses a conversation held in another folder and has no way to move one: the release's `api` command does, on a server of
  # its own, which listens on a second name in the socket's folder.
  POOLHOUSE_SOCKET_END=-move "$1" --standalone api POST "/api/session/$4/move" --data "$5" </dev/null >&2 ||
    echo "poolhouse: the conversation could not be moved to this copy of the project" >&2
fi
# The server's warnings and errors (a plugin it could not load among them) go to the run's error output.
exec "$1" --print-logs acp
"""

THROUGH = r'''import http from "node:http";
if (process.argv[2] === "serve") delete process.env.BUN_INSPECT_PRELOAD;   // the server's tools load nothing
const url = `http://127.0.0.1:${port}/`;
const listen = http.Server.prototype.listen;
let taken = false;
http.Server.prototype.listen = function (options, ...rest) {
  if (taken || options?.port !== 0) return listen.call(this, options, ...rest);
  taken = true;
  this.address = () => ({address: "127.0.0.1", family: "IPv4", port});
  return listen.call(this, {path: socket}, ...rest);
};
const reach = globalThis.fetch;
globalThis.fetch = Object.assign((input, init) => {
  const to = String(input?.url ?? input);
  return reach(input, to.startsWith(url) ? {...init, unix: socket} : init);
}, reach);
'''
HOOKED = 'await ctx.session.hook("context", context);'


def preload(socket: str) -> str:
    """`opencode.through_socket` for the commands that start a private server on any free port: the server's first such listen is on the Unix
    socket ``socket``, ending in ``POOLHOUSE_SOCKET_END`` where that is set."""
    return f'const socket = {json.dumps(socket)} + (process.env.POOLHOUSE_SOCKET_END || "");\nconst port = {opencode.SERVER_PORT};\n' + THROUGH


def titled(plugin: str) -> str:
    """``plugin`` that also gives the conversation its title, so no request to the model asks for one."""
    return plugin.replace(HOOKED, HOOKED + '\n    await ctx.session.hook("title", (event) => { event.result = "delegate"; });')


def argv_of(binary: str, through: tuple[str, str], session: str, directory: str) -> tuple[str, ...]:
    """The command line of one run: the script `SERVE` with the binary, the socket and its preload, the conversation taken up (empty for none, or
    for a word that is no id: it goes into the server's path) and where it is moved to."""
    taken = session if opencode.SESSION_ID.fullmatch(session) else ""
    return ("/bin/sh", "-c", SERVE, "opencode", binary, *through, taken, json.dumps({"directory": directory}))


class Talk:
    """One conversation with the agent: ``send`` writes a message to it, ``close`` ends its input, ``stream`` takes what it says as the runner's
    events, ``folder`` is where it works and ``session`` the conversation it takes up, empty for a new one."""

    def __init__(self, send: Callable[[dict[str, Any]], None], close: Callable[[], None], stream: delegate_confined.Stream, folder: str,
                 session: str) -> None:
        self.send, self.close, self.stream, self.folder, self.session = send, close, stream, folder, session
        self.task = ""
        self.agent: dict[str, Any] = {}
        """What the agent said of itself when asked what it can do."""
        self.over = False
        self._text: tuple[str, list[str]] = ("", [])
        """The text of the newest reply that has not been handed on: its id and its pieces."""
        self._shown = ""
        self._messages: set[str] = set()
        self._tools: dict[str, dict[str, Any]] = {}

    def start(self, task: str) -> None:
        self.task = task
        self._ask(INITIALIZE, "initialize", {"protocolVersion": PROTOCOL, "clientCapabilities": {},
                                             "clientInfo": {"name": "poolhouse", "version": "trial"}})

    def cancel(self) -> None:
        if self.stream.session and not self.over:
            self.send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": self.stream.session}})

    def _ask(self, number: int, method: str, params: Mapping[str, Any]) -> None:
        self.send({"jsonrpc": "2.0", "id": number, "method": method, "params": dict(params)})

    def line(self, raw: bytes) -> bool:
        """Take in one line of the agent's output; whether it moved the turns, the last tool or the text."""
        try:
            said = json.loads(raw)
        except ValueError:
            return False
        if not isinstance(said, dict):
            return False
        method = said.get("method")
        if isinstance(method, str):
            params = said.get("params") if isinstance(said.get("params"), dict) else {}
            if "id" in said:
                self._refuse(said["id"], method, params)
                return False
            update = params.get("update")
            return method == "session/update" and isinstance(update, dict) and self._update(update)
        if said.get("id") in (INITIALIZE, SESSION, PROMPT) and not self.over:
            return self._answered(said["id"], said)
        return False

    def _refuse(self, number: object, method: str, params: Mapping[str, Any]) -> None:
        """Answer a request of the agent: no permission, and no method of the client's."""
        if method != "session/request_permission":
            self.send({"jsonrpc": "2.0", "id": number, "error": {"code": -32601, "message": "the client has no such method"}})
            return
        options = params.get("options") if isinstance(params.get("options"), list) else []
        rejects = [each.get("optionId") for each in options if isinstance(each, dict) and each.get("kind") == "reject_once"]
        outcome = {"outcome": "selected", "optionId": rejects[0]} if rejects else {"outcome": "cancelled"}
        self.send({"jsonrpc": "2.0", "id": number, "result": {"outcome": outcome}})

    def _answered(self, number: int, said: Mapping[str, Any]) -> bool:
        result = said.get("result")
        if not isinstance(result, dict):
            error = said.get("error") if isinstance(said.get("error"), dict) else {}
            data = error.get("data") if isinstance(error.get("data"), dict) else {}
            return self._end({"type": "error", "error": {"type": data.get("errorName") or "acp", "message": error.get("message")}})
        if number == INITIALIZE:
            self.agent = result
            if result.get("protocolVersion") != PROTOCOL:
                return self._end({"type": "error", "error": {"type": "acp", "message": "the agent speaks another protocol version"}})
            taken = {"sessionId": self.session} if self.session else {}
            self._ask(SESSION, "session/resume" if self.session else "session/new", {**taken, "cwd": self.folder, "mcpServers": []})
            return False
        if number == SESSION:
            self.stream.session = self.session or str(result.get("sessionId") or "")
            self._ask(PROMPT, "session/prompt", {"sessionId": self.stream.session, "prompt": [{"type": "text", "text": self.task}]})
            return False
        self._spent(result)
        return self._end(None)

    def _update(self, update: Mapping[str, Any]) -> bool:
        kind = update.get("sessionUpdate")
        if kind in ("agent_message_chunk", "agent_thought_chunk"):
            message = str(update.get("messageId") or "").split(":")[0]
            self._counted(message)
            content = update.get("content") if isinstance(update.get("content"), dict) else {}
            if kind == "agent_thought_chunk" or not isinstance(content.get("text"), str):
                return False
            if message != self._text[0]:
                self._flush()
                self._text = (message, [])
            self._text[1].append(content["text"])
            return True
        if kind not in ("tool_call", "tool_call_update"):
            return False
        self._flush()
        call = str(update.get("toolCallId") or "")
        held = self._tools.setdefault(call, {"tool": "", "input": {}})
        if isinstance(update.get("name"), str):
            held["tool"] = update["name"]
        if isinstance(update.get("rawInput"), dict) and update["rawInput"]:
            held["input"] = update["rawInput"]
        if update.get("status") not in ("completed", "failed"):
            return False
        self._tools.pop(call)
        self.stream.finished = max(self.stream.finished, 1)   # a call has ended: the plugin must have reported by now
        return self.stream.line(json.dumps({"type": "tool_use", "part": {"id": call, "tool": held["tool"],
                                                                        "state": _state(update, held["input"])}}).encode())

    def _counted(self, message: str) -> None:
        """The turns, as far as the protocol tells them: the replies that have carried text or reasoning."""
        if message:
            self._messages.add(message)
            self.stream.turns = max(self.stream.turns, len(self._messages))

    def _flush(self) -> None:
        message, pieces = self._text
        if not pieces:
            return
        if message != self._shown:
            self.stream.last, self._shown = [], message
        self._text = (message, [])
        self.stream.line(json.dumps({"type": "text", "part": {"text": "".join(pieces)}}).encode())

    def _spent(self, result: Mapping[str, Any]) -> None:
        """What the whole turn spent, and how it ended: the only usage the protocol reports."""
        used = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        totals = {key: used.get(name) for key, name in (("input", "inputTokens"), ("output", "outputTokens"), ("reasoning", "thoughtTokens"),
                                                         ("read", "cachedReadTokens"), ("write", "cachedWriteTokens"))}
        totals = {key: value if isinstance(value, int) else 0 for key, value in totals.items()}
        for key, value in totals.items():
            self.stream.tokens[key] += value
        self.stream.reason = REASONS.get(str(result.get("stopReason")), str(result.get("stopReason")))
        self.stream.finished = max(self.stream.finished, self.stream.turns, 1)
        if self.stream.record is not None:
            self.stream.record.recorder.spend(input_tokens=totals["input"], output_tokens=totals["output"] + totals["reasoning"],
                                              cache_read_tokens=totals["read"])

    def _end(self, row: Mapping[str, Any] | None) -> bool:
        self._flush()
        if row is not None:
            self.stream.line(json.dumps(row).encode())
        self.over = True
        self.close()
        return True


def _state(update: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    """The end of a tool call as ``run --format json`` words it."""
    raw = update.get("rawOutput") if isinstance(update.get("rawOutput"), dict) else {}
    texts = [each["content"]["text"] for each in update.get("content") or [] if isinstance(each, dict) and isinstance(each.get("content"), dict)
             and isinstance(each["content"].get("text"), str)]
    state: dict[str, Any] = {"status": "completed", "input": dict(arguments), "metadata": raw.get("metadata")}
    if update.get("status") == "failed":
        state.update(status="error", error=str(raw.get("error") or ""))
        if texts and texts[-1] == state["error"]:
            texts = texts[:-1]
    state["output"] = "\n".join(texts)
    return state


class _Pipes:
    """The harness's pipes as `delegate_confined.follow` takes them, with the conversation in between: what it writes as the task starts the
    conversation, the input stays open until the conversation ends, and the output is passed on as it is read and kept for a stop."""

    def __init__(self, process: Any, stream: delegate_confined.Stream, folder: str, session: str) -> None:
        self._process = process
        self._out: queue.Queue[bytes] = queue.Queue()
        self.talk = Talk(lambda message: self._out.put(json.dumps(message).encode() + b"\n"), lambda: self._out.put(b""), stream, folder, session)
        self.lines: list[bytes] = []
        self.taken = 0
        self.answered = threading.Event()
        read, self._pass = os.pipe()
        self.stdout = os.fdopen(read, "rb", buffering=0)
        self.stdin = self
        threading.Thread(target=self._write, daemon=True).start()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._process, name)

    def write(self, task: bytes) -> None:
        self.talk.start(task.decode())

    def close(self) -> None:
        """The runner closes the input once the task is written; the conversation closes it when it has ended."""

    def _write(self) -> None:
        with contextlib.suppress(OSError, ValueError):   # an agent that has gone says why by its status
            while message := self._out.get():
                self._process.stdin.write(message)
                self._process.stdin.flush()
            self._process.stdin.close()

    def _read(self) -> None:
        held = b""
        with contextlib.suppress(OSError):
            while chunk := os.read(self._process.stdout.fileno(), 65536):
                *lines, held = (held + chunk).split(b"\n")
                self.lines.extend(lines)
                if any(_ends(line) for line in lines):
                    self.answered.set()
                while chunk:
                    chunk = chunk[os.write(self._pass, chunk):]
        os.close(self._pass)

    def line(self, raw: bytes) -> bool:
        self.taken += bool(raw)
        return self.talk.line(raw)

    def stopped(self) -> None:
        """Cancel the turn and take in what the agent says until it has answered, for at most `CANCEL_WAIT_S`."""
        self.talk.cancel()
        self.answered.wait(CANCEL_WAIT_S)
        for each in self.lines[self.taken:]:
            self.talk.line(each)
        self.talk._flush()


def _ends(line: bytes) -> bool:
    try:
        said = json.loads(line)
    except ValueError:
        return False
    return isinstance(said, dict) and "method" not in said and said.get("id") == PROMPT


class _Heard:
    """`delegate_confined.Stream` as the runner's reading loop takes it, with each line going through the conversation first."""

    def __init__(self, pipes: _Pipes, stream: delegate_confined.Stream) -> None:
        self.line, self.looked = pipes.line, stream.looked


class Driver:
    """The trial in the runner's place: ``renderer`` and ``follow`` stand for the runner's own, which are ``rendered`` and ``followed``."""

    def __init__(self, rendered: Callable[..., Any], followed: Callable[..., None]) -> None:
        self._rendered, self._followed = rendered, followed
        self._taken: dict[str, str] = {}
        """The conversation each box takes up, by the box's folder."""
        self.agent: dict[str, Any] = {}

    def renderer(self, rec: Any, binary: str, served: tuple[str, str, int], ask: Any = None,
                 *more: Any) -> Callable[[isolation.Context], isolation.Rendering]:
        """The runner's renderer, whatever else it is given passed on, with this driver's command line, preload and title."""
        plain = self._rendered(rec, binary, served, ask, *more)
        session = (ask or delegate_confined.work_pass(rec)).session

        def render(context: isolation.Context) -> isolation.Rendering:
            made = plain(context)
            self._taken[context.stage] = session
            files = dict(made.files)
            entry = f"{opencode.PLUGIN}/{opencode.ENTRY}"
            files[entry] = titled(files[entry])
            through = ("", "")
            if context.server_socket:
                files[opencode.SOCKET_PRELOAD] = preload(context.server_socket)
                through = (context.server_socket, str(context.file(opencode.SOCKET_PRELOAD)))
            return dataclasses.replace(made, argv=argv_of(binary, through, session, context.stage), files=files)

        return render

    def follow(self, confinement: Any, process: Any, message: bytes, stream: delegate_confined.Stream, watch: tuple[Callable[..., Any], ...]) -> None:
        folder = str(confinement.profile.stage)
        pipes = _Pipes(process, stream, folder, self._taken[folder])
        try:
            self._followed(confinement, pipes, message, _Heard(pipes, stream), watch)
        except (SystemExit, KeyboardInterrupt):
            pipes.stopped()
            raise
        finally:
            self.agent = pipes.talk.agent
            pipes.talk._flush()

    @contextlib.contextmanager
    def installed(self) -> Iterator[None]:
        """This driver in the runner's place for the block."""
        delegate_confined.renderer, delegate_confined.follow = self.renderer, self.follow
        try:
            yield
        finally:
            delegate_confined.renderer, delegate_confined.follow = self._rendered, self._followed


def run(name: str) -> int:
    """`delegate_run.run` of delegate ``name`` over this driver."""
    with Driver(delegate_confined.renderer, delegate_confined.follow).installed():
        return delegate_run.run(name)


def start(words: Sequence[str]) -> int:
    """`poolhouse-delegate start` on ``words``, whose detached runner is this module."""
    plain = jobs.detach

    def detach(module: str, args: Sequence[str], **more: Any) -> Any:
        if module == delegate_run.__name__:
            module, args = "poolhouse.delegate_acp", ["run", *args]
        return plain(module, args, **more)

    jobs.detach = detach
    try:
        return delegate.main(["start", *words])
    finally:
        jobs.detach = plain


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run(sys.argv[2]) if sys.argv[1] == "run" else start(sys.argv[2:]))
