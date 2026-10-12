"""``--context fit[:MAX]``: the largest context one user can hold, worked out per model.

The answer comes from the model's fit record (`poolhouse-serve fit`) for the same file and
cache type, else from its GGUF header the way `poolhouse.serve.estimate` counts a load. A
tenth of it is left over and the result stops at the model's own context and at MAX. Nothing
is served to find out.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poolhouse import hub
from poolhouse.log import say
from poolhouse.serve import fit as fits
from poolhouse.serve.estimate import Setup, _bytes_of, estimate_meta, read_meta

__all__ = ["Context", "DoesNotFit", "Fitted", "context_arg", "resolve", "total_for"]

MARGIN = 0.10
STEP = 256
CEILING = 1 << 24
"""Where the estimate's search stops: past any context a model is trained on."""

_COUNT = re.compile(r"(\d+)([km]?)", re.IGNORECASE)
_UNITS = {"": 1, "k": 1024, "m": 1024 * 1024}


class DoesNotFit(RuntimeError):
    """The model leaves no room for a conversation here, or cannot be sized."""


class Context(int):
    """What ``--context`` was given: its number (0 for ``fit``), whether it asks for the
    largest that fits, and the ceiling ``fit:MAX`` names (0 for none)."""

    fit: bool
    cap: int

    def __new__(cls, value: int = 0, *, fit: bool = False, cap: int = 0) -> Context:
        self = super().__new__(cls, value)
        self.fit, self.cap = fit, cap
        return self


def _count(text: str) -> int:
    got = _COUNT.fullmatch(text.strip())
    if got is None:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a number of tokens; write 32768, 64k, fit or fit:128k")
    return int(got.group(1)) * _UNITS[got.group(2).lower()]


def context_arg(text: str) -> Context:
    """The argparse type of ``--context``: a count, ``fit`` or ``fit:MAX``."""
    word = str(text).strip()
    if word.lower() == "fit":
        return Context(fit=True)
    if word.lower().startswith("fit:"):
        return Context(fit=True, cap=_count(word[4:]))
    return Context(_count(word))


@dataclass(frozen=True, slots=True)
class Fitted:
    """The context ``fit`` resolved to for one model."""

    per_slot: int
    parallel: int
    source: str

    @property
    def total(self) -> int:
        """What the server is asked for: every slot's share together."""
        return self.per_slot * self.parallel

    def said(self) -> str:
        return f"context fit: {self.per_slot} ({self.source})"


def _record(model: str, kv: str, headed: bool) -> fits.Fit | None:
    """The fit record for this file and cache type, preferring one measured with the same
    kind of head (or none) the run serves."""
    named = Path(str(model)).name
    found = [f for f in fits.records() if f.model == named and f.cache_type == kv]
    found.sort(key=lambda f: bool(f.draft) != headed)
    return found[0] if found else None


def _estimated(meta: dict[str, object], weights: int, kv: str, parallel: int, room: int) -> int:
    """The largest per-slot context whose estimated load is within ``room``, in steps."""
    best, low, high = 0, 1, CEILING // STEP
    while low <= high:
        mid = (low + high) // 2
        need = estimate_meta(meta, weights, Setup(context=mid * STEP, parallel=parallel,
                                                  kv_cache_type=kv)).total_bytes
        if need <= room:
            best, low = mid * STEP, mid + 1
        else:
            high = mid - 1
    return best


def resolve(model: str, *, kv: str, parallel: int = 1, cap: int = 0,
            headed: bool = False) -> Fitted:
    """The largest per-slot context ``model`` holds with ``parallel`` slots and cache ``kv``.

    Raises `DoesNotFit` when there is no record and the file cannot be read, or when
    nothing is left once the weights are in.
    """
    parallel = max(1, int(parallel))
    path = hub.located(str(model), loose=True)
    meta = read_meta(path) if path else {}
    arch = str(meta.get("general.architecture") or "")
    native = int(meta.get(f"{arch}.context_length") or 0)  # type: ignore[call-overload]
    room = int(hub.room() or 0)
    found = _record(str(model), kv, headed)
    name = Path(str(model)).name
    if found is not None:
        source = "fit record"
        raw = (found.at_room(room) if room else found).longest(parallel)
    elif path is not None and meta and room:
        source = "estimated from the header"
        raw = _estimated(meta, _bytes_of(Path(path)), kv, parallel, room)
    else:
        raise DoesNotFit(f"{name} has no fit record and its header cannot be read here, so "
                         "what fits cannot be worked out")
    per_slot = int(raw * (1 - MARGIN)) // STEP * STEP
    for ceiling in (native, cap):
        if ceiling:
            per_slot = min(per_slot, ceiling)
    if per_slot <= 0:
        raise DoesNotFit(f"{name} leaves no room for a conversation on this machine "
                         f"({source})")
    return Fitted(per_slot, parallel, source)


def total_for(args: Any, model: str, *, kv: str, default: int,
              headed: bool = False) -> tuple[int, str]:
    """``(total context, label tag)`` for ``model``: the number ``--context`` gave, else
    ``default``; with ``fit``, what fits (said on one line) and ``-ctxNk`` for the label."""
    given = getattr(args, "context", 0)
    if not getattr(given, "fit", False):
        return int(given or 0) or default, ""
    slots = int(getattr(args, "parallel", 1) or 1)
    got = resolve(model, kv=kv, parallel=slots, cap=given.cap, headed=headed)
    say(got.said())
    return got.total, f"-ctx{got.per_slot // 1024}k"
