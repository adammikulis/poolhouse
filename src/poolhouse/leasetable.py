"""The lease table of this device's node, for the programs that keep no queue of their own: the serve broker and the request gate.

One session of the node (`NATIVE`, on the pool board) is shared by every process of a state directory, and each caller is told
apart by the tag of its request, so the node orders them, drops the holder whose process ended and shows them in `lease_list`.
The session's token is kept in ``<state>/client/pool/<name>.token`` as every session's is.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poolhouse import node_health
from poolhouse.files import write_json, writing
from poolhouse.home import state as state_root
from poolhouse.lock import only_one
from poolhouse.platform import private_file

__all__ = ["BOARD", "NATIVE", "Table", "TableDown", "Terms", "default", "gpu", "line", "memory", "slot"]

BOARD = "pool"
NATIVE = {"model": "poolhouse-serve", "harness": "poolhouse-serve", "session": "leases"}
START_WAIT_S = 60.0
DOWN_S = 20.0
"""How long after a node would not start a call says so at once instead of asking it to start again."""
CONFIG = "lease-config.json"
CALL_S = 30.0
MIB = 1 << 20


class TableDown(OSError):
    """No node answers and none could be started."""


@dataclass(frozen=True)
class Terms:
    """How a lease is asked for: ``tag`` names the request among the others of the session, ``pid`` is the process whose end ends
    the lease, ``ttl_s`` how long one renewal keeps it, ``wait`` whether to queue when it is not free, ``quiet`` that it leaves no
    board entry (the GPU and a request line only)."""

    tag: str = ""
    pid: int = 0
    ttl_s: int = 0
    wait: bool = True
    quiet: bool = False


def gpu() -> dict[str, Any]:
    """The GPU, one holder at a time."""
    return {"type": "gpu"}


def line(name: str) -> dict[str, Any]:
    """The request line of a device that has no GPU lease: one request at a time."""
    return {"type": "claim", "kind": "server", "name": f"line:{name}"}


def memory(mb: int) -> dict[str, Any]:
    """``mb`` megabytes of the device's memory budget."""
    return {"type": "memory_mb", "mb": max(1, int(mb))}


def slot(model: str, *, context: int = 0, parallel: int = 0, draft: str = "") -> dict[str, Any]:
    """A served model in one shape."""
    return {"type": "model_slot", "model": model, "context": int(context), "parallel": int(parallel), "draft": draft}


class Table:
    """The node of ``state``: leases taken, followed, kept and given back as the session every serve process shares."""

    def __init__(self, state: Path | None = None) -> None:
        self.state = Path(state) if state else state_root("node")
        self._token = ""
        self._lock = threading.Lock()
        self._down: tuple[float, str] = (0.0, "")

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        """One request as the session; the node is started when it does not answer. `node_health.Refused` when it refuses."""
        wait = float(params.pop("_wait_s", 0))
        if time.monotonic() < self._down[0]:
            raise TableDown(self._down[1])
        for attempt in range(2):
            try:
                return self._send(method, params, self._session(), wait)
            except node_health.Refused as why:
                if attempt or why.code != "denied" or "token" not in str(why):
                    raise
                self._token = ""
            except OSError:
                if attempt:
                    raise
                try:
                    self._start()
                except TableDown as why:
                    self._down = (time.monotonic() + DOWN_S, str(why))
                    raise
        raise TableDown(f"the node of {self.state} did not answer")

    def _send(self, method: str, params: dict[str, Any], token: str = "", wait_s: float = 0.0) -> dict[str, Any]:
        body = node_health.request_body(method, params, board=BOARD, token=token)
        return node_health.result_of(node_health.exchange(self.state, body, CALL_S + wait_s))

    def _start(self) -> None:
        done = subprocess.run([sys.executable, "-m", "poolhouse.node_launch", "ensure", f"--state={self.state.absolute()}"],
                              capture_output=True, text=True, timeout=START_WAIT_S + 30, check=False)
        if done.returncode:
            raise TableDown(f"no node answers for {self.state} and it did not start: {(done.stdout + done.stderr).strip()[-300:]}")

    def _session(self) -> str:
        with self._lock:
            if self._token:
                return self._token
            self._token = self._kept() or self._registered()
            return self._token

    def _kept(self) -> str:
        """The token another process of this state directory kept for the session, or an empty string."""
        name = self._name()
        path = self.state / "client" / BOARD / f"{name}.token"
        return path.read_text(encoding="utf-8").strip() if name and path.is_file() else ""

    def _registered(self) -> str:
        """The token of the session registered now: one process of the state directory registers it, the others read its token."""
        self._folder()
        with only_one(self.state / "client" / BOARD / ".register.lock", timeout=CALL_S, announce=lambda _line: None):
            if token := self._kept():
                return token
            made = self._send("register", dict(NATIVE))
            self._keep(str(made["name"]), str(made["token"]))
            return str(made["token"])

    def _name(self) -> str:
        try:
            return str(self._send("session_lookup", {"harness": NATIVE["harness"], "session": NATIVE["session"]})["name"])
        except node_health.Refused:
            return ""

    def _folder(self) -> None:
        """The folders a session's token is kept in, for this user alone."""
        for folder in (self.state / "client", self.state / "client" / BOARD):
            folder.mkdir(exist_ok=True, mode=0o700)

    def _keep(self, name: str, token: str) -> None:
        target = self.state / "client" / BOARD / f"{name}.token"
        self._folder()
        with writing(target) as tmp:
            tmp.write_text(token + "\n", encoding="utf-8")
            private_file(tmp)

    def acquire(self, resources: list[dict[str, Any]], terms: Terms = Terms()) -> dict[str, Any]:  # noqa: B008 - frozen
        """The lease for ``resources`` as ``terms`` ask: ``held``, ``queued`` or, without ``wait``, ``busy``."""
        params: dict[str, Any] = {"resources": resources, "wait": terms.wait, "tag": terms.tag, "quiet": terms.quiet}
        params.update({key: value for key, value in (("pid", terms.pid), ("ttl_s", terms.ttl_s)) if value})
        return self.call("lease_acquire", **params)

    def wait(self, lease: str, seconds: float = 0.0) -> dict[str, Any]:
        """The lease once it is held, or still queued after ``seconds`` (the node answers each poll in 25 ms)."""
        return self.call("lease_wait", id=lease, timeout_ms=int(seconds * 1000), _wait_s=seconds)

    def renew(self, lease: str, *, ttl_s: int = 0, pid: int = 0) -> dict[str, Any]:
        """Extend a lease by ``ttl_s``; ``pid`` also binds it to that running process."""
        return self.call("lease_renew", id=lease, **{k: v for k, v in (("ttl_s", ttl_s), ("pid", pid)) if v})

    def release(self, lease: str) -> bool:
        """Give a lease back or cancel it while it queues; False when the node no longer had it."""
        try:
            return bool(self.call("lease_release", id=lease)["released"])
        except node_health.Refused as why:
            if why.code == "invalid":
                return False
            raise

    def listing(self) -> dict[str, Any]:
        """Every lease of the table and the table's settings."""
        return self.call("lease_list")

    def budget(self, memory_mb: int) -> None:
        """Make ``memory_mb`` the device's memory budget (0 is none); the node reads it at its next lease call."""
        path = self.state / CONFIG
        try:
            held = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            held = {}
        if isinstance(held, dict) and held.get("memory_mb", 0) == memory_mb:
            return
        write_json(path, {**(held if isinstance(held, dict) else {}), "memory_mb": int(memory_mb)})
        private_file(path)


_TABLES: dict[Path, Table] = {}
_LOCK = threading.Lock()


def default() -> Table:
    """The table of this machine's node, one per state directory in this process."""
    state = state_root("node")
    with _LOCK:
        return _TABLES.setdefault(state, Table(state))
