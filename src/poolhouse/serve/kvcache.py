"""How a served model's KV cache is stored: the default, the overrides, and where it falls back.

The default is 8 bit (``q8_0``) for K and V, on every backend that can: about half the
memory of f16 at near-f16 quality. A spec that names a type keeps it; one that names none gets
:data:`DEFAULT_KV_CACHE`. :func:`effective` answers what a spec is really served with, so the
argv, the memory admission, the registry and ``status`` all read one answer.

llama.cpp keeps a quantised V cache only under flash attention, so V falls back to f16 where
flash attention is off or the model's head size has no kernel for it; the speculative draft's
cache follows the same default and the same fallback. An embedding or reranking server keeps no
chat cache and gets no flag. MLX has one bit width for K and V together, so the higher of the
two is used; its recurrent layers hold state that no cache type quantises.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poolhouse.hub.modelfile import scan_gguf
from poolhouse.serve.mlxref import is_mlx

__all__ = ["DEFAULT_KV_BITS", "DEFAULT_KV_CACHE", "KvCache", "cache_flags", "draft_flags",
           "effective", "mlx_bits", "said"]

DEFAULT_KV_CACHE = "q8_0"
"""The KV cache type K and V are stored as unless a spec, a profile or a person names another."""

UNQUANTISED = frozenset({"f32", "f16", "bf16"})
#: the bit width MLX stores a llama.cpp cache type as
MLX_BITS = {"q8_0": 8, "q6_0": 6, "q5_0": 5, "q5_1": 5, "q4_0": 4, "q4_1": 4, "iq4_nl": 4}
DEFAULT_KV_BITS = MLX_BITS[DEFAULT_KV_CACHE]
"""MLX's width for the default cache type."""


def said(type_k: str, type_v: str) -> str:
    """One cache type from its two halves: ``q8_0``, or ``q8_0/f16`` where they differ."""
    k, v = str(type_k or "f16"), str(type_v or "f16")
    return k if k == v else f"{k}/{v}"


def mlx_bits(type_k: str, type_v: str) -> tuple[int | None, str]:
    """``(bits, note)`` MLX stores K and V with, from two cache types.

    MLX has one width for both, so the higher of the two is used and the note says so when the
    halves differ. ``None`` is an unquantised cache. A type MLX has no width for is refused.
    """
    wanted = []
    for kind in (type_k, type_v):
        kind = str(kind or DEFAULT_KV_CACHE).lower()
        if kind in UNQUANTISED:
            wanted.append(None)
        elif kind in MLX_BITS:
            wanted.append(MLX_BITS[kind])
        else:
            raise ValueError(f"MLX stores a KV cache as {', '.join(sorted(MLX_BITS))} or "
                             f"f16, not {kind!r}")
    top = max((b for b in wanted if b), default=None)
    if wanted[0] != wanted[1]:
        return top, (f"MLX has one width for K and V together; using the higher, "
                     f"{top}-bit" if top else "")
    return top, ""


@dataclass(frozen=True, slots=True)
class KvCache:
    """What a spec's KV cache is served as. ``k`` and ``v`` are the llama.cpp flags to pass,
    ``""`` for none; ``bits`` is MLX's width, ``None`` for an unquantised cache."""

    k: str = ""
    v: str = ""
    draft_k: str = ""
    draft_v: str = ""
    bits: int | None = None
    mlx: bool = False
    notes: tuple[str, ...] = ()

    @property
    def said(self) -> str:
        """The one line ``up`` and ``status`` print."""
        if self.mlx:
            return f"{self.bits}-bit (MLX)" if self.bits else "f16 (MLX)"
        return said(self.k, self.v)

    @property
    def draft_said(self) -> str:
        """The draft's own cache, ``""`` when no draft keeps one."""
        return said(self.draft_k, self.draft_v) if self.draft_k else ""


def _type_of(spec: Any, field: str, fallback: str = DEFAULT_KV_CACHE) -> str:
    return str(getattr(spec, field, "") or fallback)


HIGH_ATTENTION_HEADS = (64, 80, 96, 112, 128, 192, 256, 512)
"""Attention head sizes llama.cpp's flash-attention kernels cover."""


def flash_attention_likely(found: Mapping[str, object]) -> bool:
    """Whether the header's attention head sizes are ones llama.cpp's flash kernels cover."""
    arch = str(found.get("general.architecture") or "")
    dim = found.get(f"{arch}.attention.key_length")
    if not dim:
        embed = found.get(f"{arch}.embedding_length")
        heads = found.get(f"{arch}.attention.head_count")
        dim = int(embed) // int(heads) if embed and heads else 0  # type: ignore[call-overload]
    return int(dim or 0) in HIGH_ATTENTION_HEADS  # type: ignore[call-overload]


def _flash_missing(spec: Any, meta: Mapping[str, object] | None) -> str:
    """Why a quantised V cache cannot be used for ``spec``, or ``""`` when it can.

    ``meta`` is the model's GGUF header when the caller has read it; None reads it here."""
    if not getattr(spec, "flash_attn", True):
        return "flash attention is off"
    try:
        if meta is None:
            # a reference not on disk has no header to read; the load will say what it is
            on_disk = Path(str(spec.model)).is_file()
            meta = scan_gguf(Path(str(spec.model))).values if on_disk else {}
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    arch = str(meta.get("general.architecture") or "")
    sized = meta.get(f"{arch}.attention.key_length") or meta.get(f"{arch}.attention.head_count")
    if sized and not flash_attention_likely(meta):
        return "this model's attention head size has no flash-attention kernel"
    return ""


def effective(spec: Any, meta: Mapping[str, object] | None = None) -> KvCache:
    """The KV cache ``spec`` is served with: its own types, else the default, then the fallbacks.

    ``meta`` is the model's header when the caller already read it (``{}`` when it could not)."""
    ask_k, ask_v = _type_of(spec, "cache_type_k"), _type_of(spec, "cache_type_v")
    if is_mlx(getattr(spec, "model", "")):
        try:
            bits, note = mlx_bits(ask_k, ask_v)
        except ValueError as why:
            bits, note = None, f"{why}; the cache stays f16"
        return KvCache(bits=bits, mlx=True, notes=(note,) if note else ())
    if getattr(spec, "embedding", False) or getattr(spec, "reranking", False):
        return KvCache(notes=("this server keeps no chat KV cache; no cache type is passed",))
    drafting = bool(getattr(spec, "draft", None) or getattr(spec, "spec_type", ""))
    ask_dk = _type_of(spec, "spec_draft_type_k")
    ask_dv = _type_of(spec, "spec_draft_type_v")
    why = _flash_missing(spec, meta) if any(
        t.lower() not in UNQUANTISED for t in (ask_v, ask_dv if drafting else "f16")) else ""
    notes: list[str] = []
    if why:
        ask_v = "f16" if ask_v.lower() not in UNQUANTISED else ask_v
        ask_dv = "f16" if ask_dv.lower() not in UNQUANTISED else ask_dv
        notes.append(f"{why}: the V cache stays f16 (a quantised V needs flash attention)")
    return KvCache(ask_k, ask_v, ask_dk if drafting else "", ask_dv if drafting else "",
                   notes=tuple(notes))


def cache_flags(spec: Any) -> list[str]:
    """``--cache-type-k`` and ``--cache-type-v`` for ``spec``, none where it keeps no cache."""
    kept = effective(spec)
    return [flag for pair in (("--cache-type-k", kept.k), ("--cache-type-v", kept.v))
            if pair[1] for flag in pair]


def draft_flags(spec: Any) -> list[str]:
    """``--spec-draft-type-k`` and ``-v`` for the draft's own cache, none without a draft."""
    kept = effective(spec)
    return [flag for pair in (("--spec-draft-type-k", kept.draft_k),
                              ("--spec-draft-type-v", kept.draft_v))
            if pair[1] for flag in pair]
