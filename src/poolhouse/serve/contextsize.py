"""A context size the way a person names it, and the one a model was trained at."""
from __future__ import annotations

import re
from pathlib import Path

__all__ = ["parse_context", "trained_context"]

_CONTEXT_SUFFIX = {"": 1, "k": 1_000, "m": 1_000_000}


def parse_context(text: str | int) -> int:
    """A context size the way a person names it: ``32768``, ``256k``, ``1m``.

    ``k`` and ``m`` are the plain decimal multipliers a model card's own round figures
    use -- 1,000 and 1,000,000 -- not a file size's binary kibi/mebi, so ``1m`` is
    1,000,000 tokens, not 1,048,576.
    """
    if isinstance(text, int):
        return text
    raw = str(text).strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([km]?)", raw)
    if not match:
        raise ValueError(f"not a context size: {text!r} (want e.g. 32768, 256k, 1m)")
    number, suffix = match.groups()
    return int(float(number) * _CONTEXT_SUFFIX[suffix])


def trained_context(model: str | Path) -> int:
    """The context this model trained at, off its own GGUF header.

    0 when that cannot be read -- an ``hf:`` reference not yet on disk, or a header that
    does not name one -- which is unknown, not zero: a caller asking whether a context is
    beyond it must treat 0 as no opinion rather than as a real training length.
    """
    if isinstance(model, str) and model.startswith("hf:"):
        return 0
    path = Path(model)
    if not path.is_file():
        return 0
    try:
        from poolhouse.serve.layout import layout

        return layout(path).context_length
    except Exception:  # noqa: BLE001 - an unreadable header names no trained length
        return 0
