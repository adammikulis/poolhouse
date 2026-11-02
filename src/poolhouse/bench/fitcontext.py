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
from poolhouse.serve.estimate import read_meta
from poolhouse.serve.largest import PAD, trained_window
from poolhouse.serve.suggest import largest_fit

__all__ = ["Context", "DoesNotFit", "Fitted", "context_arg", "resolve", "total_for"]

MARGIN = 0.10
"""The share of a measured fit record's longest context left unused: the record was measured on
one load, and a conversation that runs longer than the cache it was measured with must not swap
(a stated safety margin; the header estimate has none, `largest_fit` already stops at yellow)."""
STEP = PAD

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


def resolve(model: str, *, kv: str, parallel: int = 1, cap: int = 0,
            headed: bool = False) -> Fitted:
    """The largest per-slot context ``model`` holds with ``parallel`` slots and cache ``kv``.

    A measured fit record for the same file is read first, a tenth short of what it says; with
    none, `suggest.largest_fit` answers from the header. Raises `DoesNotFit` when the header
    cannot be read or names no context length, or when nothing is left once the weights are in.
    """
    parallel = max(1, int(parallel))
    path = hub.located(str(model), loose=True)
    meta = read_meta(path) if path else {}
    name = Path(str(model)).name
    try:
        native = trained_window(meta)
        found = _record(str(model), kv, headed)
        if found is not None:
            source, room = "fit record", int(hub.room() or 0)
            raw = (found.at_room(room) if room else found).longest(parallel)
            raw = int(raw * (1 - MARGIN)) // STEP * STEP
        else:
            source, raw = "estimated from the header", largest_fit(path, slots=parallel, kv=kv).context
    except (ValueError, OSError) as why:
        raise DoesNotFit(f"{name}: what fits cannot be worked out ({why})") from why
    per_slot = min(raw, native, *([cap] if cap else []))
    if per_slot <= 0:
        raise DoesNotFit(f"{name} leaves no room for a conversation on this machine "
                         f"({source})")
    return Fitted(per_slot, parallel, source)


def total_for(args: Any, model: str, *, kv: str, default: int = 0,
              headed: bool = False) -> tuple[int, str]:
    """``(total context, label tag)`` for ``model``: the number ``--context`` gave; else
    ``default`` where the caller derives one; else (and with ``fit``) what fits, said on one
    line, with ``-ctxNk`` for the label."""
    given = getattr(args, "context", 0)
    fit = getattr(given, "fit", False)
    if not fit and (int(given or 0) or default):
        return int(given or 0) or default, ""
    slots = int(getattr(args, "parallel", 1) or 1)
    got = resolve(model, kv=kv, parallel=slots, cap=getattr(given, "cap", 0), headed=headed)
    say(got.said())
    return got.total, f"-ctx{got.per_slot // 1024}k"
