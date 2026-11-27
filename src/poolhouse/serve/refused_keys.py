"""A recorded server that refuses this build's key, such as one an earlier build left up with its key kept where this build does not read.

Its health route answers and nothing else does, so it is never shared or taken as compatible. Before a start or a lease, the broker settles
each one that serves the model asked for or sits on the port asked for: one nothing live holds but itself, with no request on it, is stopped so
that a new server replaces it; one still held, or still answering somebody, fails the launch at once when it serves the model asked for, naming
the lease. Only a process the record proves is the server this machine started, listening on that port, is ever stopped.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from poolhouse.client.health import refuses_key
from poolhouse.serve.backend import ServerFailed, ServerSpec
from poolhouse.serve.leases import recorded_servers, same_process
from poolhouse.serve.matching import model_matches
from poolhouse.serve.ports import DEFAULT_HOST
from poolhouse.serve.process import kill_process_tree, listens_on, pid_exists, request_on

if TYPE_CHECKING:
    from poolhouse.serve.broker import Broker

__all__ = ["settle"]

_ONE_AT_A_TIME = threading.Lock()


def settle(broker: Broker, spec: ServerSpec, say: Callable[[str], Any]) -> None:
    """Replace, or leave unshared, every server in the way of ``spec`` that refuses this build's key; `ServerFailed` when one that serves its
    model is still in use."""
    with _ONE_AT_A_TIME:   # a second asker reads the records after the first has replaced a server, and never stops what took its port
        for port, entry in recorded_servers(broker.manager.state_file).items():
            serves = (bool(entry.get("embedding")) == bool(spec.embedding)
                      and model_matches(str(entry.get("model") or ""), str(spec.model)))
            pid = entry.get("pid")
            if (not (serves or port == spec.port) or entry.get("pending") or not isinstance(pid, int) or not pid_exists(pid)
                    or not refuses_key(f"http://{DEFAULT_HOST}:{port}")):
                continue
            if entry.get("unmanaged") or not (same_process(entry, strict=True) and listens_on(pid, port)):
                with broker._cond:
                    broker.servers.pop(port, None)
                say(f"port {port}: the server there refuses this build's key and is not shown to be one Poolhouse started; it is left alone")
            elif not (why := _in_use(broker, port, entry)):
                _stop(broker, port, entry)
                say(f"port {port}: replaced the server (pid {pid}) an earlier session left up: it refuses this build's key, nothing holds it "
                    "and no request is on it")
            elif serves:
                raise ServerFailed(f"port {port}: the server up there (pid {pid}) refuses this build's key, so nothing can be asked of it, "
                                   f"and {why}. It is left running, and the next launch after that ends replaces it.")


def _in_use(broker: Broker, port: int, entry: dict) -> str:
    """Who or what still uses the server on ``port``, in words; empty when only the server holds itself and no request is on it."""
    pid, owner = entry["pid"], entry.get("owner_pid")
    with broker._cond:
        known = broker.servers.get(port)
        holders = dict(known.holders) if known else {}
    for lease, who in (broker._read_held().get(port, {}).get("holders") or {}).items():
        holders.setdefault(lease, (int(who[0]), str(who[1])))
    live = {lease: who for lease, who in holders.items() if who[0] != pid and broker.alive(who[0])}
    if not live and isinstance(owner, int) and owner not in (pid, os.getpid()) and broker.alive(owner):
        live = {str(entry.get("lease") or f"recorded-{port}"): (owner, "recorded owner")}
    if live:
        return "it is still held by " + ", ".join(f"lease {lease} of pid {who}" + (f" ({label})" if label else "")
                                                  for lease, (who, label) in live.items())
    busy = request_on(pid, port)
    if busy is None:
        return "whether a request is on it cannot be read"
    return "a request is in progress on it" if busy else ""


def _stop(broker: Broker, port: int, entry: dict) -> None:
    """Stop the server on ``port``, which `settle` has shown to be ours and unused, and take it off every record."""
    kill_process_tree(int(entry["pid"]))
    if pid_exists(entry["pid"]):
        raise ServerFailed(f"port {port}: the server (pid {entry['pid']}) refuses this build's key and did not stop")
    with broker._cond:
        broker.servers.pop(port, None)
    broker.manager._given_back(entry)
    broker.manager._forget(port)
