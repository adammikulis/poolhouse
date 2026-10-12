"""Thinking levels: ``off, low, medium, high, unlimited`` as served configurations.

A level is a ``--reasoning-budget`` (llama-server's cap on thinking tokens) and, for a chat
template that takes one, the template's own switch and effort, passed as
``--chat-template-kwargs``. Qwen3.8's template has ``enable_thinking`` and a
``reasoning_effort`` of ``xhigh`` (the default, which ``high`` also names), ``medium`` and
``low``; a template with only the switch gets the budgets; one with neither runs once.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from poolhouse import hub
from poolhouse.log import say
from poolhouse.serve.chat_template import template_of

__all__ = ["EFFORT", "LEVELS", "Level", "Plan", "expand", "level_arg", "parse_levels", "plan"]

LEVELS: dict[str, int] = {"off": 0, "low": 1024, "medium": 4096, "high": 16384, "unlimited": -1}
"""Each level's ``--reasoning-budget``: 0 ends thinking at once, -1 never stops it."""

EFFORT: dict[str, str] = {"low": "low", "medium": "medium", "high": "high", "unlimited": "xhigh"}
"""The ``reasoning_effort`` a Qwen3.8 template is given at each level."""

BUDGETS = "token budgets"
EFFORTS = "effort levels of the chat template"
NONE = "no thinking"


@dataclass(frozen=True, slots=True, eq=False)
class Level:
    """One thinking level for one model. ``name`` "" and ``budget`` None is the model served
    as it is, for a model with no thinking to vary."""

    name: str
    budget: int | None
    kwargs: dict[str, Any] = field(default_factory=dict)

    def over(self, config: Any) -> Any:
        """``config`` served at this level: its budget, and its template arguments laid after
        the extra arguments it already carries."""
        if self.budget is None:
            return config
        extra = tuple(config.serving.extra_args)
        if self.kwargs:
            extra += ("--chat-template-kwargs", json.dumps(self.kwargs, separators=(",", ":")))
        return config.over(reasoning_budget=self.budget, extra_args=extra)


@dataclass(frozen=True, slots=True)
class Plan:
    """The levels one model is measured at, and what they are made of."""

    levels: tuple[Level, ...]
    note: str


def level_arg(text: str) -> str:
    """The argparse type of ``--thinking``: one level's name."""
    name = str(text).strip().lower()
    if name not in LEVELS:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a thinking level; the levels are {', '.join(LEVELS)}")
    return name


def parse_levels(text: str) -> list[Level]:
    """The argparse type of ``--thinking-levels``: names, comma-separated, none twice."""
    names = [level_arg(part) for part in str(text).split(",")]
    if len(set(names)) != len(names):
        raise argparse.ArgumentTypeError(f"{text!r} names a thinking level twice")
    return [Level(name, LEVELS[name]) for name in names]


def _kwargs(name: str, template: str) -> dict[str, Any]:
    """What a level sets in the chat template: the switch, and the effort where it has one."""
    if name == "off":
        return {"enable_thinking": False} if "enable_thinking" in template else {}
    if "reasoning_effort" in template:
        return {"enable_thinking": True, "reasoning_effort": EFFORT[name]}
    return {}


def plan(model: str, levels: list[Level]) -> Plan:
    """``levels`` made to fit what ``model``'s chat template supports."""
    template = template_of(model)
    if "enable_thinking" not in template and "reasoning_effort" not in template:
        return Plan((Level("", None),), NONE)
    note = EFFORTS if "reasoning_effort" in template else BUDGETS
    return Plan(tuple(Level(lv.name, lv.budget, _kwargs(lv.name, template)) for lv in levels),
                note)


def _wanted(args: Any) -> list[Level]:
    levels = getattr(args, "thinking_levels", None)
    if levels:
        return list(levels)
    one = getattr(args, "thinking", None)
    return [Level(one, LEVELS[one])] if one else []


def expand(args: Any) -> Iterator[tuple[int, str, Level | None]]:
    """``(position, model, level)`` for every ``--serve``'d model at every level it supports,
    in order, one model's levels together; ``level`` is None when no level was asked for."""
    wanted = _wanted(args)
    for n, model in enumerate(getattr(args, "serve", []) or []):
        if not wanted:
            yield n, model, None
            continue
        found = plan(str(hub.located(model, loose=True) or model), wanted)
        say(f"thinking levels for {model}: "
            + (found.note if found.note == NONE else
               f"{', '.join(lv.name for lv in found.levels)} ({found.note})"))
        for level in found.levels:
            yield n, model, level
