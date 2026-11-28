"""The model servers guests are answered by: one private llama.cpp server for each active guest, leased from the broker.

A server of one's own is what keeps a guest's conversation hers: it stays in that process's memory from one turn to the next, nobody
else's request reaches the process, so nothing of it can be matched or restored by another, and the host's own servers, slots and
caches are not touched. It is given back by ending the process: when the guest has been idle, is removed, or sharing stops.
Nothing is stopped or evicted to make room; a share that does not fit says so.

Only llama.cpp (GGUF weights) is covered.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil

from poolhouse import serverkeys
from poolhouse.serve import ServerManager, ServerSpec, free_port
from poolhouse.serve.admission import estimate_bytes

__all__ = ["CACHE_MB", "CONTEXT", "IDLE_S", "DoesNotFit", "PrivateServers"]

CONTEXT = 32_768
CACHE_MB = 2048
IDLE_S = 1800.0
LOAD_S = 600.0


class DoesNotFit(RuntimeError):
    """A guest's server does not fit beside what this computer is running."""


class PrivateServers:
    """The private servers of the guests of one shared model."""

    def __init__(self, model: str | Path, *, context: int = CONTEXT, cache_mb: int = CACHE_MB, idle_s: float = IDLE_S,
                 manager: Any = None) -> None:
        self.model = Path(model)
        if self.model.suffix.lower() != ".gguf" or not self.model.is_file():
            raise ValueError("guest sharing covers llama.cpp only: name a GGUF model file this computer holds "
                             "(Strata, MLX, vLLM and SGLang models are not covered)")
        self.context, self.cache_mb, self.idle_s = int(context), int(cache_mb), float(idle_s)
        self.manager = manager or ServerManager()
        self._held: dict[str, list[Any]] = {}          # guest -> [server info, requests in flight, last used]
        self._lock = threading.RLock()

    def spec(self, guest: str, port: int) -> ServerSpec:
        return ServerSpec(model=self.model, port=port, context=self.context, parallel=1, private=guest, cache_ram_mb=self.cache_mb)

    def need_bytes(self) -> int:
        """What one active guest's server takes: weights, context, and the conversations held in memory."""
        return estimate_bytes(self.spec("measure", 1))

    def acquire(self, guest: str) -> tuple[str, str]:
        """``(address, key)`` of the guest's server, started when she has none. `release` follows each request."""
        with self._lock:
            held = self._held.get(guest)
            if held is not None and held[0].pid and not psutil.pid_exists(held[0].pid):
                self.drop(guest)
                held = None
            if held is None:
                need, room = self.need_bytes(), int(psutil.virtual_memory().available)
                if need > room:
                    raise DoesNotFit(f"a guest's copy of this model needs {need / 2**30:.1f} GiB and {room / 2**30:.1f} GiB is free; "
                                     "nothing was stopped to make room")
                port = free_port()
                info = self.manager.lease(self.spec(guest, port), timeout=LOAD_S, reason="a guest's private model server")
                held = self._held[guest] = [info, 0, time.monotonic()]
            held[1] += 1
            return str(held[0].base_url), serverkeys.for_url(str(held[0].base_url))

    def release(self, guest: str) -> None:
        with self._lock:
            if held := self._held.get(guest):
                held[1] = max(0, held[1] - 1)
                held[2] = time.monotonic()

    def drop(self, guest: str) -> None:
        """Stop the guest's server, which gives back everything it held of hers."""
        with self._lock:
            held = self._held.pop(guest, None)
        if held is not None:
            self.manager.release(held[0])

    def sweep(self, allowed: Callable[[str], bool]) -> None:
        """Drop the server of a guest who may no longer use it, and of one who has been idle."""
        now = time.monotonic()
        with self._lock:
            gone = [g for g, (_, busy, last) in self._held.items() if not allowed(g) or (not busy and now - last >= self.idle_s)]
        for guest in gone:
            self.drop(guest)

    def active(self) -> list[str]:
        with self._lock:
            return sorted(self._held)

    def close(self) -> None:
        for guest in self.active():
            self.drop(guest)
