"""The largest safe context for an MLX model, from the model's own config.json.

A GGUF names its window in its header; an MLX folder names it in ``config.json`` (the same
reading `poolhouse.decide.window` makes). The config is turned into the header fields the
memory estimate reads, so an MLX model is fitted by the same search and the same rating as a
GGUF, with the KV cache at the width MLX stores it. A model not yet on this machine is read
from its Hub config before any weight is fetched; if that cannot be read, the refusal says the
window is not known until the model is fetched. A config that reports no window fails, naming
the field.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from poolhouse import home, hub
from poolhouse.decide import window
from poolhouse.decide.types import DecideError
from poolhouse.hub import remote
from poolhouse.hub.probe import MachineMemory
from poolhouse.serve import mlx_tree
from poolhouse.serve.mlxref import PREFIX
from poolhouse.serve.suggest import Suggestion, largest_fit_meta

__all__ = ["config_of", "header_of", "largest_fit"]

REPO = re.compile(r"[\w.-]+/[\w.-]+")
CACHE_TYPES = {8: "q8_0", 4: "q4_0"}
"""The GGUF cache type that costs what MLX's quantised cache of that many bits costs."""


def _local(text: str) -> Path | None:
    """The folder holding this model's config.json: the folder named, else the Hub cache's snapshot when
    that has one (a snapshot of a download that never got that far has none and is read from the Hub)."""
    where = home.expand(text)
    if (where / "config.json").is_file():
        return where
    snapshot = hub.held_snapshot(text) if REPO.fullmatch(text) else None
    return snapshot if snapshot is not None and (snapshot / "config.json").is_file() else None


def _weights(folder: Path) -> int:
    return sum(part.stat().st_size for part in folder.glob("*.safetensors"))


def config_of(model: str | Path) -> tuple[Mapping[str, Any], int, str]:
    """``(config, weight bytes, where it was read)`` for an MLX ``model``: its folder, the Hub
    cache's snapshot, or, for a model not on this machine, the Hub's own config.json and file
    sizes (nothing is downloaded)."""
    text = str(model).removeprefix(PREFIX)
    folder = _local(text)
    if folder is not None:
        path = folder / "config.json"
        try:
            return json.loads(path.read_text(encoding="utf-8")), _weights(folder), str(path)
        except (OSError, ValueError) as error:
            raise ValueError(f"{path} cannot be read ({error}); the model's window is read from it") from error
    if not REPO.fullmatch(text):
        raise ValueError(f"{text} is not an MLX folder on this machine and not a Hub repository (owner/repo), "
                         "so its window cannot be known; pass a context")
    try:
        config = json.loads(remote.text_file(text, "config.json"))
        size = sum(one.size for one in remote.listing(text) if one.name.endswith(".safetensors"))
    except (remote.RemoteError, OSError, ValueError) as error:
        raise ValueError(f"{text} is not on this machine and its Hub config could not be read ({error}): its window "
                         "is not known until it is fetched (`poolhouse-models pull`), or pass a context") from error
    return config, size, f"the Hub's config.json for {text}"


def header_of(config: Mapping[str, Any], where: str) -> dict[str, object]:
    """The GGUF-style header fields the memory estimate reads, from an MLX ``config``; ValueError
    naming the field when the config does not report the window or the attention shape."""
    try:
        dim = window.dimensions(config, where)
    except DecideError as error:
        raise ValueError(str(error)) from error
    each, _layers, heads, width = mlx_tree.attention_shape(config)
    if not (each and heads and width):
        raise ValueError(f"{where} does not say its attention layers, KV heads and head width "
                         "(num_hidden_layers, num_key_value_heads, head_dim), so its cache cannot be sized")
    return {"general.architecture": "mlx", "mlx.context_length": dim.window, "mlx.block_count": each,
            "mlx.attention.head_count": dim.heads, "mlx.attention.head_count_kv": heads,
            "mlx.attention.key_length": width, "mlx.attention.value_length": width}


def largest_fit(model: str | Path, machine: MachineMemory | None = None, *, slots: int = 1,
                bits: int | None = None) -> Suggestion:
    """`suggest.largest_fit` for an MLX ``model``: its window as the ceiling, this machine's memory
    as the floor, the cache stored in ``bits`` bits (``None`` for f16)."""
    config, weights, where = config_of(model)
    return largest_fit_meta(header_of(config, where), weights, machine, slots=slots,
                            kv=CACHE_TYPES.get(bits or 0, "f16"))
