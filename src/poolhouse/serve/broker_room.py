"""What the broker makes room for before a start: the bytes an ask comes to, and the idle servers to stop for all of them."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from poolhouse.serve import engines
from poolhouse.serve.backend import ServerFailed
from poolhouse.serve.weights import resident_of

if TYPE_CHECKING:
    from poolhouse.serve.broker import Ask, Held

__all__ = ["more_for", "need"]


def need(ask: Ask) -> int:
    """The bytes to make room for before ``ask`` starts: what it says; else its engine's own figure, since an engine may hold
    less than its weights come to; else the weights."""
    if ask.weight:
        return ask.weight
    return _takes(ask) if ask.spec.get("engine") else resident_of(ask.models[0])


def _takes(ask: Ask) -> int:
    """All that admission will ask for the server of ``ask`` (`engines.wanted_bytes`: the engine's own figure, or the weights with
    their KV and prompt cache). 0 for settings that cannot be sized: the start says what is wrong."""
    try:
        return engines.wanted_bytes(ask.server_spec(ask.port))
    except (OSError, TypeError, ValueError, ServerFailed):
        return 0


def more_for(ask: Ask, spare: list[Held], room: int, given_back: Callable[[Held], int]) -> list[Held]:
    """The idle servers of ``spare`` to stop as well, oldest first, where the weights fit ``room`` but all that admission will ask for
    the server does not. None of them when stopping every one would not be enough."""
    takes = _takes(ask)
    more: list[Held] = []
    for held in spare:
        if takes <= room:
            break
        more.append(held)
        room += given_back(held)
    return more if takes <= room else []
