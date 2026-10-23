"""``poolhouse-serve plan``: where a model's tensors go on a machine, from its GGUF headers
alone -- no server, no GPU -- and the same placement for ``up --plan auto``."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, replace
from pathlib import Path

from poolhouse import hub
from poolhouse.command import flag
from poolhouse.hub.probe import MachineMemory, machine_memory
from poolhouse.kvtype import DEFAULT_KV_CACHE
from poolhouse.log import say, warn
from poolhouse.serve import plan as plan_mod
from poolhouse.serve.kvbudget import CACHE_TYPES, kv_row
from poolhouse.serve.largest import contexts, largest_context, trained_window
from poolhouse.serve.placement import ShardFacts, cache_bytes, facts_of
from poolhouse.serve.plan import DEFAULTS, GIB, Hardware, Plan, Shortfall
from poolhouse.serve.preflight import read_gguf_header
from poolhouse.serve.tensors import shard_paths, tensors_by_shard
from poolhouse.units import human_bytes, span

__all__ = ["OPTIONS", "Ask", "auto_fields", "cmd_plan", "hardware_of", "plan_for", "render"]

OPTIONS = [
    flag("model", help="path to the first .gguf file of the model"),
    flag("--vram", type=float, default=None, metavar="GB",
         help="the GPU's memory, for a machine with a separate card"),
    flag("--ram", type=float, default=None, metavar="GB",
         help="the machine's RAM (default: this machine's)"),
    flag("--unified", type=float, default=None, metavar="GB",
         help="a unified-memory machine (a Mac) where a model may use this much"),
    flag("--disk-read", type=float, default=DEFAULTS["disk_gbps"], metavar="GBPS",
         help="sequential disk read speed, for the paging estimate (default: %(default)s, "
              "an assumption)"),
    flag("--ram-gbps", type=float, default=DEFAULTS["ram_gbps"], metavar="GBPS",
         help="memory bandwidth, for the estimates (default: %(default)s, an assumption)"),
    flag("--vram-gbps", type=float, default=DEFAULTS["vram_gbps"], metavar="GBPS",
         help="the card's memory bandwidth (default: %(default)s, an assumption)"),
    flag("--context", type=int, default=0, metavar="TOKENS",
         help="tokens across all slots the cache is sized for (default: the most this "
              "placement holds on the machine, up to the model's trained window)"),
    flag("--parallel", type=int, default=1, metavar="N", help="slots (default: 1)"),
    flag("--cache-type-k", default=DEFAULT_KV_CACHE, choices=CACHE_TYPES,
         help="how the K cache is stored (default: %(default)s). Quantised K costs more "
              "quality than quantised V"),
    flag("--cache-type-v", default=DEFAULT_KV_CACHE, choices=CACHE_TYPES,
         help="how the V cache is stored (default: %(default)s); q4_0 here is the cheaper "
              "of the two compromises"),
    flag("--compute", type=float, default=DEFAULTS["compute_buffers"] / GIB, metavar="GB",
         help="headroom for llama.cpp's compute buffers and runtime (default: %(default)s, "
              "an assumption; the measured Metal buffers were 0.38 GB at 32k and 0.55 GB "
              "at 64k tokens)"),
    flag("--ubatch", type=int, default=DEFAULTS["ubatch"], metavar="N",
         help="prompt micro-batch for the prompt-time estimate (default: %(default)s)"),
    flag("--tflops", type=float, default=DEFAULTS["tflops"], metavar="T",
         help="sustained compute for the prompt-time estimate (default: %(default)s, an "
              "assumption)"),
    flag("--json", action="store_true", help="print one JSON object instead of the listing"),
]


def _gb(value: float | None) -> int:
    return int(value * GIB) if value else 0


def hardware_of(args: argparse.Namespace, found: MachineMemory | None = None) -> Hardware:
    """The machine the flags describe, filling what they leave out from this machine."""
    rates = {"ram_gbps": float(args.ram_gbps), "vram_gbps": float(args.vram_gbps),
             "disk_gbps": float(args.disk_read)}
    given = any(v is not None for v in (args.vram, args.ram, args.unified))
    here = found if found is not None else (None if given else machine_memory())
    ram = _gb(args.ram) or (here.total_ram if here else 0)
    if args.unified is not None or (here is not None and here.unified and args.vram is None):
        pool = _gb(args.unified) or (here.gpu_limit_bytes if here else 0) or hub.room()
        return Hardware("unified", pool, ram or pool, **rates)
    if args.vram is not None:
        return Hardware("discrete", _gb(args.vram), ram, **rates)
    if here is not None and here.gpus:
        card = max(here.gpus, key=lambda g: g.free_bytes)
        return Hardware("discrete", card.free_bytes, ram, **rates)
    return Hardware("cpu", 0, ram, **rates)


@dataclass(frozen=True, slots=True)
class Ask:
    """What the plan is sized for: the context across all slots (0 for the most the placement
    holds), the slots, the K and V cache types, the compute-buffer headroom in bytes, and the micro-batch and TFLOPS the
    prompt-time estimate uses."""

    context: int = 0
    parallel: int = 1
    cache: tuple[str, str] = (DEFAULT_KV_CACHE, DEFAULT_KV_CACHE)
    compute: int = DEFAULTS["compute_buffers"]
    ubatch: int = DEFAULTS["ubatch"]
    tflops: float = DEFAULTS["tflops"]


def plan_for(model: str | Path, hw: Hardware, ask: Ask = Ask()) -> Plan:  # noqa: B008
    """The placement of ``model``, read from its headers, on ``hw``. Raises `Shortfall`.

    A context of 0 is the largest one the placement holds, found by the same search every
    default uses (`largest.largest_context`) over this placement's own memory model."""
    first = shard_paths(model)[0]
    meta = read_gguf_header(first)
    if ask.context <= 0:
        ask = replace(ask, context=_largest_context(model, hw, ask, meta))
    facts = facts_of(tensors_by_shard(model), meta, Path(str(model)).name)
    kv = cache_bytes(meta, ask.context, ask.cache[0], ask.cache[1], ask.parallel)
    plan = plan_mod.place(facts, hw, context=ask.context, kv_bytes=kv, allowance=ask.compute)
    rows = tuple(kv_row(meta, kind, kind) for kind in CACHE_TYPES)
    chosen = kv_row(meta, ask.cache[0], ask.cache[1])
    rows = (chosen, *(r for r in rows if (r.cache_k, r.cache_v) != ask.cache))
    more = plan_mod.prompt_estimates(plan, ask.context, ask.ubatch, ask.tflops)
    return replace(plan, estimates=(*plan.estimates, *more), kv_rows=rows)


def _largest_context(model: str | Path, hw: Hardware, ask: Ask, meta: dict) -> int:
    """The most tokens across all slots that ``hw`` places ``model`` at, per slot up to the
    model's trained window. When not even the smallest holds, that smallest, so the caller
    raises the shortfall with its numbers."""
    slots = max(1, ask.parallel)
    steps = contexts(trained_window(meta))

    def holds(each: int) -> bool:
        try:
            plan_for(model, hw, replace(ask, context=each * slots))
        except Shortfall:
            return False
        return True

    return (largest_context(holds, steps) or steps[-1]) * slots


def _shard_kind(shard: ShardFacts) -> str:
    if not shard.roles:
        return "metadata only"
    if shard.holds_table:
        return (f"holds the n-gram table, {human_bytes(shard.roles['table'])} served from disk, "
                "mmapped")
    return "resident"


def _shard_lines(plan: Plan) -> list[str]:
    lines = []
    for shard in plan.facts.shards:
        lines.append(f"    {human_bytes(shard.bytes):>8}  {shard.name}  [{_shard_kind(shard)}]")
        parts = ", ".join(f"{r} {human_bytes(b)}" for r, b in sorted(
            shard.roles.items(), key=lambda kv: -kv[1]) if b)
        if parts:
            lines.append(f"              {parts}")
    return lines


def _machine_line(plan: Plan) -> str:
    hw = plan.hardware
    if hw.kind == "unified":
        return f"unified memory, a model may use {human_bytes(hw.fast_bytes)}"
    if hw.kind == "discrete":
        return f"a card with {human_bytes(hw.fast_bytes)} and {human_bytes(hw.ram_bytes)} of RAM"
    return f"CPU only, {human_bytes(hw.ram_bytes)} of RAM"


def _tier_lines(plan: Plan) -> list[str]:
    facts, hw = plan.facts, plan.hardware
    names = {"discrete": "card", "unified": "memory pool", "cpu": "-"}
    out = []
    if hw.kind != "cpu":
        out.append(f"    {names[hw.kind]:<12} {human_bytes(plan.fast_resident):>8}  "
                   f"always-needed {human_bytes(facts.always_bytes)} + experts "
                   f"{human_bytes(plan.fast_experts)} + KV cache {human_bytes(plan.kv_bytes)} "
                   f"at {plan.context:,} tokens + runtime {human_bytes(plan.allowance)}")
    if hw.kind != "unified":
        out.append(f"    {'RAM':<12} {human_bytes(plan.ram_resident):>8}  experts held "
                   f"{human_bytes(plan.host_in_ram)} of {human_bytes(plan.host_experts)} "
                   "on the CPU side")
    out.append(f"    {'disk':<12} {human_bytes(plan.disk_bytes):>8}  n-gram table "
               f"{human_bytes(facts.table)} (mmapped; its pages enter the page cache as rows "
               f"are read) + experts paged {human_bytes(plan.paged)}")
    return out


def _kv_lines(plan: Plan) -> list[str]:
    if not plan.kv_rows:
        return []
    rows = plan.kv_rows
    lines = ["", f"  caches at {plan.context:,} tokens (from the header: "
             f"{rows[0].layers} layers hold K/V, {rows[0].heads} KV heads x "
             f"{rows[0].key_dim}/{rows[0].value_dim} K/V dims; the other layers keep a "
             f"recurrent state of {human_bytes(rows[0].recurrent_per_seq)} a sequence)"]
    for row in rows:
        mark = "  <- planned" if row is rows[0] else ""
        lines.append(f"    K {row.cache_k:<5} V {row.cache_v:<5} {row.kv_per_token:>7,} B/token K/V"
                     f" + {row.indexer_per_token:>7,} B/token indexer = "
                     f"{human_bytes(row.total(plan.context))}{mark}")
    if rows[0].indexer_per_token:
        lines.append(f"    the indexer cache is what llama.cpp allocates (the size of the K/V "
                     f"cache, measured: docs/architectures/qwen4exp.md); the header alone implies "
                     f"at least {rows[0].indexer_floor_per_token:,} B/token (one shared "
                     "key head)")
    lines.append("    quantising K costs more quality than quantising V: q8_0 for both is the "
                 "default, and V can drop to q4_0 before K does")
    return lines


def render(plan: Plan) -> str:
    """The placement as the listing `plan` prints."""
    facts = plan.facts
    lines = [f"{facts.name}", f"  machine: {_machine_line(plan)}",
             f"  model: {facts.layers} layers, {facts.experts} experts per layer "
             f"({facts.used} used a token), {len(facts.shards)} shard(s)", "",
             "  shards"]
    lines += _shard_lines(plan)
    lines += ["", "  expected resident (bytes from the headers; the split is the plan)"]
    lines += _tier_lines(plan)
    lines += _kv_lines(plan)
    lines += ["", "  why"] + [f"    {why}" for why in plan.reasons]
    lines += ["", "  set these ServerSpec fields (poolhouse-serve up ... or up --plan auto)"]
    fields = plan.spec_fields()
    lines.append("    " + (", ".join(f"{k}={v!r}" for k, v in fields.items())
                           if fields else "none"))
    explicit = plan.explicit_override()
    if explicit:
        lines.append("    as flags: " + " ".join(f"--override-tensor '{p}'" for p in explicit))
    if plan.n_cpu_moe or plan.cpu_moe:
        lines.append("    or: " + ("--cpu-moe" if plan.cpu_moe else f"--n-cpu-moe {plan.n_cpu_moe}"))
    lines += ["", "  ESTIMATES (from the headers and the assumed bandwidths; not measured)"]
    for one in plan.estimates:
        shown = (human_bytes(one.value) if one.unit == "B" else
                 f"{one.value:,.0f} s ({span(one.value)})" if one.unit == "s" else f"{one.value:,.2f} {one.unit}")
        lines += [f"    {one.name}: {shown}", f"      = {one.formula}"]
    hw = plan.hardware
    lines.append(f"    assumed: RAM {hw.ram_gbps:g} GB/s, card {hw.vram_gbps:g} GB/s, "
                 f"disk {hw.disk_gbps:g} GB/s, RAM reserve "
                 f"{human_bytes(DEFAULTS['ram_reserve'])}, expert use uniform across experts; "
                 "memory bandwidth only, compute and PCIe transfers are not modelled")
    lines.append("  prompt processing is the practical cost of a long coding context; a third "
                 "party engine claims ~1.3k tok/s at 4K tokens on a 12 GB card (a claim, "
                 "not measured here)")
    lines += [f"  note: {note}" for note in facts.notes]
    return "\n".join(lines)


def _as_dict(plan: Plan) -> dict[str, object]:
    return {"model": plan.facts.name, "machine": plan.hardware.kind,
            "spec_fields": plan.spec_fields(), "explicit_override": plan.explicit_override(),
            "fast_resident": plan.fast_resident, "ram_resident": plan.ram_resident,
            "disk": plan.disk_bytes, "paged": plan.paged, "kv_bytes": plan.kv_bytes,
            "shards": [{"name": s.name, "bytes": s.bytes, "roles": dict(s.roles),
                        "holds_table": s.holds_table} for s in plan.facts.shards],
            "estimates": [{"name": e.name, "value": e.value, "unit": e.unit,
                           "formula": e.formula, "measured": False} for e in plan.estimates],
            "reasons": list(plan.reasons)}


def cmd_plan(args: argparse.Namespace) -> int:
    """``poolhouse-serve plan MODEL`` -- the placement for a machine, from headers alone."""
    model = str(hub.located(args.model) or args.model)
    try:
        plan = plan_for(model, hardware_of(args), Ask(
            args.context, args.parallel, (args.cache_type_k, args.cache_type_v),
            _gb(args.compute), args.ubatch, args.tflops))
    except Shortfall as no:
        for line in no.lines:
            warn(line)
        return 2
    except (OSError, ValueError) as exc:
        warn(f"error: {exc}")
        return 2
    say(json.dumps(_as_dict(plan), indent=2) if args.json else render(plan))
    return 0


def auto_fields(model: str, *, context: int, parallel: int, cache: tuple[str, str],
                found: MachineMemory | None = None) -> tuple[Plan, list[str]]:
    """The plan for this machine and the lines that say what was chosen. Raises `Shortfall`."""
    here = found if found is not None else machine_memory()
    args = argparse.Namespace(vram=None, ram=None, unified=None, ram_gbps=DEFAULTS["ram_gbps"],
                              vram_gbps=DEFAULTS["vram_gbps"], disk_read=DEFAULTS["disk_gbps"])
    plan = plan_for(model, hardware_of(args, here),
                    Ask(context, parallel, (cache[0] or DEFAULT_KV_CACHE, cache[1] or DEFAULT_KV_CACHE)))
    fields = plan.spec_fields()
    lines = [f"plan auto: {_machine_line(plan)}, {plan.context:,} tokens of context",
             "  placement: " + (", ".join(f"{k}={v!r}" for k, v in fields.items())
                                if fields else "none needed"), *(f"  {why}" for why in plan.reasons)]
    return plan, lines
