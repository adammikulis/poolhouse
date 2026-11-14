"""The node's lease table as the memory a model server asks for: the lease of a start, its renewal, and what the table says is free.

`poolhouse.serve.admission` sizes a server; this is the part of admitting one that talks to the node, so a program that only
sizes and admits against the machine's server file (`poolhouse.core`) never loads the node's client.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from poolhouse import leasetable
from poolhouse.node_health import Refused
from poolhouse.serve.admission import (
    KEEP_S,
    MIB,
    AdmissionRefused,
    budget_mb,
    entry_mb,
    slot_name,
    tag_of,
)
from poolhouse.serve.backend import ServerSpec

__all__ = ["ask", "committed", "free_bytes", "keep", "said"]


def ask(table: leasetable.Table, spec: ServerSpec, est_bytes: int, *, pid: int, ttl_s: int) -> dict[str, Any]:
    """The lease of the memory and the slot a server of ``spec`` needs, bound to the process ``pid`` and kept ``ttl_s`` seconds
    between renewals; `AdmissionRefused` when the node says it can never fit or takes no work."""
    table.budget(budget_mb())
    resources = [leasetable.memory(-(-est_bytes // MIB)),
                 leasetable.slot(slot_name(spec.model, spec.port), context=int(spec.context),
                                 parallel=int(spec.parallel or 1), draft=Path(str(spec.draft or "")).name)]
    try:
        return table.acquire(resources, leasetable.Terms(tag=tag_of(spec.port), pid=pid, ttl_s=ttl_s))
    except Refused as why:
        raise AdmissionRefused(f"port {spec.port}: {why}") from why


def keep(table: leasetable.Table, port: int, entry: Mapping[str, Any]) -> None:
    """Renew the lease of the live server on ``port`` that ``entry`` records; one the node no longer holds is taken again, bound to
    the server's process, when the budget has room for it."""
    lease, pid, model = str(entry.get("lease") or ""), int(entry.get("pid") or 0), str(entry.get("model") or "")
    if lease:
        try:
            table.renew(lease, ttl_s=KEEP_S)
            return
        except Refused:
            pass
    mb = entry_mb(entry)
    slot = leasetable.slot(slot_name(model, port), context=int(entry.get("context") or 0), parallel=int(entry.get("parallel") or 1),
                           draft=Path(str(entry.get("draft") or "")).name)
    with contextlib.suppress(Refused):
        table.acquire([leasetable.memory(mb), slot], leasetable.Terms(tag=tag_of(port), pid=pid, ttl_s=KEEP_S, wait=False))


def committed(listing: Mapping[str, Any]) -> tuple[int, list[str]]:
    """The MiB the held leases of a lease listing take and a line for each that takes some."""
    total, lines = 0, []
    for one in listing["leases"]:
        mb = sum(int(r["mb"]) for r in one["resources"] if r["type"] == "memory_mb")
        if one["state"] != "held" or not mb:
            continue
        total += mb
        named = next((str(r["model"]) for r in one["resources"] if r["type"] == "model_slot"), one.get("tag") or "another lease")
        lines.append(f"{named} {mb / 1024:.1f} GiB")
    return total, lines


def free_bytes(table: leasetable.Table) -> int | None:
    """The bytes of the memory budget no lease holds, or None when there is no budget."""
    listing = table.listing()
    budget = int(listing["config"]["memory_mb"])
    return max(0, budget - committed(listing)[0]) * MIB if budget else None


def said(table: leasetable.Table, est_bytes: int) -> str:
    """Why a start waits for memory, in words."""
    listing = table.listing()
    total, lines = committed(listing)
    budget = int(listing["config"]["memory_mb"])
    gib = 1024 ** 3
    return (f"this server needs {est_bytes / gib:.1f} GiB beside {total / 1024:.1f} GiB already taken, of the "
            f"{budget / 1024:.1f} GiB this machine allows ({'; '.join(lines) or 'nothing else'})")
