"""Where a model's tensors go on one machine, and what that is expected to cost.

`place` takes a model's header facts (`poolhouse.serve.placement`) and a `Hardware` and
returns a `Plan`: the always-needed tensors and the KV cache on the fast device, the routed
experts that do not fit there on the CPU side (`--n-cpu-moe` / `--cpu-moe`), the n-gram
table mmapped from disk, and the `ServerSpec` fields that say so. Every throughput figure
is an `Estimate` that carries its formula; nothing here is a measurement.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from poolhouse.serve.kvbudget import KvRow
from poolhouse.serve.placement import ModelFacts
from poolhouse.serve.preflight import RUNTIME_ALLOWANCE_BYTES
from poolhouse.units import human_bytes

__all__ = ["DEFAULTS", "Estimate", "Hardware", "Plan", "Shortfall", "place"]

GIB = 1024 ** 3
# Bandwidths and reserves the estimates use unless the caller names its own. They are
# assumptions, printed beside every figure they feed.
DEFAULTS = {"ram_gbps": 50.0, "vram_gbps": 300.0, "disk_gbps": 3.0,
            "ram_reserve": 4 * GIB, "device_reserve": GIB // 2, "compute_buffers": 2 * GIB,
            "tflops": 20.0, "ubatch": 512}
PAGE = 4096


class Shortfall(Exception):
    """Nothing fits: ``lines`` say what is needed, what there is, and by how much it is short."""

    def __init__(self, *lines: str) -> None:
        super().__init__(lines[0] if lines else "")
        self.lines = lines


@dataclass(frozen=True, slots=True)
class Hardware:
    """One machine's memory: ``unified`` (one pool), ``discrete`` (a card and host RAM) or ``cpu``."""

    kind: str
    fast_bytes: int
    ram_bytes: int
    ram_gbps: float = DEFAULTS["ram_gbps"]
    vram_gbps: float = DEFAULTS["vram_gbps"]
    disk_gbps: float = DEFAULTS["disk_gbps"]


@dataclass(frozen=True, slots=True)
class Estimate:
    """A figure derived by a formula, never measured."""

    name: str
    value: float
    unit: str
    formula: str


@dataclass(frozen=True, slots=True)
class Plan:
    """The placement of one model on one machine."""

    facts: ModelFacts
    hardware: Hardware
    context: int
    kv_bytes: int
    allowance: int
    n_cpu_moe: int
    cpu_moe: bool
    override_tensor: tuple[str, ...]
    n_gpu_layers: int | None
    fast_experts: int
    host_experts: int
    host_in_ram: int
    paged: int
    estimates: tuple[Estimate, ...]
    reasons: tuple[str, ...]
    kv_rows: tuple[KvRow, ...] = ()

    @property
    def fast_resident(self) -> int:
        """Bytes in the fast memory: always-needed tensors, GPU experts, KV cache, runtime."""
        if self.hardware.kind == "cpu":
            return 0
        return self.facts.always_bytes + self.fast_experts + self.kv_bytes + self.allowance

    @property
    def ram_resident(self) -> int:
        """Bytes expected in host RAM: the CPU-side experts that fit, plus the CPU-only machine's own load."""
        extra = (self.facts.always_bytes + self.kv_bytes + self.allowance
                 if self.hardware.kind == "cpu" else 0)
        return self.host_in_ram + extra

    @property
    def disk_bytes(self) -> int:
        """Bytes read from disk: the n-gram table and the experts that do not fit in RAM."""
        return self.facts.table + self.paged

    def explicit_override(self) -> tuple[str, ...]:
        """`override_tensor` written out, with the experts of the moved layers as a pattern in
        place of ``--n-cpu-moe``."""
        moved = len(self.facts.expert_layer_bytes) if self.cpu_moe else self.n_cpu_moe
        return (*self.override_tensor, *((_layers_regex(moved),) if moved else ()))

    def spec_fields(self) -> dict[str, object]:
        """The `ServerSpec` fields this placement sets."""
        out: dict[str, object] = {}
        if self.override_tensor:
            out["override_tensor"] = self.override_tensor
        if self.cpu_moe:
            out["cpu_moe"] = True
        elif self.n_cpu_moe:
            out["n_cpu_moe"] = self.n_cpu_moe
        if self.n_gpu_layers is not None:
            out["n_gpu_layers"] = self.n_gpu_layers
        return out


def _fewest_moved(per_layer: Sequence[int], room: int) -> int:
    """How many leading layers' experts move to the CPU so the rest fit in ``room``."""
    for moved in range(len(per_layer) + 1):
        if sum(per_layer[moved:]) <= room:
            return moved
    return len(per_layer)


def _layers_regex(moved: int) -> str:
    layers = "|".join(str(n) for n in range(moved))
    return rf"blk\.({layers})\.ffn_.*_exps\.=CPU"


def _need(facts: ModelFacts, caches: tuple[int, int]) -> str:
    return (f"always-needed tensors {human_bytes(facts.always_bytes)} + KV cache "
            f"{human_bytes(caches[0])} + runtime {human_bytes(caches[1])}")


def _short(facts: ModelFacts, caches: tuple[int, int], have: int, where: str,
           context: int) -> Shortfall:
    need = facts.always_bytes + sum(caches)
    return Shortfall(
        f"refused: {facts.name} needs {human_bytes(need)} in {where} before any routed expert "
        f"is placed ({_need(facts, caches)}), and there is {human_bytes(have)}: short by "
        f"{human_bytes(need - have)}",
        f"  a shorter context lowers the KV cache ({context:,} tokens now); a larger card, or "
        f"{human_bytes(need - have)} more memory, is the other way; `plan MODEL --ram N` "
        "plans a CPU-only placement")


def _times(fast_read: int, ram_read: float, disk_read: float, hw: Hardware) -> list[Estimate]:
    """Tokens a second as ``1 / max(tier times)`` with the tiers overlapped and ``1 / sum`` serial."""
    gb = 1e9
    parts = [("fast", fast_read, hw.vram_gbps if hw.kind == "discrete" else hw.ram_gbps),
             ("RAM", ram_read, hw.ram_gbps), ("disk", disk_read, hw.disk_gbps)]
    times = [read / (rate * gb) for _n, read, rate in parts if read > 0]
    shown = " + ".join(f"{human_bytes(r)}/{g:g}GB/s" for _n, r, g in parts if r > 0)
    if not times:
        return []
    return [Estimate("tokens/s upper bound, tiers overlapped", 1 / max(times), "tok/s",
                     f"1 / max over tiers of (bytes read per token / bandwidth): {shown}"),
            Estimate("tokens/s upper bound, tiers in series", 1 / sum(times), "tok/s",
                     f"1 / sum over tiers of (bytes read per token / bandwidth): {shown}")]


def _table_estimates(facts: ModelFacts) -> list[Estimate]:
    if not facts.table:
        return []
    rows, row = facts.table_rows_per_token, facts.table_row_bytes
    return [Estimate("n-gram rows gathered per token", rows, "rows",
                     "len(ple.head_vocab_sizes), else (ple.ngram_size - 1) * ple.heads_per_ngram"),
            Estimate("n-gram bytes used per token", rows * row, "B",
                     f"rows per token * table bytes / table rows = {rows} * {row}"),
            Estimate("n-gram bytes paged in per token, every row a cold page", rows * PAGE, "B",
                     f"rows per token * {PAGE}-byte page: bound while the page cache is cold")]


def _expert_estimates(facts: ModelFacts) -> list[Estimate]:
    if not facts.experts:
        return []
    total = sum(facts.expert_read(layer) for layer in range(len(facts.expert_layer_bytes)))
    return [Estimate("routed-expert bytes read per token", total, "B",
                     f"sum over layers of layer expert bytes * {facts.used} used / "
                     f"{facts.experts} experts, at the tensor types in the header")]


@dataclass(frozen=True, slots=True)
class Layout:
    """How the experts divide: the first ``moved`` layers' on the host (``in_ram`` of it held,
    ``paged`` read from disk), and ``fast`` bytes of the rest on the fast side."""

    moved: int
    fast: int
    in_ram: int
    paged: int
    overrides: tuple[str, ...]
    gpu_layers: int | None
    reasons: tuple[str, ...]


def _hosted(host: int, hw: Hardware, pinned: int) -> tuple[int, int]:
    """``(experts in RAM, experts paged from disk)`` given ``pinned`` bytes RAM holds already."""
    room = hw.ram_bytes - DEFAULTS["ram_reserve"] - pinned
    if room < 0:
        return 0, host
    return min(host, room), max(host - room, 0)


def _paged_reason(paged: int, have: int, what: str) -> list[str]:
    if not paged:
        return []
    return [f"{human_bytes(paged)} of experts do not fit in {human_bytes(have)} of {what}: they "
            "are paged from disk each token and the speed is bound by the disk"]


def _discrete(facts: ModelFacts, hw: Hardware, kv: int, allowance: int, context: int) -> Layout:
    have = hw.fast_bytes - DEFAULTS["device_reserve"]
    if facts.always_bytes + kv + allowance > have:
        raise _short(facts, (kv, allowance), have, "the card's memory", context)
    room = have - facts.always_bytes - kv - allowance
    per_layer = facts.expert_layer_bytes
    moved = _fewest_moved(per_layer, room)
    host = sum(per_layer[:moved])
    in_ram, paged = _hosted(host, hw, 0)
    reasons = [f"the card holds the always-needed tensors, the KV cache and "
               f"{human_bytes(room)} of experts; "
               + ("every expert fits there" if moved == 0 else
                  f"the experts of the first {moved} of {len(per_layer)} layers go to the CPU")]
    overrides: tuple[str, ...] = ()
    if facts.table:
        overrides = ("per_layer_token_embd=CPU",)
        reasons.append("the n-gram table is gathered a few rows a token, so it stays on the "
                       "host, mmapped from disk, whatever the card's size")
    reasons += _paged_reason(paged, hw.ram_bytes, "RAM beside the reserve")
    return Layout(moved, sum(per_layer) - host, in_ram, paged, overrides, None, tuple(reasons))


def _unified(facts: ModelFacts, hw: Hardware, kv: int, allowance: int, context: int) -> Layout:
    need = facts.always_bytes + kv + allowance
    if need > hw.fast_bytes:
        raise _short(facts, (kv, allowance), hw.fast_bytes, "the memory a model may use", context)
    experts = sum(facts.expert_layer_bytes)
    room = hw.fast_bytes - need
    paged = max(experts - room, 0)
    reasons = ["unified memory: the CPU and GPU halves are one pool, so no override is set "
               "and `--on-cpu` buys nothing; the n-gram table stays mmapped on the host side "
               "of its own accord"]
    reasons += _paged_reason(paged, hw.fast_bytes, "the memory a model may use")
    return Layout(0, min(experts, room), 0, paged, (), None, tuple(reasons))


def _cpu(facts: ModelFacts, hw: Hardware, kv: int, allowance: int, context: int) -> Layout:
    need = facts.always_bytes + kv + allowance
    if need > hw.ram_bytes - DEFAULTS["ram_reserve"]:
        raise _short(facts, (kv, allowance), hw.ram_bytes - DEFAULTS["ram_reserve"], "RAM",
                     context)
    experts = sum(facts.expert_layer_bytes)
    in_ram, paged = _hosted(experts, hw, need)
    reasons = ["no GPU: every layer runs on the CPU (`--n-gpu-layers 0`); the experts are "
               "mmapped from disk and the page cache keeps what fits"]
    reasons += _paged_reason(paged, hw.ram_bytes, "RAM beside the reserve")
    return Layout(len(facts.expert_layer_bytes), 0, in_ram, paged, (), 0, tuple(reasons))


def _reads(facts: ModelFacts, hw: Hardware, lay: Layout) -> tuple[float, float, float]:
    """Bytes a token reads from ``(fast memory, RAM, disk)``: the always-needed tensors, then
    each layer's routed experts from where the layout put them."""
    per = [facts.expert_read(n) for n in range(len(facts.expert_layer_bytes))]
    always = facts.read_per_token()
    total_exp = sum(per)
    if hw.kind == "cpu":
        ratio = lay.in_ram / max(lay.in_ram + lay.paged, 1)
        return 0.0, always + total_exp * ratio, total_exp * (1 - ratio)
    if hw.kind == "unified":
        paged_share = lay.paged / max(lay.fast + lay.paged, 1)
        return always + total_exp * (1 - paged_share), 0.0, total_exp * paged_share
    host_read = sum(per[:lay.moved])
    ratio = lay.in_ram / max(lay.in_ram + lay.paged, 1)
    return always + sum(per[lay.moved:]), host_read * ratio, host_read * (1 - ratio)


def prompt_estimates(plan: Plan, tokens: int, ubatch: int, tflops: float) -> list[Estimate]:
    """Time to read a ``tokens``-long prompt, as a bandwidth-bound part and a compute-bound
    part. Every micro-batch of ``ubatch`` tokens reads each weight it routes to; at that size
    nearly every expert is routed to, so the whole of each tier's weights is read once per
    micro-batch. The time is at least the larger of the two."""
    facts, hw = plan.facts, plan.hardware
    batches = -(-tokens // max(ubatch, 1))
    fast = facts.always_bytes + plan.fast_experts
    if hw.kind == "cpu":
        fast_time, ram_bytes = 0.0, facts.always_bytes + plan.host_in_ram
    else:
        fast_time = fast / ((hw.vram_gbps if hw.kind == "discrete" else hw.ram_gbps) * 1e9)
        ram_bytes = plan.host_in_ram if hw.kind == "discrete" else 0
    per_batch = fast_time + ram_bytes / (hw.ram_gbps * 1e9) + plan.paged / (hw.disk_gbps * 1e9)
    flops = 2 * facts.active_elements
    bandwidth = batches * per_batch
    compute = tokens * flops / (tflops * 1e12)
    longest = max(bandwidth, compute)
    shown = (f"{batches} micro-batches of {ubatch} * (weights on the fast side "
             f"{human_bytes(fast)} / {hw.vram_gbps:g} GB/s + host experts "
             f"{human_bytes(ram_bytes)} / {hw.ram_gbps:g} GB/s + paged "
             f"{human_bytes(plan.paged)} / {hw.disk_gbps:g} GB/s)")
    return [Estimate(f"prompt of {tokens:,} tokens, bandwidth-bound time", bandwidth, "s",
                     f"ceil(tokens / micro-batch) * sum over tiers of weight bytes / bandwidth = {shown}"),
            Estimate(f"prompt of {tokens:,} tokens, compute-bound time", compute, "s",
                     f"tokens * 2 * active parameters ({facts.active_elements / 1e9:.1f}B, from the "
                     f"header) / ({tflops:g} TFLOPS assumed)"),
            Estimate("prompt processing speed upper bound", tokens / longest, "tok/s",
                     "tokens / max(bandwidth-bound time, compute-bound time); attention score "
                     "cost and PCIe transfers are not modelled")]


def place(facts: ModelFacts, hw: Hardware, *, context: int, kv_bytes: int,
          allowance: int = RUNTIME_ALLOWANCE_BYTES) -> Plan:
    """The `Plan` for ``facts`` on ``hw`` at ``context`` tokens with ``kv_bytes`` of cache.

    ``allowance`` is the runtime and compute-buffer headroom. Raises `Shortfall` naming the
    exact number of bytes missing when the always-needed tensors and the cache do not fit.
    """
    lay = {"discrete": _discrete, "unified": _unified, "cpu": _cpu}[hw.kind](
        facts, hw, kv_bytes, allowance, context)
    layers = len(facts.expert_layer_bytes)
    fast_read, ram_read, disk_read = _reads(facts, hw, lay)
    estimates = [*_expert_estimates(facts), *_table_estimates(facts),
                 *_times(fast_read, ram_read, disk_read, hw)]
    moved = lay.moved if hw.kind == "discrete" else 0
    return Plan(facts, hw, context, kv_bytes, allowance, moved,
                hw.kind == "discrete" and layers > 0 and moved >= layers, lay.overrides,
                lay.gpu_layers, lay.fast, lay.in_ram + lay.paged, lay.in_ram, lay.paged,
                tuple(estimates), lay.reasons)
