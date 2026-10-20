"""Two private Unix listeners for installation discovery and fixed producer control.

Kernel connector identity is evidence, never a source grant. The factory supplies
bounded callbacks; durable admission, capability checks and replay handling remain
there. Each connection dispatches at most one request, with no plain TLS fallback.
"""

from __future__ import annotations

import contextlib
import copy
import json
import math
import os
import re
import secrets
import socket
import ssl
import stat
import threading
import time
from pathlib import Path

from poolhouse import unix_socket
from poolhouse.graph.guard import MAX_BODY
from poolhouse.json_objects import unique_object

from . import producer_bootstrap, tls
from .framing import HANDSHAKE_S, MOST_CONNECTIONS, SOCKET_S
from .job_records import process_started

METADATA_BYTES = 64 * 1024
BRIDGE_BYTES = MAX_BODY * 2 + METADATA_BYTES
_ACTIONS = {"receivers", "offer", "offer-status", "withdraw", "bridge-publish", "bridge-receipt"}
_HEX32 = re.compile("[0-9a-f]{32}")
_HEX64 = re.compile("[0-9a-f]{64}")


def _identity(row):
    return row.st_dev, row.st_ino, row.st_mode, row.st_uid


class _Location:
    def __init__(self, root):
        self.root = Path(root)
        self.descriptors, self.anchors, self.sockets = [], [], {}
        self.name = "producer-" + secrets.token_hex(8)
        if not self.root.is_absolute() or ".." in self.root.parts:
            raise ValueError("Private producer directory must be absolute")
        try:
            at = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self.descriptors.append(at)
            for component in self.root.parts[1:]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=at)
                self.descriptors.append(child)
                self.anchors.append((at, component, child, _identity(os.fstat(child))))
                at = child
            info = os.fstat(at)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError("Private producer directory is unavailable")
            self.parent = at
            self.path = self.root / self.name
            if len(os.fsencode(self.path / "bootstrap.sock")) > 103:
                raise ValueError("Private producer directory path is too long")
            os.mkdir(self.name, mode=0o700, dir_fd=at)
            self.directory = os.open(
                self.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=at
            )
            self.descriptors.append(self.directory)
            self.anchors.append(
                (at, self.name, self.directory, _identity(os.fstat(self.directory)))
            )
        except (OSError, ValueError):
            self.close()
            raise

    def bind(self, name):
        channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            channel.bind(str(self.path / name))
            os.chmod(name, 0o600, dir_fd=self.directory, follow_symlinks=False)
            self.sockets[name] = _identity(
                os.stat(name, dir_fd=self.directory, follow_symlinks=False)
            )
            channel.listen(MOST_CONNECTIONS)
            channel.settimeout(0.01)
            self.check()
            return channel
        except (OSError, ValueError):
            channel.close()
            raise

    def check(self):
        for parent, name, child, original in self.anchors:
            if (
                _identity(os.fstat(child)) != original
                or _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != original
            ):
                raise OSError("Private producer directory changed")
        for name, original in self.sockets.items():
            if _identity(os.stat(name, dir_fd=self.directory, follow_symlinks=False)) != original:
                raise OSError("Private producer listener changed")

    def close(self):
        for name, original in self.sockets.items():
            with contextlib.suppress(OSError):
                if (
                    _identity(os.stat(name, dir_fd=self.directory, follow_symlinks=False))
                    == original
                ):
                    os.unlink(name, dir_fd=self.directory)
        if hasattr(self, "directory"):
            with contextlib.suppress(OSError):
                if _identity(
                    os.stat(self.name, dir_fd=self.parent, follow_symlinks=False)
                ) == _identity(os.fstat(self.directory)):
                    os.rmdir(self.name, dir_fd=self.parent)
        for descriptor in reversed(self.descriptors):
            os.close(descriptor)
        self.descriptors.clear()


def _remaining(channel, deadline, most=None):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Producer connection expired")
    channel.settimeout(min(remaining, most) if most is not None else remaining)


def _finite(value):
    pending, active = [(value, False)], set()
    while pending:
        item, leaving = pending.pop()
        if isinstance(item, (dict, list)):
            if leaving:
                active.remove(id(item))
                continue
            if id(item) in active:
                raise ValueError("Cyclic producer response")
            active.add(id(item))
            pending.append((item, True))
            if isinstance(item, dict) and any(not isinstance(key, str) for key in item):
                raise ValueError("Invalid producer fields")
            children = item.values() if isinstance(item, dict) else item
            pending.extend((child, False) for child in children)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Invalid producer number")


def receive_frame(channel, *, deadline, maximum=METADATA_BYTES):
    def exact(size):
        result = bytearray()
        while len(result) < size:
            _remaining(channel, deadline)
            chunk = channel.recv(min(size - len(result), 65536))
            if not chunk:
                raise OSError("Producer connection ended")
            result.extend(chunk)
        return bytes(result)

    size = int.from_bytes(exact(4), "big")
    if not 0 < size <= maximum:
        raise ValueError("Producer frame exceeds its bound")
    value = json.loads(exact(size).decode("utf-8"), object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError("Producer frame must be an object")
    _finite(value)
    return value


def send_frame(channel, value, *, deadline, maximum=METADATA_BYTES, started=None):
    _finite(value)
    encoded, size = [], 0
    for piece in json.JSONEncoder(separators=(",", ":"), allow_nan=False).iterencode(value):
        part = piece.encode()
        size += len(part)
        if size > maximum:
            raise ValueError("Producer response exceeds its bound")
        encoded.append(part)
    raw = b"".join(encoded)
    _remaining(channel, deadline)
    if started is not None:
        started["value"] = True
    channel.sendall(len(raw).to_bytes(4, "big") + raw)


def _retain(channel, deadline):
    _remaining(channel, deadline)
    channel.recv(1)


def _request(value, admitted):
    if (
        set(value) != {"version", "action", "request"}
        or type(value["version"]) is not int
        or value["version"] != 1
        or not isinstance(value["action"], str)
        or value["action"] not in _ACTIONS
        or not isinstance(value["request"], dict)
    ):
        raise ValueError("Invalid producer operation")
    action, request = value["action"], value["request"]
    if action.startswith("bridge-"):
        if admitted is not True:
            raise PermissionError("Private bridge admission is unavailable")
    elif action == "receivers":
        if request:
            raise ValueError("Invalid receiver request")
    elif action == "offer":
        if (
            set(request) != {"receiver_id", "label"}
            or not isinstance(request["receiver_id"], str)
            or not 0 < len(request["receiver_id"]) <= 256
            or not isinstance(request["label"], str)
            or len(request["label"]) > 256
        ):
            raise ValueError("Invalid producer offer")
    elif (
        set(request) != {"offer_id"}
        or not isinstance(request["offer_id"], str)
        or not _HEX32.fullmatch(request["offer_id"])
    ):
        raise ValueError("Invalid producer offer identity")
    return action, request


class ControlServer:
    """Own the two listener inodes and all bounded connections until explicit close."""

    def __init__(self, root, *, identity, current, dispatch, bridge):
        self.current, self.dispatch, self.bridge = current, dispatch, bridge
        self.identity = identity
        self.certificate = identity.beacon
        if ssl.PEM_cert_to_DER_cert(identity.certfile.read_text()) != identity.der:
            raise ValueError("Producer TLS identity does not match its certificate")
        self.context = tls.server_context(identity)
        self.process = {"pid": os.getpid(), "started": process_started(os.getpid())}
        if self.process["started"] is None:
            raise OSError("Producer daemon identity is unavailable")
        self.instance = secrets.token_hex(32)
        self._lock, self._stop = threading.RLock(), threading.Event()
        self._connections, self._workers, self._listeners, self._acceptors = set(), set(), [], []
        self._room = threading.BoundedSemaphore(MOST_CONNECTIONS)
        self._location = _Location(root)
        self.bootstrap_path = str(self._location.path / "bootstrap.sock")
        self.control_path = str(self._location.path / "control.sock")
        self._holder = producer_bootstrap.BootstrapHolder(root, current=self._binding)
        try:
            self._snapshot()
            for name, secure in (("bootstrap.sock", False), ("control.sock", True)):
                listener = self._location.bind(name)
                self._listeners.append(listener)
                thread = threading.Thread(target=self._accept, args=(listener, secure), daemon=True)
                self._acceptors.append(thread)
            for thread in self._acceptors:
                thread.start()
        except (OSError, ValueError):
            self.close()
            raise

    def _snapshot(self):
        self._location.check()
        value = self.current()
        if (
            not isinstance(value, dict)
            or set(value) != {"pool", "runtime"}
            or any(
                not isinstance(item, str) or not _HEX64.fullmatch(item) for item in value.values()
            )
            or self.identity.beacon != self.certificate
            or process_started(self.process["pid"]) != self.process["started"]
        ):
            raise OSError("Current producer daemon is unavailable")
        return copy.deepcopy(value)

    def _binding(self):
        return {
            **self._snapshot(),
            "certificate": self.certificate,
            "control_path": self.control_path,
            "daemon": {**self.process, "instance": self.instance},
        }

    def _check(self, channel, process, snapshot, admitted=False):
        if (
            self._stop.is_set()
            or self._snapshot() != snapshot
            or not unix_socket.matches(channel, process)
            or (admitted and self.bridge(copy.deepcopy(process)) is not True)
        ):
            raise OSError("Producer connection identity changed")

    def _accept(self, listener, secure):
        while not self._stop.is_set():
            try:
                channel, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with self._lock:
                if self._stop.is_set() or not self._room.acquire(blocking=False):
                    channel.close()
                    continue
                self._connections.add(channel)
                thread = threading.Thread(target=self._serve, args=(channel, secure), daemon=True)
                self._workers.add(thread)
                thread.start()

    def _answer(self, value, secure, process, admitted, check):
        if secure:
            action, request = _request(value, admitted)
            if action.startswith("bridge-") and self.bridge(copy.deepcopy(process)) is not True:
                raise PermissionError("Private bridge admission changed")
            check()
            result = self.dispatch(action, request, copy.deepcopy(process))
            if not isinstance(result, dict):
                raise ValueError("Invalid producer result")
            answer = {"ok": True, "result": result}
        else:
            if set(value) != {"nonce"}:
                raise ValueError("Invalid bootstrap request")
            answer = self._holder.answer(value["nonce"])
        return answer

    def _serve(self, channel, secure):
        deadline = time.monotonic() + SOCKET_S
        admitted, process, snapshot = False, None, None
        started = {"value": False}
        try:
            process, snapshot = unix_socket.identity(channel), self._snapshot()
            self._check(channel, process, snapshot, admitted)
            if secure:
                admitted = self.bridge(copy.deepcopy(process)) is True
                with self._lock:
                    if self._stop.is_set():
                        raise OSError("Producer listener stopped")
                    wrapped = self.context.wrap_socket(
                        channel, server_side=True, do_handshake_on_connect=False
                    )
                    self._connections.discard(channel)
                    self._connections.add(wrapped)
                channel = wrapped
                _remaining(channel, deadline, HANDSHAKE_S)
                channel.do_handshake()
                self._check(channel, process, snapshot, admitted)
            most = BRIDGE_BYTES if admitted else METADATA_BYTES
            value = receive_frame(channel, deadline=deadline, maximum=most)
            self._check(channel, process, snapshot, admitted)
            answer = self._answer(
                value,
                secure,
                process,
                admitted,
                lambda: self._check(channel, process, snapshot, admitted),
            )
            self._check(channel, process, snapshot, admitted)
            send_frame(channel, answer, deadline=deadline, maximum=most, started=started)
            self._check(channel, process, snapshot, admitted)
            # Retain kernel endpoint evidence until the client verifies and closes.
            # Any extra byte ends this connection; it is never another operation.
            _retain(channel, deadline)
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            RuntimeError,
            OverflowError,
            RecursionError,
        ) as exc:
            if not started["value"] and process is not None and snapshot is not None:
                with contextlib.suppress(OSError, ValueError, TypeError, KeyError, RuntimeError):
                    self._check(channel, process, snapshot, admitted)
                    code = (
                        "busy"
                        if isinstance(exc, BlockingIOError)
                        else "conflict"
                        if isinstance(exc, FileExistsError)
                        else "invalid"
                        if isinstance(exc, ValueError)
                        else "unavailable"
                    )
                    send_frame(
                        channel,
                        {
                            "ok": False,
                            "error": "Producer request could not be completed",
                            "code": code,
                        },
                        deadline=deadline,
                    )
        finally:
            with self._lock:
                self._connections.discard(channel)
                self._workers.discard(threading.current_thread())
            channel.close()
            self._room.release()

    def close(self):
        self._stop.set()
        with self._lock:
            for listener in self._listeners:
                with contextlib.suppress(OSError):
                    listener.shutdown(socket.SHUT_RDWR)
                listener.close()
            for channel in tuple(self._connections):
                with contextlib.suppress(OSError):
                    channel.shutdown(socket.SHUT_RDWR)
                channel.close()
            threads = [*self._acceptors, *self._workers]
        deadline = time.monotonic() + SOCKET_S
        for thread in threads:
            if thread.ident is not None:
                thread.join(max(0, deadline - time.monotonic()))
        self._location.close()
