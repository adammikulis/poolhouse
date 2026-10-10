"""Gated full attention over a draft tree: each node sees the prefix and its own ancestors."""

from __future__ import annotations

from collections.abc import Sequence

import mlx.core as mx
import mlx.nn as nn
from mlx_lm.models.base import quantized_scaled_dot_product_attention
from mlx_lm.models.cache import KVCache, QuantizedKVCache

__all__ = ["GROUP_SIZE", "attend", "compact", "plan_kv", "quantise", "rewind", "tree_mask"]

GROUP_SIZE = 64
"""Values per scale in a quantised KV cache: mlx-lm's own default."""


def tree_mask(ancestors: Sequence[Sequence[int]], offset: int) -> mx.array:
    """Boolean [N, offset + N]: the cached prefix, then node j visible to node i iff j is on i's path."""
    count = len(ancestors)
    own = [[False] * count for _ in range(count)]
    for i, row in enumerate(ancestors):
        for j in row:
            if j >= 0:
                own[i][j] = True
    return mx.concatenate([mx.ones((count, offset), dtype=mx.bool_), mx.array(own)], axis=1)


def attend(mod: nn.Module, cache: object, x: mx.array, positions: mx.array,
           mask: mx.array | None) -> mx.array:
    """The mixer output [N, D] for x [N, D] at per-node ``positions``, appending N rows to ``cache``."""
    count = x.shape[0]
    queries, gate = mx.split(mod.q_proj(x).reshape(count, mod.num_attention_heads, -1), 2,
                             axis=-1)
    keys = mod.k_norm(mod.k_proj(x).reshape(count, mod.num_key_value_heads, -1))
    values = mod.v_proj(x).reshape(count, mod.num_key_value_heads, -1)
    # one batch element per node, so each gets its own rotary position
    queries = mod.rope(mod.q_norm(queries)[:, :, None, :], offset=positions)
    keys = mod.rope(keys[:, :, None, :], offset=positions)
    queries = queries[:, :, 0, :].transpose(1, 0, 2)[None]
    keys = keys[:, :, 0, :].transpose(1, 0, 2)[None]
    values = values.transpose(1, 0, 2)[None]
    keys, values = cache.update_and_fetch(keys, values)
    if hasattr(cache, "bits"):
        out = quantized_scaled_dot_product_attention(
            queries, keys, values, scale=mod.scale, mask=mask,
            group_size=cache.group_size, bits=cache.bits)
    else:
        out = mx.fast.scaled_dot_product_attention(queries, keys, values, scale=mod.scale,
                                                   mask=mask)
    out = out[0].transpose(1, 0, 2).reshape(count, -1)
    return mod.o_proj(out * mx.sigmoid(gate.reshape(count, -1)))


def _parts(held: object) -> tuple[mx.array, ...]:
    """The arrays one side of a cache is stored in: one, or a quantised cache's three."""
    return tuple(held) if isinstance(held, tuple) else (held,)


def quantise(caches: list[object], bits: int, group_size: int = GROUP_SIZE) -> int:
    """Replace each empty attention cache in ``caches`` with a ``bits``-bit one; returns how many.

    Only a plain ``KVCache`` converts: a recurrent layer's state, a rotating window and any
    other kind stay as they are, as mlx-lm's own ``maybe_quantize_kv_cache`` leaves them.
    """
    done = 0
    for index, held in enumerate(caches):
        if type(held) is KVCache and held.offset == 0:
            caches[index] = QuantizedKVCache(group_size=group_size, bits=bits)
            done += 1
    return done


def plan_kv(layout: object, bits: int | None) -> tuple[int | None, str]:
    """``(bits, note)``: the width this layout's attention caches are stored at, and one line.

    ``None`` bits with a reason where the layout cannot (its attention reads the cache
    directly and has no quantised path) or the heads are narrower than one scale group.
    """
    if not bits:
        return None, "KV cache stored as f16"
    if not getattr(layout, "kv_quantisable", False):
        return None, "KV cache stored as f16: this layout has no quantised attention path"
    width = int(getattr(layout.args, "head_dim", 0) or 0)  # type: ignore[attr-defined]
    if width % GROUP_SIZE:
        return None, (f"KV cache stored as f16: a head is {width} wide, not a multiple of "
                      f"the {GROUP_SIZE} values a scale covers")
    held = layout.make_cache()  # type: ignore[attr-defined]
    each = sum(type(one) is KVCache for one in held)
    return bits, (f"KV cache stored in {bits} bits on {each} of {len(held)} layers; the "
                  f"other {len(held) - each} hold recurrent state, which is not quantised")


def _index_rows(cache: object) -> tuple[mx.array, mx.array] | None:
    keys = getattr(cache, "index_keys", None)
    return None if keys is None else (keys, cache.index_position_ids)


def compact(caches: Sequence[object], offset: int, path: mx.array) -> None:
    """Move the accepted rows of every attention cache to ``offset`` and cut the rest.

    A cache that also keeps indexer keys (``index_keys`` [B, L, D] and their positions) has
    those rows moved the same way.
    """
    rows = [(tuple(a[..., offset + path, :] for a in _parts(c.keys)),
             tuple(a[..., offset + path, :] for a in _parts(c.values))) for c in caches]
    indexed = [_index_rows(c) for c in caches]
    kept = [None if held is None else
            (mx.concatenate([held[0][:, :offset], held[0][:, offset + path]], axis=1),
             mx.concatenate([held[1][..., :offset], held[1][..., offset + path]], axis=-1))
            for held in indexed]
    mx.eval(*[half for pair in rows for side in pair for half in side],
            *[half for pair in kept if pair is not None for half in pair])
    accepted = int(path.size)
    for cache, (keys, values), index in zip(caches, rows, kept, strict=True):
        for held, moved in zip((*_parts(cache.keys), *_parts(cache.values)), (*keys, *values),
                               strict=True):
            held[..., offset:offset + accepted, :] = moved
        cache.offset = offset + accepted
        if index is not None:
            cache.index_keys, cache.index_position_ids = index


def rewind(cache: object, count: int) -> None:
    """Cut an attention cache back to its first ``count`` tokens, indexer rows included."""
    cache.offset = count
    if getattr(cache, "index_keys", None) is not None:
        cache.index_keys = cache.index_keys[:, :count]
        cache.index_position_ids = cache.index_position_ids[..., :count]
    blocks = getattr(cache, "index_block_keys", None)
    if blocks is not None:
        cache.index_block_keys = blocks[:, :, :count // int(cache.index_block_ratio)]
