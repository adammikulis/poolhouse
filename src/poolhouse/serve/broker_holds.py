"""What a broker holds besides servers: the GPU claim of a training run, the cores of the test runners, and its leases in the node's table."""

from __future__ import annotations

import contextlib
import math
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from poolhouse import leasetable
from poolhouse.node_health import Refused
from poolhouse.serve import admission
from poolhouse.serve.broker_lifecycle import admitted
from poolhouse.serve.leases import recorded_servers
from poolhouse.serve.process import started_at

__all__ = ["MODEL_START", "POLL_S", "BrokerHolds"]

POLL_S = 0.5
MODEL_START = "gpu-model-start:"
"""The prefix of a claim that reserves a native model start until its server lease is recorded."""


class BrokerHolds:
    """The claims, the cores and the node leases of a `Broker`, which supplies the state they work on."""

    if TYPE_CHECKING:
        _cond: threading.Condition
        claims: dict[str, dict[str, Any]]
        claim_waiters: dict[str, dict[str, Any]]
        cores: dict[str, tuple[int, int, float]]
        cpus: int
        servers: dict[int, Any]
        table: leasetable.Table
        alive: Callable[[int | None], bool]
        manager: Any

        def adopt(self) -> list[Any]: ...
        @staticmethod
        def _held_said(held: Any) -> str: ...

    @admitted
    def claim(self, name: str, pid: int, info: Mapping[str, Any], *,
              timeout: float | None = 0.0, connected: Callable[[], bool] | None = None) -> dict[str, Any]:
        """Claim for this process generation; None waits until admission or cancellation."""
        if timeout is not None and not math.isfinite(timeout):
            raise ValueError("A claim wait must be finite or explicitly unbounded.")
        waiter = {"name": name, "pid": pid, "started": started_at(pid), "info": dict(info), "since": time.time()}
        if not self._waiter_alive(waiter, connected):
            raise RuntimeError("The compute requester is no longer connected or cannot prove its process identity.")
        if name == "gpu-training":
            self.adopt()
        deadline = None if timeout is None else time.monotonic() + timeout
        ticket = uuid.uuid4().hex
        with self._cond:
            self.claim_waiters[ticket] = waiter
            try:
                return self._await_claim(ticket, deadline, connected)
            finally:
                self.claim_waiters.pop(ticket, None)
                self._cond.notify_all()

    def _claim_alive(self, held: Mapping[str, Any]) -> bool:
        return bool(held.get("started") is not None and self.alive(held["pid"])
                    and started_at(held["pid"]) == held["started"])

    def _waiter_alive(self, waiter: Mapping[str, Any], connected: Callable[[], bool] | None) -> bool:
        try:
            return self._claim_alive(waiter) and (connected is None or connected())
        except OSError:
            return False

    def _await_claim(self, ticket: str, deadline: float | None, connected: Callable[[], bool] | None) -> dict:
        waiter = self.claim_waiters[ticket]
        name, pid = waiter["name"], waiter["pid"]
        while True:
            if not self._waiter_alive(waiter, connected):
                raise RuntimeError("The queued compute request was cancelled or its process identity changed.")
            held = self.claims.get(name)
            if held and held["pid"] == pid and self._claim_alive(held):
                if held["info"] != waiter["info"]:
                    raise ValueError("This process already holds that claim for different work.")
                return {"granted": True, **held}
            busy = next((server for server in self.servers.values()
                         if server.loading or server.unmanaged or server.holders), None) if name == "gpu-training" else None
            starting = next((value for key, value in self.claims.items()
                             if key.startswith(MODEL_START) and self.alive(value["pid"])), None) if name == "gpu-training" else None
            first = next(key for key, value in self.claim_waiters.items() if value["name"] == name)
            if first == ticket and busy is None and starting is None and (held is None or not self._claim_alive(held)):
                lease = self._gpu_for(name, pid)
                if lease is not None:
                    if not self._waiter_alive(waiter, connected):
                        if lease:
                            self.table.release(lease)
                        raise RuntimeError("The compute requester disappeared before admission.")
                    self.claims[name] = {**{key: waiter[key] for key in ("pid", "started", "info", "since")}, "lease": lease}
                    return {"granted": True, **self.claims[name]}
                held = {"pid": 0, "info": {"purpose": "a request or another claim holds the GPU"}, "since": time.time()}
            if busy is not None:
                holder = next(iter(busy.holders.values()), (busy.pid or 0, "loading model"))
                held = {"pid": holder[0], "info": {"purpose": self._held_said(busy)}, "since": time.time()}
            elif starting is not None:
                held = starting
            left = None if deadline is None else deadline - time.monotonic()
            if left is not None and left <= 0:
                return {"granted": False, **(held or {"pid": 0, "info": {"purpose": "an earlier claim is queued"}})}
            self._cond.wait(POLL_S if left is None else min(left, POLL_S))

    def _gpu_for(self, name: str, pid: int) -> str | None:
        """The id of the node's lease of the GPU for the claim ``name`` of ``pid`` ("" for a claim that holds nothing of the node's),
        or None when the GPU is in use."""
        if name != "gpu-training":
            return ""
        got = self.table.acquire([leasetable.gpu()], leasetable.Terms(tag=f"claim.{name}", pid=pid, ttl_s=admission.KEEP_S, wait=False))
        return str(got["id"]) if got["state"] == "held" else None

    def unclaim(self, name: str, pid: int) -> bool:
        """Let go of ``name`` if ``pid`` holds it."""
        with self._cond:
            held = self.claims.get(name)
            if held is None or held["pid"] != pid or (not name.startswith(MODEL_START) and not self._claim_alive(held)):
                return False
            del self.claims[name]
            self._cond.notify_all()
        if held.get("lease"):
            with contextlib.suppress(Refused, OSError):
                self.table.release(held["lease"])
        return True

    @admitted
    def take_cores(self, pid: int, want: int) -> dict[str, Any]:
        """Cores for ``pid`` to run tests on: ``want`` at most, what is free at least one.

        Never refuses and never waits -- a suite that cannot have what it asked for runs
        narrower rather than queueing.
        """
        with self._cond:
            self.cores = {k: v for k, v in self.cores.items() if self.alive(v[0])}
            taken = sum(cores for _, cores, _ in self.cores.values())
            cores = max(1, min(max(1, want), self.cpus - taken))
            lease = uuid.uuid4().hex
            self.cores[lease] = (pid, cores, time.time())
            return {"lease": lease, "cores": cores, "cpus": self.cpus, "taken": taken}

    def give_back_cores(self, lease: str) -> bool:
        """Let go of a core grant. False when nothing held it."""
        with self._cond:
            return self.cores.pop(lease, None) is not None

    def _counted(self, proc: Mapping[str, Any]) -> None:
        """Hold the memory an unmanaged server takes in the node's table until its process ends, so a start is admitted beside it.
        Its size is counted in steps of 256 MiB, so a server that grows a little is still the same lease."""
        step = 256 * admission.MIB
        mb = -(-int(proc.get("rss") or 0) // step) * step // admission.MIB
        if mb:
            with contextlib.suppress(Refused, OSError):
                self.table.acquire([leasetable.memory(mb)], leasetable.Terms(
                    tag=f"unmanaged.{proc['port']}.{proc['pid']}", pid=int(proc["pid"]), ttl_s=admission.KEEP_S, wait=False))

    @admitted
    def keep_leases(self) -> None:
        """Renew what this broker holds in the node's table: the lease of every live server on the registry (taken again when the
        node holds none) and of every claim."""
        for port, entry in recorded_servers(self.manager.state_file).items():
            if self.alive(entry.get("pid")):
                admission.keep(self.table, port, entry)
        with self._cond:
            leases = [held["lease"] for held in self.claims.values() if held.get("lease")]
        for lease in leases:
            with contextlib.suppress(Refused, OSError):
                self.table.renew(lease, ttl_s=admission.KEEP_S)
