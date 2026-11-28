"""What a model server asks of this device, and what the node's lease table gives it: memory and a slot for the model.

A server about to start is a lease of the node (`poolhouse.leasetable`) tagged with its port: the memory it will take
(`estimate_bytes`: the weights, the draft and projector files, the KV cache read off the GGUF header, a prompt cache asked for and a
fixed allowance for the runtime) and its slot (`slot_name`). The node holds the machine's budget, the red share of what `poolhouse.hub.room` allows,
queues a start that does not fit beside the servers up, refuses one that could never fit, and ends the lease when the process it
is bound to ends. A server runs on the device ``gpu``, or on ``cpu`` when it offloads no layer or says so. `poolhouse.gate` queues
the requests of a device.
"""

from __future__ import annotations

import os
import struct
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from poolhouse import hub
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
from poolhouse.serve.weights import resident_of
from poolhouse.ui.verdict import THRESHOLDS

__all__ = [
    "ENV_WAIT",
    "KEEP_S",
    "AdmissionRefused",
    "budget_mb",
    "compatible",
    "device_of",
    "differences",
    "entry_mb",
    "estimate_bytes",
    "live",
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
    return resident_of(ref) if ref else 0


def estimate_bytes(spec: ServerSpec) -> int:
    """What serving ``spec`` will take: weights, draft and projector files, the KV cache
    read off the GGUF header, a prompt cache the spec asks for (llama-server's own default,
    when it asks for none, is not counted) and a fixed allowance for the runtime. Weights
    not on disk are unknown and count as 0."""
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
            + draft_kv_estimate_bytes(spec) + max(spec.cache_ram_mb or 0, 0) * MIB + RUNTIME_ALLOWANCE_BYTES)


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


def servings(entry: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The settings a record, or a held server's shape, may be asked for by: as it serves, and, where it was started
    with another draft head than was asked, with the head settings of each such asking (``asked``, ``also``)."""
    others = (entry.get("asked"), *(entry.get("also") or ()))
    return [entry, *({**entry, **one} for one in others if one)]


def as_served(entry: Mapping[str, Any]) -> dict[str, Any]:
    """The head settings the server on ``entry`` is asked for by as it serves."""
    mtp = {"mtp": True} if entry.get("mtp") else {} if entry.get("draft") else {"mtp": False}
    return {"draft": str(entry.get("draft") or ""), "spec_type": str(entry.get("spec_type") or ""), **mtp}


def differences(spec: ServerSpec, entry: Mapping[str, Any], mismatches: list[str]) -> list[str]:
    """Each way the server on ``entry`` differs from ``spec``, in plain words: ``mismatches``
    (from `serving_mismatch`) and then the recorded settings that were asked for and are not
    the server's. Empty when it can serve ``spec`` as it is, by any of its `servings`."""
    found = [_unlike(spec, one) for one in servings(entry)]
    return [*mismatches, *next((one for one in found if not one), found[0])]


def _unlike(spec: ServerSpec, entry: Mapping[str, Any]) -> list[str]:
    """Each recorded setting ``spec`` asks for that ``entry``, read one way, does not have."""
    out: list[str] = []
    if bool(entry.get("embedding")) != bool(spec.embedding):
        out.append("embedding: the server " + ("serves embeddings" if entry.get("embedding") else "does not"))
    if bool(entry.get("reranking")) != bool(spec.reranking):
        out.append("reranking: the server " + ("reranks" if entry.get("reranking") else "does not"))
    if spec.mtp is not None and bool(entry.get("mtp")) is not bool(spec.mtp):
        out.append("draft head: " + ("one was asked for and the server runs none" if spec.mtp
                                     else f"none was asked for and the server runs {entry.get('mtp')}"))
    kept = kvcache.effective(spec)
    for field, asked in (("cache_type_k", kept.k), ("cache_type_v", kept.v),
                         ("spec_type", spec.spec_type)):
        if asked and entry.get(field) != asked:
            out.append(f"{field}: asked for {asked}, the server runs {entry.get(field) or 'none'}")
    for field in ("draft", "chat_template_file"):
        asked = getattr(spec, field)
        loaded = entry.get(field)
        if asked and (not loaded or Path(asked).resolve() != Path(loaded).resolve()):
            out.append(f"{field}: asked for {Path(str(asked)).name}, the server runs "
                       f"{Path(str(loaded)).name if loaded else 'none'}")
    if spec.mmproj and not entry.get("mmproj"):
        out.append("mmproj: a vision projector was asked for and the server runs none")
    return out


def compatible(spec: ServerSpec, entry: Mapping[str, Any], mismatches: list[str]) -> bool:
    """Whether the server on ``entry`` can serve ``spec`` as it is, given the settings
    in which it differs (``mismatches``, from `serving_mismatch`)."""
    return not differences(spec, entry, mismatches)


def tag_of(port: int) -> str:
    """The tag of the lease of the server on ``port``."""
    return f"server.{port}"


def slot_name(model: str | Path, port: int) -> str:
    """The name the model slot of a server has: the model's file name and its port, apart from the names a worker's slot has."""
    return f"serve:{Path(str(model)).name}@{port}"


def entry_mb(entry: Mapping[str, Any]) -> int:
    """The MiB a registry entry's server takes: the estimate it was admitted with, else its weights and the overhead."""
    return int(entry.get("est_mb") or 0) or max(1, round(_size(str(entry.get("model") or "")) * WEIGHTS_OVERHEAD / MIB))
