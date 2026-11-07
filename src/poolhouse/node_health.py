"""Ask a node on its local socket (a named pipe on Windows) whether it is alive: one framed `hello`, nothing else it can be refused for."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import stat
import struct
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from poolhouse import win32
from poolhouse.home import state as state_root
from poolhouse.platform import is_windows

SOCKET = "node.sock"
API_VERSION = 1
TIMEOUT_S = 2.0
MAX_FRAME = 1 << 20


def default_state() -> Path:
    """The node's state directory on this machine: where every caller that is not handed a state asks the node."""
    return state_root("node")


class Refused(ValueError):
    """The node refused a request; ``code`` is its word for why (``denied``, ``invalid``, ``quota``), and ``grant`` names what a
    person's key would sign to allow it, when a missing grant is why."""

    def __init__(self, message: str, grant: dict | None = None, code: str = "") -> None:
        super().__init__(message)
        self.grant = grant
        self.code = code


def key_of(text: str) -> str:
    """The 32 hex characters that name a state directory's pipe and stop event: the SHA-256 of its absolute path with
    backslashes, lower case, no `\\\\?\\` prefix and no trailing separator. `poolhouse-node`'s `sys::key_of` computes the same."""
    text = text.replace("/", "\\")
    text = text[4:] if text.startswith("\\\\?\\") else text
    return hashlib.sha256(text.rstrip("\\").lower().encode("utf-8")).hexdigest()[:32]


def state_key(state: Path) -> str:
    """`key_of` the absolute path of a state directory."""
    return key_of(os.path.normpath(Path(state).absolute()))


def pipe_name(state: Path) -> str:
    """The named pipe the node of a state directory serves on Windows."""
    return f"\\\\.\\pipe\\poolhouse-node-{state_key(state)}"


def node_stop_event(state: Path) -> str:
    """The named event whose signal makes the node of a state directory exit cleanly on Windows."""
    return f"Local\\poolhouse-node-stop-{state_key(state)}"


def socket_path(state: Path) -> Path:
    """Where the node of a state directory listens: a socket file, or on Windows a pipe name."""
    return Path(pipe_name(state)) if is_windows() else state / SOCKET


def _read(recv: Callable[[int], bytes], count: int) -> bytes:
    data = b""
    while len(data) < count:
        block = recv(count - len(data))
        if not block:
            raise ConnectionError("the node closed the connection")
        data += block
    return data


def _exchange_socket(state: Path, frame: bytes, timeout: float) -> bytes:
    family = getattr(socket, "AF_UNIX", None)
    if family is None:  # a Python without Unix sockets: no node can answer here
        raise OSError("this Python has no Unix sockets")
    until = time.monotonic() + timeout
    with socket.socket(family, socket.SOCK_STREAM) as stream:
        stream.settimeout(timeout)
        stream.connect(str(socket_path(state)))
        def within(count: int) -> bytes:
            """One read that cannot outlast what is left of the exchange's ``timeout``: a node that dribbles bytes is as late as a silent one."""
            left = until - time.monotonic()
            if left <= 0:
                raise TimeoutError("the node did not answer in time")
            stream.settimeout(left)
            return stream.recv(count)
        frame = _root_frame(state, frame, stream.sendall, within, lambda: _socket_server_pid(stream))
        stream.sendall(frame)
        return _reply(within)


def _root_key(state: Path) -> bytes:
    """Read a private root credential without following a Unix credential-directory or file symlink."""
    if is_windows():
        folder = state / "hook-credentials"
        path = folder / "root.key"
        if folder.is_symlink() or path.is_symlink():
            raise ValueError("redirected root credential")
        return bytes.fromhex(path.read_text(encoding="ascii").strip())
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    base = os.open(state.resolve(), flags)
    try:
        folder = os.open("hook-credentials", flags, dir_fd=base)
        try:
            directory = os.fstat(folder)
            if directory.st_uid != os.geteuid() or directory.st_mode & 0o077:
                raise ValueError("root credential directory is not private and owned")
            key = os.open("root.key", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
            try:
                info = os.fstat(key)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size != 64:
                    raise ValueError("root credential is not private and owned")
                return bytes.fromhex(os.read(key, 65).decode("ascii"))
            finally:
                os.close(key)
        finally:
            os.close(folder)
    finally:
        os.close(base)


def _socket_server_pid(stream: socket.socket) -> int:
    """The connected server process as reported by the local socket, not by its reply."""
    if sys.platform == "darwin":
        pid = stream.getsockopt(0, 2)  # SOL_LOCAL, LOCAL_PEERPID (Darwin libc).
    elif sys.platform.startswith("linux"):
        pid, _, _ = struct.unpack("3i", stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    else:
        raise OSError("the local socket cannot identify its server process")
    if pid <= 0:
        raise OSError("the local socket returned no server process")
    return pid


def _root_frame(state: Path, frame: bytes, send: Callable[[bytes], object], recv: Callable[[int], bytes], server_pid: Callable[[], int]) -> bytes:
    """Sign a registration on this connection; a child may register without the root key."""
    try:
        request = json.loads(frame[4:])
        if not isinstance(request, dict) or request.get("method") != "register" or not isinstance(request.get("params"), dict):
            return frame
        key = _root_key(state)
        if len(key) != 32:
            return frame
    except (OSError, ValueError, UnicodeError):
        return frame
    challenge = json.dumps({"v": API_VERSION, "id": 0, "method": "root_challenge", "params": {}}).encode()
    send(struct.pack(">I", len(challenge)) + challenge)
    reply = json.loads(_reply(recv))
    nonce = reply.get("result", {}).get("nonce") if isinstance(reply, dict) and reply.get("ok") is True else None
    if not isinstance(nonce, str):
        raise ValueError("the node did not answer the root challenge")
    params = request["params"]

    def text(value: object) -> str:
        return value if isinstance(value, str) else ""

    fields = [nonce, server_pid(), text(request.get("board")), text(request.get("token")), text(params.get("model")),
              text(params.get("harness")), text(params.get("session"))]
    body = json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode()
    params["root_proof"] = hmac.new(key, b"poolhouse/root-register/v1\n" + body, hashlib.sha256).hexdigest()
    encoded = json.dumps(request).encode()
    if len(encoded) > MAX_FRAME:
        raise ValueError("the signed registration exceeds the node frame limit")
    return struct.pack(">I", len(encoded)) + encoded


def _reply(recv: Callable[[int], bytes]) -> bytes:
    (size,) = struct.unpack(">I", _read(recv, 4))
    if size > MAX_FRAME:
        raise ValueError("the node sent an oversized frame")
    return _read(recv, size)


def _talk_pipe(state: Path, frame: bytes, out: dict, timeout: float) -> None:
    name, until = pipe_name(state), time.monotonic() + timeout
    try:
        while True:
            try:
                pipe = win32.open_pipe(name)
                break
            except OSError as exc:
                if getattr(exc, "winerror", 0) != win32.ERROR_PIPE_BUSY or time.monotonic() > until:
                    raise
                time.sleep(0.02)
        with pipe:
            frame = _root_frame(state, frame, pipe.write, pipe.read, lambda: win32.pipe_server_pid(pipe))
            pipe.write(frame)
            out["reply"] = _reply(pipe.read)
    except (OSError, ValueError) as exc:
        out["error"] = exc


def _exchange_pipe(state: Path, frame: bytes, timeout: float) -> bytes:
    """A pipe read has no timeout, so the exchange runs in a thread that is given ``timeout`` and then abandoned."""
    out: dict = {}
    worker = threading.Thread(target=_talk_pipe, args=(state, frame, out, timeout), daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError("the node did not answer in time")
    if "error" in out:
        raise out["error"]
    return out["reply"]


def exchange(state: Path, body: bytes, timeout: float = TIMEOUT_S) -> bytes:
    """One framed request body to the node of ``state`` and the body of its reply."""
    windows = is_windows()
    return (_exchange_pipe if windows else _exchange_socket)(state if windows else state.resolve(), struct.pack(">I", len(body)) + body, timeout)


def request_body(method: str, params: dict | None = None, *, board: str = "", token: str = "") -> bytes:
    """The body of a request of ``method`` to a node."""
    request = {"v": API_VERSION, "id": 1, "method": method, "params": params or {}}
    request.update({key: value for key, value in (("board", board), ("token", token)) if value})
    return json.dumps(request).encode()


def result_of(body: bytes) -> dict:
    """The result in a node's reply, or `Refused` when the reply says it was."""
    reply = json.loads(body)
    if reply.get("ok") is not True:
        error = reply.get("error") or {}
        raise Refused(str(error.get("message", "refused")), error.get("grant"), str(error.get("code", "")))
    return reply["result"]


def call(state: Path, method: str, params: dict | None = None, *, board: str = "", token: str = "") -> dict:
    """One request to the node of ``state``; the result, or OSError/ValueError when it is dead or refuses."""
    return result_of(exchange(state, request_body(method, params, board=board, token=token)))


def node_health(state: Path) -> dict | None:
    """The node's `hello` (node, version, pid, fingerprint) with the socket and the round trip, or None when it does not answer."""
    began = time.monotonic()
    try:
        said = call(state, "hello")
    except (OSError, ValueError):
        return None
    return {**said, "socket": str(socket_path(state)), "latency_ms": round((time.monotonic() - began) * 1000, 1)}
