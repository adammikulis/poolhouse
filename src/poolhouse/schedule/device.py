"""A job's condition that the device is free, taken through the machine's broker and never guessed from load.

The job's process asks the broker for the reservation `JOB_CLAIM`, which is granted only while no server is held or loading, no
other work claims the GPU and the node's lease of the GPU is free. A job that measures needs the device to itself, so it also
asks the broker to stop each server nobody holds (the broker refuses one in use) and waits while any model stays resident. The
reservation then keeps every other requester out until the job gives it back or its process ends. Nothing in use is stopped.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from poolhouse.serve import broker_wire
from poolhouse.serve.broker import BrokerError
from poolhouse.serve.broker_holds import GPU_CLAIM, JOB_CLAIM, MODEL_START
from poolhouse.serve.manager import measurement_on_the_card, measurement_said

__all__ = ["Device", "blockers"]


def _in_use(server: dict[str, Any]) -> str:
    """Why a job may not stop the server the broker reports, "" for one of Poolhouse's that nothing holds."""
    port, model = server["port"], server["model"]
    if server.get("unmanaged") or not server.get("ours", True):
        return f"port {port} serves {model}, which Poolhouse did not start"
    if server.get("holders") or server.get("loading"):
        held = ", ".join(f"pid {one['pid']} ({one.get('label') or 'no label'})" for one in server.get("holders") or [])
        return f"port {port} serves {model}, in use by {held or 'a load under way'}"
    return ""


def blockers(*, wire: Any = broker_wire, measuring: Callable[[], Any] = measurement_on_the_card) -> list[str]:
    """What would keep a job from the device were it due now, each named; nothing is taken, started or stopped. A server
    nothing holds is not one: the job stops it."""
    found = []
    try:
        if (held := measuring()) is not None:
            found.append(f"a measurement holds the device: {measurement_said(held)}")
        status = wire.status()
    except (BrokerError, OSError, RuntimeError, ValueError) as why:
        if str(why).startswith("no broker is running"):
            return found
        return [*found, f"the model broker could not be asked: {why}"]
    for name, claim in (status.get("claims") or {}).items():
        if name in (GPU_CLAIM, JOB_CLAIM) or name.startswith(MODEL_START):
            purpose = (claim.get("info") or {}).get("purpose") or "a model start"
            found.append(f"the device is claimed: {purpose} (pid {claim.get('pid') or '?'})")
    return found + [why for server in status.get("servers") or [] if (why := _in_use(server))]


class Device:
    """The device as one job's process takes it and gives it back."""

    def __init__(self, job: str, *, wire: Any = broker_wire, measuring: Callable[[], Any] = measurement_on_the_card,
                 say: Callable[[str], Any] = print) -> None:
        self.job, self.wire, self.measuring, self.say = job, wire, measuring, say

    def take(self, *, alone: bool) -> str:
        """Reserve the device, "" once it is this job's; else why it is not free. ``alone`` also needs no model resident.
        Asking again while it is held keeps it, and takes it again from a broker that restarted."""
        try:
            if (held := self.measuring()) is not None:
                return f"a measurement holds the device: {measurement_said(held)}"
            got = self.wire.claim(JOB_CLAIM, {"purpose": f"scheduled job {self.job}"}, timeout=0.0)
            if not got.get("granted"):
                return f"the device is in use: {(got.get('info') or {}).get('purpose') or 'held'} (pid {got.get('pid') or '?'})"
            if not got.get("reserves_device"):
                self.give_back()
                self.wire.replace_stale()
                return "the running model broker is older than scheduled jobs and has been asked to hand over"
            if alone and (why := self._clear()):
                self.give_back()
                return why
        except (BrokerError, OSError, RuntimeError, ValueError) as why:
            return f"the model broker could not be asked: {why}"
        return ""

    def _clear(self) -> str:
        """Stop each server nobody holds; why the device is still not this job's alone, "" when no server is left."""
        left = []
        for server in self.wire.status()["servers"]:
            port, model = server["port"], server["model"]
            if held := _in_use(server):
                left.append(held)
            elif (done := self.wire.stop(port)).get("stopped"):
                self.say(f"stopped the server on port {port} ({model}): nothing held it, and this job measures the device alone")
            else:
                left.append(f"port {port} serves {model}: {done.get('why', 'it was not stopped')}")
        return "; ".join(left)

    def give_back(self) -> None:
        """Let the reservation go; a broker that is gone holds none."""
        try:
            self.wire.unclaim(JOB_CLAIM)
        except (BrokerError, OSError, RuntimeError, ValueError) as why:
            self.say(f"the device reservation was not given back ({why}); it ends with this process")
