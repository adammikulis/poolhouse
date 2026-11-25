"""The session bridge: hooks and workspace verbs run in the parent, and the confined command is a client of one channel
(docs/design-isolated-launcher.md, 7).

One parent-owned service per session on a unix socket in a directory of mode 0700 with a random name; the channel is the only endpoint a
profile names besides the model bridge. A frame is one line of JSON with each field once, no longer than the served window times 4 bytes
(a tool input is model output, which the window bounds), and one is answered before the next is read. The permissions, the agent, the roots
and the protected paths are not in any frame: the launcher closed them into the `Handlers` it passed in, and a frame that carries one more
field than its kind has is refused. The one `grant` frame is answered, in the same round trip, with one fixed refusal whatever its kind and
subject; it is logged (the kind and a digest of the subject, never the text), counted for the sentinel, and never reported, queued or waited
on. Nothing here asks anyone anything.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import socket
import stat
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from poolhouse import activity
from poolhouse.errors import Error
from poolhouse.isolation import session_verbs
from poolhouse.isolation.refusals import IsolationRefused, sentence
from poolhouse.isolation.serving import IDLE_SECONDS, Server, credentials, tell
from poolhouse.json_objects import unique_object

__all__ = ["GRANT_REFUSAL", "Handlers", "SessionBridge", "Terms"]

GRANT_REFUSAL = "Grants are launch arguments; none is decided here."
"""The one answer to a `grant` frame: it names nothing the session asked and nothing the launch rule holds (AF2, AF5)."""
KINDS = ("read", "toolchain", "net", "publish", "in-place")
EVENTS = ("pre", "post", "stop")
FRAMES = {"hook": {"verb", "event", "payload"}, "workspace": {"verb", "name", "arguments"}, "tools": {"verb"}}
FAILURES = (Error, OSError, ValueError, LookupError, RuntimeError, TypeError, EOFError, AttributeError, ImportError, ArithmeticError)
FRAME_TOKENS = 512
"""A session may send one frame a minute for each this many tokens of its window, and never fewer than `MOST_FRAMES`. A frame is a hook event or a
workspace verb, that is a tool call the model wrote, so what bounds them is the model's own output; this is a ceiling that no harness comes
near (256k tokens of window give 500 a minute) and that a loop made to flood the parent's processes, log and sentinel meets in a minute."""
MOST_FRAMES = 60
MINUTE = 60.0
BUSY = "That is more frames than a session sends in a minute."
HOOK_FAILED = "The hook could not decide, so the call is blocked."
VERB_FAILED = "The workspace could not run {verb} ({kind})."
"""The fixed sentences of a failure: what failed and its kind, never a path, a name or a word the parent holds (the detail goes to the log)."""


@dataclass(frozen=True, slots=True)
class Handlers:
    """What the launcher closes into a bridge: ``hook`` decides one hook event with the launch's own rail (`harnesshook.pre`, `post` or
    `stop`), ``workspace`` runs one workspace tool as the one session the bridge acts for, ``log`` writes an activity record and ``events``
    tells the sentinel. The bridge imports none of them, so no import goes up."""

    hook: Callable[[str, dict[str, Any]], dict[str, Any]]
    workspace: Callable[[str, Mapping[str, Any]], Any]
    log: Callable[..., object] = activity.record
    events: Callable[[str, dict[str, object]], None] | None = None


@dataclass(frozen=True, slots=True)
class Terms:
    """What the launcher fixes about the session the bridge serves: whether it may act in the workspace (a read-only or planning session may
    only read) and the window the model serves, which bounds a frame."""

    writable: bool
    window: int
    claim_ttl_s: float = session_verbs.TTL_CAP
    """The longest a claim or a heartbeat of the session may last: the workspace's configured `claim_ttl_s`, which the launcher passes in."""
    verbs: Mapping[str, session_verbs.Verb] = field(default_factory=lambda: session_verbs.VERBS)
    """The one closed table of the session: the workspace's (`VERBS`) or a delegate's two ways to its lead (`LEAD`)."""


@dataclass(slots=True)
class Counts:
    """What the bridge counted: frames by kind, and the refusals by kind; never a frame's text."""

    frames: Counter[str] = field(default_factory=Counter)
    refused: Counter[str] = field(default_factory=Counter)


def digest(value: object) -> str:
    """A digest of a value the session chose, short enough to log and fixed in length: the text itself is never written down."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


class SessionBridge:
    """The bridge of one session. ``terms`` are what the launcher fixed about it, and ``root`` is a short path whose directory the socket's
    directory is made in. ``admit`` is told the process id of each connecting process and may refuse it (it is in the session's tree). A bridge
    is made with one, or with ``unadmitted``, the reason it has none, which is logged when it starts: no bridge admits every process of the user
    by default."""

    def __init__(self, handlers: Handlers, terms: Terms, *, root: str, admit: Callable[[int], bool] | None = None, unadmitted: str = "",  # noqa: PLR0913
                 idle: float = IDLE_SECONDS) -> None:
        if os.name != "posix":
            raise IsolationRefused(sentence("windows_network"))
        if admit is None and not unadmitted.strip():
            raise ValueError("a session bridge is made with an admit predicate, or with the reason it has none")
        self.handlers, self.writable, self.limit, self.idle, self._admit = handlers, terms.writable, int(terms.window) * 4, idle, admit
        self.unadmitted, self.claim_ttl_s, self.verbs = unadmitted.strip()[:200], float(terms.claim_ttl_s), terms.verbs
        self.budget = max(MOST_FRAMES, int(terms.window) // FRAME_TOKENS)
        self._times: deque[float] = deque()
        self._post = threading.Lock()
        self._last_post: dict[str, Any] = {}
        self.directory = str(Path(os.path.realpath(root)) / ("ph-" + secrets.token_hex(8)))
        self.path = str(Path(self.directory) / "s")
        self.pid = os.getpid()
        self.counts = Counts()
        self._lock = threading.Lock()
        self._server = Server(socket.AF_UNIX, self.path, self._connection, admit=self._admitted_here,
                              stopped=lambda: tell(self.handlers.events, "session-bridge", "isolation.service_stopped"))

    def __repr__(self) -> str:
        return f"SessionBridge(path={self.path!r})"

    def start(self) -> str:
        """Make the 0700 directory (it must not exist and must be ours), bind the socket in it and serve; the path a profile names."""
        Path(self.directory).mkdir(mode=0o700)
        made = Path(self.directory).lstat()
        if not stat.S_ISDIR(made.st_mode) or made.st_uid != os.getuid() or stat.S_IMODE(made.st_mode) != 0o700:
            raise OSError("the session bridge's directory is not a private directory of this user")
        try:
            self._server.start()
            Path(self.path).chmod(0o600)
        except OSError:
            with contextlib.suppress(OSError):
                Path(self.directory).rmdir()
            raise
        if self.unadmitted:
            with contextlib.suppress(*FAILURES):
                self.handlers.log("isolation.session_bridge_unadmitted", subject="session-bridge", outcome="started", meta={"reason": self.unadmitted})
        return self.path

    def stop(self) -> None:
        self._server.stop()
        with contextlib.suppress(OSError):
            Path(self.directory).rmdir()

    def summary(self) -> dict[str, dict[str, int]]:
        """The frames answered by kind and the refusals by kind, for the end-of-session record."""
        with self._lock:
            return {"frames": dict(self.counts.frames), "refused": dict(self.counts.refused)}

    def _admitted_here(self, connection: socket.socket) -> bool:
        """The connecting process runs as this user and, where the launcher said how, is one of the session's."""
        pid, uid = credentials(connection)
        if uid != os.getuid():
            return False
        return self._admit is None or bool(pid >= 0 and self._admit(pid))

    def _count(self, kind: str, *, refused: bool = False) -> None:
        with self._lock:
            self.counts.frames[kind] += 1
            if refused:
                self.counts.refused[kind] += 1

    def _connection(self, sock: socket.socket) -> None:
        """Frames, one at a time, until the client ends the connection or is silent for too long."""
        sock.settimeout(self.idle)
        buffer = bytearray()
        while (line := self._read(sock, buffer)) is not None:
            reply = self._answer(line)
            try:
                sock.sendall(json.dumps(reply, default=str).encode() + b"\n")
            except OSError:
                return
            if "refused" in reply and reply.get("close"):
                return

    def _read(self, sock: socket.socket, buffer: bytearray) -> bytes | None:
        while (end := buffer.find(b"\n")) < 0:
            if len(buffer) > self.limit:
                return b"\xff"
            try:
                data = sock.recv(65536)
            except OSError:
                return None
            if not data:
                return None
            buffer.extend(data)
        line = bytes(buffer[:end])
        del buffer[:end + 1]
        return line if len(line) <= self.limit else b"\xff"

    def _refuse(self, kind: str, why: str, **extra: object) -> dict[str, Any]:
        self._count(kind, refused=True)
        tell(self.handlers.events, "session-bridge", "isolation.refused_frame", kind=kind)
        return {"refused": why, **extra}

    def _within_budget(self) -> bool:
        """Whether one more frame is within what a session sends in a minute; the frames of the last minute are what is counted."""
        now = time.monotonic()
        with self._lock:
            while self._times and now - self._times[0] > MINUTE:
                self._times.popleft()
            if len(self._times) >= self.budget:
                return False
            self._times.append(now)
            return True

    def _answer(self, line: bytes) -> dict[str, Any]:
        def reject(constant: str) -> None:
            raise ValueError(constant)

        try:
            frame = json.loads(line.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject)
        except (ValueError, RecursionError):
            return self._refuse("malformed", "That is not a frame.", close=True)
        if not self._within_budget():
            return self._refuse("busy", BUSY)
        verb = frame.get("verb") if isinstance(frame, dict) else None
        if verb == "grant":
            return self._grant(frame)
        if not isinstance(verb, str) or verb not in FRAMES or set(frame) - FRAMES[verb]:
            return self._refuse("unknown", "That is not a frame this bridge answers.")
        if verb == "tools":
            self._count("tools")
            return {"tools": session_verbs.describe(self.verbs)}
        return self._hook(frame) if verb == "hook" else self._workspace(frame)

    def _grant(self, frame: Mapping[str, Any]) -> dict[str, Any]:
        """The one answer to every grant frame: the same bytes for every kind and subject, whatever they are and whatever the launch rule
        holds. The request is logged by its kind (only when it is one of the five) and a digest of its subject, and counted."""
        kind = frame.get("kind") if frame.get("kind") in KINDS else "other"
        self._count("grant", refused=True)
        with contextlib.suppress(*FAILURES):   # a log that cannot be written does not change the answer
            self.handlers.log("isolation.grant_frame", subject=f"{kind}:{digest(frame.get('subject'))}", outcome="refused")
        tell(self.handlers.events, "session-bridge", "isolation.grant_frame", kind=kind, severity="notice")
        return {"refused": GRANT_REFUSAL}

    def _hook(self, frame: Mapping[str, Any]) -> dict[str, Any]:
        event, payload = frame.get("event"), frame.get("payload")
        if event not in EVENTS or not isinstance(payload, dict):
            return self._refuse("hook", "That is not a hook event.")
        if event == "post" and not self._post.acquire(blocking=False):   # one notification runs at a time; the rest are answered as the last was
            self._count("hook")
            return {"answer": self._last_post}
        try:
            answer = self.handlers.hook(str(event), payload)
        except FAILURES:
            return self._refuse("hook", HOOK_FAILED)
        finally:
            if event == "post":
                self._post.release()
        if event == "post":
            self._last_post = answer if isinstance(answer, dict) else {}
        self._count("hook")
        return {"answer": answer}

    def _workspace(self, frame: Mapping[str, Any]) -> dict[str, Any]:
        name = frame.get("name")
        try:
            verb, arguments = session_verbs.check(str(name), frame.get("arguments"), ttl_cap=self.claim_ttl_s, table=self.verbs)
        except ValueError as why:
            return self._refuse("workspace", f"Refused: {why}.")
        if verb.acts and not self.writable:
            return self._refuse("workspace", "Refused: this session may only read the workspace.")
        try:
            result = self.handlers.workspace(verb.tool, arguments)
        except FAILURES as error:
            self._count("workspace_failed", refused=True)
            with contextlib.suppress(*FAILURES):   # the detail is the parent's to read, in its log
                self.handlers.log("isolation.workspace_failed", subject=f"workspace:{verb.tool}", outcome="failed",
                                  meta={"kind": type(error).__name__, "detail": str(error)[:400]})
            return {"error": VERB_FAILED.format(verb=str(name)[:40], kind=type(error).__name__)}
        self._count("workspace")
        return {"result": result}
