"""What a model's tensors are for and how many bytes each token reads, from GGUF headers alone.

`facts_of` takes every shard's tensors and the first shard's metadata and answers the
questions a placement needs: which bytes every token touches, which are routed experts (per
layer, with the expert count read from the tensor shapes), which are a gathered n-gram
table, and which shard holds what. `poolhouse.serve.plan` puts those bytes on a machine.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from poolhouse.serve.preflight import _kv_estimate_bytes, recurrent_state_bytes
from poolhouse.serve.tensors import Tensor

__all__ = ["ModelFacts", "Part", "ShardFacts", "cache_bytes", "facts_of", "part_of"]

_LAYER = re.compile(r"(?:^|\.)blk\.(\d+)\.")
_EXPERTS = re.compile(r"\.ffn_(?:gate|up|down)_exps\.")
_TABLE_NAMES = ("per_layer_token_embd", "ngram", "engram")


@dataclass(frozen=True, slots=True)
class Part:
    """What one tensor is for (``experts``, ``table``, ``router``, ``shared``, ``attention``,
    ``embedding``, ``mtp``, ``other``) and the layer it belongs to, when it has one."""

    role: str
    layer: int | None


def part_of(tensor: Tensor) -> Part:
    """The role and layer of a tensor, from its name."""
    name = tensor.name.lower()
    found = _LAYER.search(name)
    layer = int(found.group(1)) if found else None
    if any(word in name for word in _TABLE_NAMES):
        return Part("table", layer)
    if "nextn" in name or "mtp" in name:
        return Part("mtp", layer)
    if _EXPERTS.search(name):
        return Part("experts", layer)
    if "ffn_gate_inp" in name or "exp_probs" in name:
        return Part("router", layer)
    if "_shexp" in name:
        return Part("shared", layer)
    if "hc_" in name.rsplit(".", 2)[-2]:
        return Part("other", layer)
    if any(word in name for word in ("attn_", "ssm_", "indexer.")):
        return Part("attention", layer)
    if "token_embd" in name or name.startswith("output."):
        return Part("embedding", layer)
    return Part("other", layer)


@dataclass(frozen=True, slots=True)
class ShardFacts:
    """One shard file: its name, size, and the bytes of each role it holds."""

    name: str
    bytes: int
    roles: Mapping[str, int]

    @property
    def holds_table(self) -> bool:
        return self.roles.get("table", 0) > 0


@dataclass(frozen=True, slots=True)
class ModelFacts:
    """What the headers say about a model, in bytes."""

    name: str
    arch: str
    layers: int
    experts: int
    used: int
    expert_layer_bytes: tuple[int, ...]
    always: Mapping[str, int]
    table: int
    table_rows: int
    table_rows_per_token: int
    gathered: int
    active_elements: int
    shards: tuple[ShardFacts, ...]
    notes: tuple[str, ...]

    @property
    def always_bytes(self) -> int:
        return sum(self.always.values())

    @property
    def expert_bytes(self) -> int:
        return sum(self.expert_layer_bytes)

    @property
    def table_row_bytes(self) -> int:
        return self.table // self.table_rows if self.table_rows else 0

    def read_per_token(self) -> int:
        """Bytes the always-needed tensors read per token: all of them but the one-row token
        embedding lookup."""
        return self.always_bytes - self.gathered

    def expert_read(self, layer: int) -> int:
        """Bytes the routed experts of one layer read per token: ``used / experts`` of it."""
        if not self.experts:
            return 0
        return self.expert_layer_bytes[layer] * self.used // self.experts


def _count(values: Sequence[int]) -> int:
    return max(set(values), key=values.count) if values else 0


def _expert_counts(tensors: Sequence[Tensor], meta: Mapping[str, object], arch: str,
                   notes: list[str]) -> int:
    """The experts per layer: the last dimension of the stacked expert tensors."""
    shapes = [t.shape[-1] for t in tensors if part_of(t).role == "experts" and len(t.shape) == 3]
    counted = _count(shapes)
    stated = meta.get(f"{arch}.expert_count")
    if counted and stated and int(stated) != counted:  # type: ignore[call-overload]
        notes.append(f"the header says {stated} experts and the tensors hold {counted}; "
                     "the tensors are what is read")
    if len(set(shapes)) > 1:
        notes.append(f"expert tensors hold different counts {sorted(set(shapes))}; "
                     f"{counted} is the most common")
    return counted


def _table_rows(meta: Mapping[str, object], arch: str, tensors: Sequence[Tensor]) -> tuple[int, int]:
    """``(rows, rows gathered per token)`` of the n-gram table."""
    rows = 0
    for one in tensors:
        if part_of(one).role == "table" and len(one.shape) >= 2:
            rows = max(rows, one.shape[-1])
    vocab = meta.get(f"{arch}.ple.head_vocab_sizes")
    if isinstance(vocab, list) and vocab:
        return rows, len(vocab)
    ngram = int(meta.get(f"{arch}.ple.ngram_size") or 0)  # type: ignore[call-overload]
    heads = int(meta.get(f"{arch}.ple.heads_per_ngram") or 0)  # type: ignore[call-overload]
    return rows, max(ngram - 1, 0) * heads


def facts_of(shards: Sequence[tuple[Path, Sequence[Tensor]]], meta: Mapping[str, object],
             name: str = "") -> ModelFacts:
    """The facts of a model from its shards' tensors and the first shard's metadata."""
    arch = str(meta.get("general.architecture") or "")
    everything = [t for _path, held in shards for t in held]
    notes: list[str] = []
    experts = _expert_counts(everything, meta, arch, notes)
    used = int(meta.get(f"{arch}.expert_used_count") or 0)  # type: ignore[call-overload]
    layers = int(meta.get(f"{arch}.block_count") or 0)  # type: ignore[call-overload]
    if experts and not used:
        notes.append("the header names no expert_used_count; every expert is read")
        used = experts
    per_layer = [0] * max(layers, 1 + max((p.layer or 0 for p in map(part_of, everything)),
                                          default=0))
    always: dict[str, int] = {}
    table = gathered = 0
    active = gathered_elements = 0
    for one in everything:
        part = part_of(one)
        if part.role == "experts" and part.layer is not None:
            per_layer[part.layer] += one.bytes
            active += one.elements * used // max(experts, 1)
        elif part.role == "table":
            table += one.bytes
        else:
            always[part.role] = always.get(part.role, 0) + one.bytes
            active += one.elements
        if "token_embd" in one.name and part.role == "embedding":
            gathered += one.bytes
            gathered_elements += one.elements
    active -= gathered_elements
    unknown = sorted({t.type for t in everything if t.type.startswith("unknown")})
    if unknown:
        notes.append("sized from the offsets in the header, as the type is not in this "
                     f"table: {', '.join(unknown)}")
    rows, per_token = _table_rows(meta, arch, everything)
    facts = [ShardFacts(path.name, sum(t.bytes for t in held), _roles(held))
             for path, held in shards]
    return ModelFacts(name or (shards[0][0].name if shards else ""), arch, layers, experts,
                      used, tuple(per_layer) if experts else (), always, table, rows,
                      per_token, gathered, active, tuple(facts), tuple(notes))


def _roles(held: Sequence[Tensor]) -> dict[str, int]:
    out: dict[str, int] = {}
    for one in held:
        role = part_of(one).role
        out[role] = out.get(role, 0) + one.bytes
    return out


def cache_bytes(meta: Mapping[str, object], context: int, cache_k: str, cache_v: str,
                parallel: int) -> int:
    """The KV cache for ``context`` tokens plus the recurrent state of ``parallel`` sequences,
    for the layers that hold either."""
    found = dict(meta)
    return (_kv_estimate_bytes(found, context, cache_k or "f16", cache_v or "f16")
            + recurrent_state_bytes(found) * max(1, parallel))
