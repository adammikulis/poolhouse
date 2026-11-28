"""The configuration a Strata server is started with on this device: memory, context and engine arguments, by the rules Strata's
setup applies (``terminal/strata_control/mac_fit.py`` and ``setup_config.py`` at the pinned commit)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from poolhouse.serve.backend import ServerFailed
from poolhouse.serve.strata import pins
from poolhouse.serve.strata.prepare import Job, Prepared, mapped_bytes

__all__ = ["CONTEXTS", "Shape", "estimate_bytes", "fitted_context", "shape"]

GIB = 1 << 30
CONTEXTS = (262144, 204800, 131072, 65536, 32768)
"""The context sizes a start fits to, largest first; 262,144 is the length the model was trained at."""
TRAINED = 262144

MAC_OVERHEAD = 12 * GIB
"""Beside the mapped file on a Mac: prompt buffers, the draft layer and the first KV cells (mac_fit.OVERHEAD_GB)."""
MAC_KV_BYTES = 17_700
"""A Mac's KV and recurrent state a context token: 0.65 GB at 36,864 tokens (Strata's docs/MAC.md)."""
MAC_SHARE = 0.75
MAC_BIG_GIB, MAC_BIG_LEAVE, MAC_LEAVE_GIB, MAC_LEAVE = 64.0, 0.30, 7.0, 0.15
MAC_STREAM_MTP_GIB = 96

PC_KV_BYTES = 13_728
"""RAM a streamed 8-bit KV cache takes a context token (setup.kv_streaming_ram_gb)."""
PC_BESIDE_GIB = 10
"""RAM beside the experts (setup.LOW_RAM_HEADROOM_GB)."""
PC_BUDGET_LEFT_GIB = 17
"""RAM left beside a RAM budget of experts (setup.UNSLOTH_BUDGET_LEFT_GB)."""
PC_BUFFERS = 6 * GIB
"""Poolhouse's allowance on a PC for the server and the engine's host buffers, for admission."""


@dataclass(frozen=True, slots=True)
class Shape:
    """The engine arguments and environment of a start, and what to say about the choices behind them."""

    args: tuple[str, ...]
    env: dict[str, str]
    experts_file: bool
    notes: tuple[str, ...]


def mac_budget_bytes(memory: int) -> int:
    """The most a Mac keeps resident for one program: Metal's share, less what macOS keeps (the engine's resident_cap)."""
    leave = max(MAC_LEAVE_GIB * GIB, MAC_LEAVE * memory)
    if memory >= MAC_BIG_GIB * GIB:
        leave = max(leave, MAC_BIG_LEAVE * memory)
    return int(min(memory * MAC_SHARE, memory - leave))


def estimate_bytes(chosen: pins.ModelSet, context: int, kind: str, memory: int) -> int:
    """What serving ``chosen`` at ``context`` takes of this device's memory; a Mac that streams the experts takes its budget."""
    if kind == "metal":
        held = mapped_bytes(chosen) + MAC_OVERHEAD + context * MAC_KV_BYTES
        return min(held, mac_budget_bytes(memory)) if held > mac_budget_bytes(memory) else held
    experts = int(chosen.experts_gb * 1e9)
    return min(experts, max(8 * GIB, memory - PC_BUDGET_LEFT_GIB * GIB)) + context * PC_KV_BYTES + PC_BUFFERS


def fitted_context(chosen: pins.ModelSet, kind: str, memory: int, slots: int = 1) -> int:
    """The largest of `CONTEXTS` (for each of ``slots``) this device holds ``chosen`` at without streaming; the smallest when
    none does."""
    for context in CONTEXTS:
        total = context * max(1, slots)
        if kind == "metal":
            if mapped_bytes(chosen) + MAC_OVERHEAD + total * MAC_KV_BYTES <= mac_budget_bytes(memory):
                return context
        elif memory / GIB >= _pc_stream_need(chosen, total):
            return context
    return CONTEXTS[-1]


def _pc_stream_need(chosen: pins.ModelSet, context: int) -> float:
    """The RAM (GiB) setup wants before it keeps a PC's whole KV cache in RAM (setup.kv_streaming_need_gb)."""
    kv = context * PC_KV_BYTES / 1e9
    if chosen.unsloth:
        return max(16.0, PC_BESIDE_GIB + kv + 4.0)
    return min(chosen.ram_gb + kv + 1, chosen.experts_gb + PC_BESIDE_GIB + kv)


def shape(job: Job, made: Prepared) -> Shape:
    """The engine arguments of one start."""
    if job.kind not in ("metal", "cuda") or not getattr(job.chosen, job.kind):
        raise ServerFailed(f"Strata has no {job.kind} support for {job.chosen.key}")
    profile = job.engine.source / "terminal" / "data" / "expert-profile.bin"
    make = _metal if job.kind == "metal" else _cuda
    return make(job, made, profile)


def _metal(job: Job, made: Prepared, profile: Path) -> Shape:
    chosen, context, memory = job.chosen, job.context, job.memory
    streams = mapped_bytes(chosen) + MAC_OVERHEAD > mac_budget_bytes(memory)
    notes = []
    args = ["--pack", str(made.pack), "--native", str(made.native), "--ple-gguf", str(made.ngram)]
    if made.repacked:
        args += ["--native-dense-gguf", str(made.native)]
    mtp = made.drafter == "mtp"
    if mtp and not streams and chosen.key in ("iq3_s", "q2_0"):
        window = ["--spec", "3", "--spec-min-p", "0.5"]
    else:
        window = ["--spec", "3" if streams and mtp else "2"]
    args += ["--expert-profile", str(profile), "--expert-cache", "64", "--prefill", "auto", *window]
    if streams:
        args += ["--suffix-draft", "0"]
        notes.append("the experts stream from the SSD: this set does not stay in this Mac's memory")
        if memory < MAC_STREAM_MTP_GIB * GIB and mtp:
            notes.append("Strata measured a Mac under 96 GB faster without its draft layer when streaming")
    if mtp:
        args += ["--mtp", str(made.data / "mtp" / "rt")]
    args += ["--max-context", str(context), "--mmap-experts"]
    if context > 32768:
        notes.append("Strata has measured the quality of long contexts on a Mac only to 32,768 tokens")
    return Shape(tuple(args), {"STRATA_METAL_STREAM": "1" if streams else "0"}, False, tuple(notes))


def _cuda(job: Job, made: Prepared, profile: Path) -> Shape:
    chosen, files, context, memory = job.chosen, job.files, job.context, job.memory
    ram = memory / GIB
    experts = chosen.experts_gb * 1e9 / GIB
    notes: list[str] = []
    args = ["--pack", str(made.pack), "--native", str(made.native), *(["--ple-gguf", str(made.ngram)] if len(files) <= 2 else []),
            "--expert-profile", str(profile), "--expert-cache", "auto", "--prefill", "auto", "--spec", "4", "--spec-min-p", "0.5",
            "--mtp", str(made.data / "mtp" / "rt"), "--max-context", str(context)]
    if context > 8192:
        args += ["--kv", "int8"]
    streamed = context >= 65536 and ram >= _pc_stream_need(chosen, context)
    experts_file = False
    if ram < experts + PC_BESIDE_GIB:
        if chosen.unsloth:
            kv = math.ceil(context * PC_KV_BYTES / 1e9) if streamed else 0
            budget = max(8, min(int(experts), math.ceil(ram) - PC_BUDGET_LEFT_GIB - kv))
            args += ["--resident-budget-gib", str(budget)]
            notes.append(f"{budget} GiB of experts stay in RAM and the rest are read from the SSD")
        else:
            args += ["--mmap-experts"]
            experts_file = True
            notes.append("this PC's RAM does not hold the experts: they are read from the model folder as needed")
    if streamed:
        args += ["--kv-resident", "32768"]
    elif context >= 65536:
        notes.append("the KV cache stays in video memory: this PC's RAM has no room to stream it")
    return Shape(tuple(args), {}, experts_file, tuple(notes))
