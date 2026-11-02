"""The search every "largest safe context" default shares, and the machine it is searched on.

A memory model (the header estimate of `estimate`, the tensor placement of `plan`) says whether
one context holds; this module finds the largest one that does, under the model's trained
window, and says which machine to ask it of. Nothing else in the tree picks a context by hand.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

from poolhouse.hub.probe import MachineMemory

__all__ = ["PAD", "capacity", "contexts", "largest_context", "trained_window"]

PAD = 256
"""llama.cpp pads each sequence's context up to a multiple of this (`GGML_PAD(n_ctx, 256)` in
src/llama-context.cpp), so a finer step buys nothing."""


def trained_window(found: Mapping[str, object]) -> int:
    """The context length a GGUF header says its model trained at. A header that names none is
    refused, naming the field: there is no window to fit under, and none is guessed."""
    arch = str(found.get("general.architecture") or "")
    field = f"{arch or '<architecture>'}.context_length"
    value = found.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"the header names no {field}, so there is no trained window to fit under")
    return value


def contexts(top: int) -> list[int]:
    """Every context worth trying, largest first: ``top`` itself, then each multiple of
    `PAD` below it."""
    return [top, *range((top - 1) // PAD * PAD, 0, -PAD)]


def largest_context(holds: Callable[[int], bool], steps: Sequence[int]) -> int:
    """The first of ``steps`` (largest first) that ``holds``, or 0 when none does.

    A larger context never needs less memory, so the steps that hold are the tail of the list
    and halving finds its start in a dozen calls however many steps there are.
    """
    low, high, found = 0, len(steps) - 1, 0
    while low <= high:
        mid = (low + high) // 2
        if holds(steps[mid]):
            found, high = steps[mid], mid - 1
        else:
            low = mid + 1
    return found


def capacity(machine: MachineMemory, limit: int = 0) -> MachineMemory:
    """``machine`` with all of its memory counted as free, the pool a model may ever use.

    A default must not depend on what happens to be running this minute: the same model on
    the same machine gets the same context, and the broker is what makes a lease wait for
    memory another lease holds. ``limit`` is the most poolhouse was told it may take
    (`poolhouse.limits`), 0 for no limit.
    """
    cards = tuple(replace(g, free_bytes=min(g.total_bytes, limit) if limit else g.total_bytes)
                  for g in machine.gpus)
    pool = min(machine.gpu_limit_bytes, limit) if limit and machine.gpu_limit_bytes else machine.gpu_limit_bytes
    ram = min(machine.total_ram, limit) if limit and not machine.unified else machine.total_ram
    return replace(machine, available_ram=ram, gpu_limit_bytes=pool, gpus=cards)
