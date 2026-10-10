"""The KV cache type everything is served with unless it says otherwise.

A leaf of the core layer, so the serve code, the MLX engine and the docs read one default.
`poolhouse.serve.kvcache` applies it: what a spec is really served with, and where it falls back.
"""

from __future__ import annotations

__all__ = ["DEFAULT_KV_BITS", "DEFAULT_KV_CACHE", "MLX_BITS", "UNQUANTISED"]

DEFAULT_KV_CACHE = "q8_0"
"""The KV cache type K and V are stored as unless a spec, a profile or a person names another:
8 bit, about half the memory of f16 at near-f16 quality."""

UNQUANTISED = frozenset({"f32", "f16", "bf16"})
"""The cache types that store full-width values."""

MLX_BITS = {"q8_0": 8, "q6_0": 6, "q5_0": 5, "q5_1": 5, "q4_0": 4, "q4_1": 4, "iq4_nl": 4}
"""The bit width MLX stores each llama.cpp cache type as."""

DEFAULT_KV_BITS = MLX_BITS[DEFAULT_KV_CACHE]
"""MLX's width for the default cache type."""
