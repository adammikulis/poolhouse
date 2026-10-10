"""What the caches of a model cost, per token and per sequence, from its GGUF metadata.

Three parts, each read from the header and not assumed: the K/V cache of the layers that
hold one (which layers is the layer pattern in the metadata), the sparse-attention indexer's
cache when the header names an indexer, and the fixed recurrent (Gated DeltaNet) state each
sequence keeps in the other layers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from poolhouse.serve.preflight import (
    _CACHE_BYTES,
    _per_layer,
    _recurrent_layers,
    recurrent_state_bytes,
)

__all__ = ["CACHE_TYPES", "KvRow", "kv_layers", "kv_row"]

CACHE_TYPES = ("f16", "q8_0", "q4_0")


@dataclass(frozen=True, slots=True)
class KvRow:
    """The caches at one pair of cache types.

    ``indexer`` is what llama.cpp allocates for the sparse-attention indexer (the same size
    as the K/V cache; measured, docs/architectures/qwen4exp.md) and ``indexer_floor`` the
    smallest cache its metadata could imply: one shared key head of ``indexer.key_length``.
    """

    cache_k: str
    cache_v: str
    layers: int
    heads: int
    key_dim: int
    value_dim: int
    kv_per_token: int
    indexer_per_token: int
    indexer_floor_per_token: int
    recurrent_per_seq: int

    @property
    def per_token(self) -> int:
        return self.kv_per_token + self.indexer_per_token

    def total(self, context: int, parallel: int = 1) -> int:
        """Bytes for ``context`` tokens in all, with ``parallel`` sequences' recurrent state."""
        return self.per_token * context + self.recurrent_per_seq * max(1, parallel)


def kv_layers(meta: Mapping[str, object]) -> list[bool]:
    """Which layers hold a K/V cache: those with a nonzero entry in ``attention.compress_ratios``
    when the header has one, else the ones the layer pattern (``full_attention_interval`` or
    ``attention.recurrent_layers``) does not make recurrent."""
    arch = str(meta.get("general.architecture") or "")
    count = int(meta.get(f"{arch}.block_count") or 0)  # type: ignore[call-overload]
    ratios = meta.get(f"{arch}.attention.compress_ratios")
    if isinstance(ratios, list) and len(ratios) == count and any(ratios):
        return [bool(r) for r in ratios]
    shared = int(meta.get(f"{arch}.attention.shared_kv_layers") or 0)  # type: ignore[call-overload]
    pattern = _recurrent_layers(lambda suffix: meta.get(f"{arch}.{suffix}"), count)
    return [not pattern[n] and n < count - shared for n in range(count)]


def kv_row(meta: Mapping[str, object], cache_k: str, cache_v: str) -> KvRow:
    """The cache costs of ``meta`` stored as ``cache_k`` / ``cache_v``."""
    arch = str(meta.get("general.architecture") or "")
    count = int(meta.get(f"{arch}.block_count") or 0)  # type: ignore[call-overload]
    holding = kv_layers(meta)
    heads = _per_layer(meta.get(f"{arch}.attention.head_count_kv") or 0, count)
    key = _per_layer(meta.get(f"{arch}.attention.key_length") or 0, count)
    value = _per_layer(meta.get(f"{arch}.attention.value_length")
                       or meta.get(f"{arch}.attention.key_length") or 0, count)
    bytes_k, bytes_v = _CACHE_BYTES.get(cache_k, 2.0), _CACHE_BYTES.get(cache_v, 2.0)
    kv = sum(heads[n] * (key[n] * bytes_k + value[n] * bytes_v) for n in range(count) if holding[n])
    indexed = meta.get(f"{arch}.attention.indexer.key_length")
    floor = sum(float(indexed) * bytes_k for n in range(count) if holding[n]) if indexed else 0.0  # type: ignore[arg-type]
    used = [n for n in range(count) if holding[n]]
    first = used[0] if used else 0
    return KvRow(cache_k, cache_v, len(used), int(heads[first]) if count else 0,
                 int(key[first]) if count else 0, int(value[first]) if count else 0,
                 int(kv), int(kv) if indexed else 0, int(floor), recurrent_state_bytes(dict(meta)))
