"""What a pointer decider's base model says it can read, and whether this machine has room for a prompt.

There is no length limit of ours. A prompt is bounded by the model's own window, read from its
config at load time (``max_position_embeddings``), and by the memory free on the device when it
is read. A model that reports no window is refused by name; a prompt that does not fit says what
it needs and what is free.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poolhouse.decide.types import DecideError

__all__ = ["Dimensions", "available_bytes", "dimensions", "of_directory", "require_room",
           "within_window", "working_bytes"]

GIB = 1 << 30
FIELDS = ("max_position_embeddings", "hidden_size", "num_hidden_layers", "num_attention_heads")


@dataclass(frozen=True, slots=True)
class Dimensions:
    """What a loaded model's config says: its window and the sizes its prompt memory follows."""

    window: int
    hidden: int
    layers: int
    heads: int
    where: str


def dimensions(config: Mapping[str, Any], where: str) -> Dimensions:
    """The `Dimensions` of a model ``config`` (its ``text_config`` when it has one), read from
    ``where``; a missing or unusable field raises a `DecideError` naming it."""
    inner = config.get("text_config")
    text = inner if isinstance(inner, Mapping) else config
    found: list[int] = []
    for name in FIELDS:
        value = text.get(name)
        if type(value) is not int or value <= 0:
            raise DecideError(f"{where} has no usable {name} ({value!r}); the model's own window "
                              "is read from it and there is no other number to use")
        found.append(value)
    return Dimensions(*found, where=where)


def of_directory(base_dir: Path | str) -> Dimensions:
    """The `Dimensions` read from the ``config.json`` of the model directory ``base_dir``."""
    path = Path(base_dir) / "config.json"
    try:
        config = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise DecideError(f"{path} cannot be read ({error}); the model's window is read from it") from error
    if not isinstance(config, dict):
        raise DecideError(f"{path} is not a model config; the model's window is read from it")
    return dimensions(config, str(path))


def within_window(tokens: int, dim: Dimensions, what: str, *, limit: int | None = None) -> None:
    """Raise a `DecideError` when ``what`` (``tokens`` long) is over the model's window, or over the
    ``limit`` the person chose to save memory."""
    if tokens > dim.window:
        raise DecideError(f"{what} is {tokens} tokens; the model's context window is {dim.window} "
                          f"(max_position_embeddings in {dim.where})")
    if limit is not None and tokens > limit:
        raise DecideError(f"{what} is {tokens} tokens; this run was given max_tokens {limit}")


def working_bytes(dim: Dimensions, tokens: int, rows: int = 1) -> int:
    """The memory ``rows`` prompts of ``tokens`` tokens take to read, beyond the weights: the
    attention scores and the per-layer activations, as an upper estimate."""
    return rows * (tokens ** 2 * dim.heads * 8 + tokens * dim.hidden * dim.layers * 16)


def available_bytes(device: str) -> int:
    """The memory free now on ``device``: the card's free memory for ``cuda``, else the machine's."""
    if device.startswith("cuda"):
        import torch
        return int(torch.cuda.mem_get_info()[0])
    import psutil
    return int(psutil.virtual_memory().available)


def require_room(dim: Dimensions, tokens: int, device: str, what: str, *, rows: int = 1) -> int:
    """The working bytes of ``rows`` prompts of ``tokens`` tokens, or a `DecideError` saying what
    they need and what ``device`` has free."""
    need, free = working_bytes(dim, tokens, rows), available_bytes(device)
    if need > free:
        many = f"{rows} at once, " if rows > 1 else ""
        raise DecideError(f"{what} is {tokens} tokens ({many}within the model's {dim.window}-token window) "
                          f"and needs about {need / GIB:.1f} GiB of working memory; "
                          f"{free / GIB:.1f} GiB is available on {device}")
    return need
