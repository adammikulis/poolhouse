"""What a model server asks of this device, and what the node's lease table gives it: memory and a slot for the model.

A server about to start is a lease of the node (`poolhouse.leasetable`) tagged with its port: the memory it will take
(`estimate_bytes`: the weights, the draft and projector files, the KV cache read off the GGUF header and a fixed allowance for the
runtime) and its slot (`slot_name`). The node holds the machine's budget, the red share of what `poolhouse.hub.room` allows,
queues a start that does not fit beside the servers up, refuses one that could never fit, and ends the lease when the process it
is bound to ends. A server runs on the device ``gpu``, or on ``cpu`` when it offloads no layer or says so. `poolhouse.gate` queues
the requests of a device.
"""

from __future__ import annotations

import contextlib
import os
import struct
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from poolhouse import hub, leasetable
from poolhouse.node_health import Refused
from poolhouse.serve import kvcache
from poolhouse.serve.backend import ServerFailed, ServerSpec
from poolhouse.serve.preflight import (
    RUNTIME_ALLOWANCE_BYTES,
    _kv_estimate_bytes,
    draft_kv_estimate_bytes,
    read_gguf_header,
    recurrent_state_bytes,
)
from poolhouse.serve.process import pid_exists
from poolhouse.serve.weights import weight_of
from poolhouse.ui.verdict import THRESHOLDS

__all__ = [
    "ENV_WAIT",
    "KEEP_S",
    "AdmissionRefused",
    "ask",
    "budget_mb",
    "committed",
    "compatible",
    "device_of",
    "estimate_bytes",
    "free_bytes",
    "keep",
    "live",
    "said",
    "slot_name",
    "tag_of",
    "wait_s",
]

ENV_WAIT = "POOLHOUSE_ADMISSION_WAIT_S"
"""Seconds a start waits for memory to come free before it is refused."""

DEFAULT_WAIT_S = 60.0
WEIGHTS_OVERHEAD = 1.1
"""Share of a model's weights taken on top of them for a server found running with no estimate recorded."""
KEEP_S = 120
"""How long the lease of a server lasts without a renewal."""
MIB = 1 << 20


class AdmissionRefused(ServerFailed):
    """The servers up, and this one, would not fit in the memory this machine allows."""


def wait_s() -> float:
    """How long a start waits for memory."""
    try:
        return float(os.environ.get(ENV_WAIT) or DEFAULT_WAIT_S)
    except ValueError:
        return DEFAULT_WAIT_S


def budget_mb() -> int:
    """The memory the node admits servers against: the red share of what this machine allows a model, in MiB; 0 when that is not known."""
    return int(hub.room() / MIB * THRESHOLDS.red_at)


def device_of(settings: ServerSpec | Mapping[str, Any]) -> str:
    """The device, ``cpu`` or ``gpu``, a spec or its settings as a dict compute on: the
    ``device`` given, else ``cpu`` when no layer is offloaded. Raises ``ValueError`` on a
    device that is neither or that disagrees with ``n_gpu_layers``."""
    get = settings.get if isinstance(settings, Mapping) else lambda k, d=None: getattr(settings, k, d)
    given = str(get("device") or "").strip().lower()
    none_offloaded = str(get("n_gpu_layers") if get("n_gpu_layers") is not None else "auto").strip() == "0"
    if given not in ("", "cpu", "gpu"):
        raise ValueError(f"device is cpu or gpu, not {given!r}")
    if given == "gpu" and none_offloaded:
        raise ValueError("device gpu with n_gpu_layers 0")
    return given or ("cpu" if none_offloaded else "gpu")


def _size(ref: str | Path | None) -> int:
    return weight_of(ref) if ref else 0


def estimate_bytes(spec: ServerSpec) -> int:
    """What serving ``spec`` will take: weights, draft and projector files, the KV cache
    read off the GGUF header, and a fixed allowance for the runtime. Weights not on disk
    are unknown and count as 0."""
    weights = _size(spec.model)
    if not weights:
        return 0
    kv = 0
    try:
        meta = read_gguf_header(Path(str(spec.model)))
        kept = kvcache.effective(spec)
        kv = _kv_estimate_bytes(meta, int(spec.context), kept.k or "f16", kept.v or "f16")
        kv += recurrent_state_bytes(meta) * max(1, spec.parallel)
    except (OSError, ValueError, struct.error):
        kv = 0
    return (weights + _size(spec.draft) + _size(spec.mmproj) + kv
            + draft_kv_estimate_bytes(spec) + RUNTIME_ALLOWANCE_BYTES)


def live(entry: Mapping[str, Any]) -> bool:
    """Whether a registry entry is a server that is up or about to be: its process exists,
    or it is a start in progress whose owner exists."""
    pid, owner = entry.get("pid"), entry.get("owner_pid")
    if isinstance(pid, int):
        return pid_exists(pid)
    return bool(entry.get("pending")) and isinstance(owner, int) and pid_exists(owner)


def pending_serves(spec: ServerSpec, entry: Mapping[str, Any]) -> bool:
    """Whether the server being started on ``entry`` will serve ``spec`` when it is up."""
    held = max(1, int(entry.get("parallel") or 1))
    return (int(entry.get("context") or 0) // held >= int(spec.context) // max(1, int(spec.parallel or 1))
            and held >= max(1, int(spec.parallel or 1))
            and bool(entry.get("embedding")) == bool(spec.embedding)
            and bool(entry.get("reranking")) == bool(spec.reranking)
            and not (spec.mmproj and not entry.get("mmproj")))


def compatible(spec: ServerSpec, entry: Mapping[str, Any], mismatches: list[str]) -> bool:
    """Whether the server on ``entry`` can serve ``spec`` as it is, given the settings
    in which it differs (``mismatches``, from `serving_mismatch`)."""
    if mismatches:
        return False
    if bool(entry.get("embedding")) != bool(spec.embedding):
        return False
    if bool(entry.get("reranking")) != bool(spec.reranking):
        return False
    if spec.mtp is not None and entry.get("mtp") is not spec.mtp:
        return False
    kept = kvcache.effective(spec)
    for field, asked in (("cache_type_k", kept.k), ("cache_type_v", kept.v),
                         ("spec_type", spec.spec_type)):
        if asked and entry.get(field) != asked:
            return False
    for field in ("draft", "chat_template_file"):
        asked = getattr(spec, field)
        loaded = entry.get(field)
        if asked and (not loaded or Path(asked).resolve() != Path(loaded).resolve()):
            return False
    return not (spec.mmproj and not entry.get("mmproj"))


def tag_of(port: int) -> str:
    """The tag of the lease of the server on ``port``."""
    return f"server.{port}"


def slot_name(model: str | Path, port: int) -> str:
    """The name the model slot of a server has: the model's file name and its port, apart from the names a worker's slot has."""
    return f"serve:{Path(str(model)).name}@{port}"


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
    mb = int(entry.get("est_mb") or 0) or max(1, round(_size(model) * WEIGHTS_OVERHEAD / MIB))
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
