"""The confined side of the session bridge: the command a harness's hook runs, and the two tools to the lead, `ask` and `note`, as a shell command
and as the stdio MCP server of every harness.

    python -I -m poolhouse.isolation.client --socket PATH --pid PID hook pre|post|stop     (the event on standard input)
    python -I -m poolhouse.isolation.client --socket PATH --pid PID workspace ask --question TEXT | note --text TEXT
    python -I -m poolhouse.isolation.client --socket PATH --pid PID grant [KIND [SUBJECT]]
    python -I -m poolhouse.isolation.client --socket PATH --pid PID mcp

Standard library only, and it imports nothing of Poolhouse, so it runs as a file too (``python -I client.py ...``) under a profile that reads
nothing of the library. The socket path and the launcher's process id are written into the command by the launcher; no token exists, and none
is read from the environment or typed. On connecting, the client asks the operating system whose process listens on the socket and sends
nothing unless that is the launcher (docs/design-isolated-launcher.md, 7). Nothing here waits for a person or reads a terminal.
"""

from __future__ import annotations

import json
import socket
import struct
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TextIO

__all__ = ["Impostor", "Streams", "call", "connect", "run", "serve", "tool"]

SOL_LOCAL = 0
LOCAL_PEERPID = 0x002
BLOCKED = 2
REFUSED = 3
USAGE = "usage: client --socket PATH --pid PID (hook EVENT | workspace VERB --name TEXT | grant [KIND [SUBJECT]] | mcp)"
DENIED_HOOK = "poolhouse: the session bridge did not answer, so the call is blocked."


class Impostor(OSError):
    """The process that listens on the socket is not the launcher."""


@dataclass(slots=True)
class Streams:
    """The three streams of a command: what it reads, what it answers on and where it says why it refused."""

    inp: TextIO
    out: TextIO
    err: TextIO


def _listener(sock: socket.socket) -> int:
    """The process id of the process at the other end of ``sock`` as the operating system reports it."""
    if hasattr(socket, "SO_PEERCRED"):
        return struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))[0]
    return int(sock.getsockopt(SOL_LOCAL, LOCAL_PEERPID))


def connect(path: str, pid: int) -> socket.socket:
    """A connection to the bridge at ``path``, made only when the process listening there is ``pid``, the launcher's."""
    sock = socket.socket(socket.AF_UNIX)
    try:
        sock.connect(path)
        if _listener(sock) != pid:
            raise Impostor("the process that listens on the session socket is not the launcher")
    except OSError:
        sock.close()
        raise
    return sock


def call(sock: socket.socket, frame: dict[str, Any]) -> dict[str, Any]:
    """Send one frame and read its one answer."""
    sock.sendall(json.dumps(frame).encode() + b"\n")
    held = bytearray()
    while b"\n" not in held:
        more = sock.recv(65536)
        if not more:
            raise OSError("the session bridge ended the connection")
        held.extend(more)
    answer = json.loads(bytes(held).split(b"\n", 1)[0])
    if not isinstance(answer, dict):
        raise OSError("the session bridge's answer is not an object")
    return answer


def _options(words: list[str]) -> tuple[str, int, list[str]]:
    """The socket, the launcher's pid and the words that are left."""
    path, pid, rest, at = "", -1, [], 0
    while at < len(words):
        if words[at] == "--socket" and at + 1 < len(words):
            path, at = words[at + 1], at + 2
        elif words[at] == "--pid" and at + 1 < len(words) and words[at + 1].isdigit():
            pid, at = int(words[at + 1]), at + 2
        else:
            rest.append(words[at])
            at += 1
    if not path or pid < 0 or not rest:
        raise ValueError(USAGE)
    return path, pid, rest


def _data(streams: Streams) -> str:
    """What standard input holds, which is data the command was handed: a terminal is never read, since nothing typed there is an answer."""
    if streams.inp.isatty():
        raise ValueError("standard input is a terminal, and no command here reads one")
    return streams.inp.read()


def _hook(sock: socket.socket, event: str, streams: Streams) -> int:
    """One hook event: standard input to the bridge, the bridge's answer to standard output unchanged. A call that is not answered is
    blocked, a notification that is not answered is not."""
    payload = json.loads(_data(streams) or "{}")
    answer = call(sock, {"verb": "hook", "event": event, "payload": payload if isinstance(payload, dict) else {}})
    if "answer" not in answer:
        streams.err.write(f"{answer.get('refused', DENIED_HOOK)}\n")
        return BLOCKED if event == "pre" else 0
    if answer["answer"]:
        streams.out.write(json.dumps(answer["answer"], sort_keys=True) + "\n")
    return 0


def _arguments(argument: str, words: list[str], streams: Streams) -> dict[str, str]:
    """``--name TEXT`` as the verb's one argument; a text ``-`` reads standard input."""
    if len(words) != 2 or words[0] != f"--{argument}":
        raise ValueError(f"this verb takes --{argument} TEXT")
    return {argument: _data(streams) if words[1] == "-" else words[1]}


def _workspace(sock: socket.socket, verb: str, words: list[str], streams: Streams) -> int:
    tools = call(sock, {"verb": "tools"}).get("tools", {})
    if verb not in tools:
        streams.err.write("Refused: no such verb.\n")
        return REFUSED
    answer = call(sock, {"verb": "workspace", "name": verb, "arguments": _arguments(tools[verb]["argument"], words, streams)})
    if "result" not in answer:
        streams.err.write(f"{answer.get('refused') or answer.get('error', 'Refused.')}\n")
        return REFUSED
    streams.out.write(json.dumps(answer["result"], indent=1, default=str) + "\n")
    return 0


def tool(name: str, description: str, argument: str, hint: str) -> dict[str, Any]:
    """One tool of the server as an MCP client lists it: it takes the one text ``argument``."""
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "additionalProperties": False, "required": [argument],
                            "properties": {argument: {"type": "string", "description": hint}}}}


def _reply(message: dict[str, Any], listed: Callable[[], list[dict[str, Any]]],
           called: Callable[[str, dict[str, Any]], tuple[bool, str]]) -> dict[str, Any] | None:
    """The answer to one message of a server whose tools ``listed`` names and ``called`` runs, returning whether it ran and the words of its result."""
    method, ident, params = message.get("method", ""), message.get("id"), message.get("params") or {}
    if ident is None:
        return None
    if method == "initialize":
        result: dict[str, Any] = {"protocolVersion": params.get("protocolVersion") or "2024-11-05", "capabilities": {"tools": {}},
                                  "serverInfo": {"name": "lead", "version": "0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": listed()}
    elif method == "tools/call":
        ran, said = called(str(params.get("name", "")), params.get("arguments") or {})
        result = {"content": [{"type": "text", "text": said}], "isError": not ran}
    else:
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": "no such method"}}
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def _mcp(path: str, pid: int, streams: Streams) -> int:
    """The server as the session bridge's client. Each request that reaches the bridge has a connection of its own (the bridge ends a connection
    that is silent for its idle time, and a model is silent far longer than that between two calls)."""
    def listed() -> list[dict[str, Any]]:
        with connect(path, pid) as sock:
            return [tool(name, each["help"], each["argument"], each["hint"]) for name, each in call(sock, {"verb": "tools"}).get("tools", {}).items()]

    def called(name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        with connect(path, pid) as sock:
            answer = call(sock, {"verb": "workspace", "name": name, "arguments": arguments})
        said = answer["result"] if "result" in answer else answer.get("refused") or answer.get("error", "Refused.")
        return "result" in answer, said if isinstance(said, str) else json.dumps(said, indent=1, default=str)

    return serve(streams, listed, called)


def serve(streams: Streams, listed: Callable[[], list[dict[str, Any]]], called: Callable[[str, dict[str, Any]], tuple[bool, str]]) -> int:
    """A stdio MCP server: one JSON-RPC message per line in, one per line out, until standard input ends."""
    if streams.inp.isatty():
        raise ValueError("standard input is a terminal, and no command here reads one")
    for line in streams.inp:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            reply = _reply(message if isinstance(message, dict) else {}, listed, called)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        except OSError as failure:
            reply = {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                     "result": {"content": [{"type": "text", "text": f"The session bridge did not answer: {type(failure).__name__}."}], "isError": True}}
        if reply is not None:
            streams.out.write(json.dumps(reply) + "\n")
            streams.out.flush()
    return 0


def run(argv: list[str] | None = None, stdin: TextIO | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    """Run one command: 0 done, 2 a call blocked, 3 a request refused, 1 the bridge could not be reached or answered."""
    streams = Streams(stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr)
    words = list(sys.argv[1:] if argv is None else argv)
    event = words[words.index("hook") + 1] if "hook" in words and words.index("hook") + 1 < len(words) else "pre"
    try:
        path, pid, rest = _options(words)
        if rest == ["mcp"]:
            return _mcp(path, pid, streams)   # each of its requests connects on its own
        sock = connect(path, pid)
        with sock:
            if rest[0] == "hook" and len(rest) == 2 and rest[1] in ("pre", "post", "stop"):
                return _hook(sock, rest[1], streams)
            if rest[0] == "workspace" and len(rest) >= 2:
                return _workspace(sock, rest[1], rest[2:], streams)
            if rest[0] == "grant":
                answer = call(sock, {"verb": "grant", "kind": rest[1] if len(rest) > 1 else "", "subject": " ".join(rest[2:])})
                streams.err.write(f"{answer.get('refused', 'Refused.')}\n")
                return REFUSED
            raise ValueError(USAGE)
    except (OSError, ValueError) as failure:
        streams.err.write(f"poolhouse: {type(failure).__name__}: {failure}\n")
        return BLOCKED if event == "pre" and "hook" in words else 1


if __name__ == "__main__":
    raise SystemExit(run())
