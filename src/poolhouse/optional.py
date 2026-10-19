"""Modules of the experimental layer, loaded by name when the build holds them."""

from __future__ import annotations

from importlib import import_module
from importlib.util import find_spec
from types import ModuleType

__all__ = ["command", "held", "load", "module"]


def held(name: str) -> bool:
    """Whether this build holds the module ``name``, without importing it."""
    return find_spec(name) is not None


def command(name: str, args: str = "") -> str:
    """``python -m NAME ARGS`` when this build holds the module ``name``, else an empty string."""
    return f"python -m {name} {args}".strip() if held(name) else ""


def module(name: str) -> ModuleType:
    """The module ``name``; raises ModuleNotFoundError when this build leaves it out."""
    return import_module(name)


def load(name: str) -> ModuleType | None:
    """The module ``name``, or None when this build leaves it out."""
    try:
        return import_module(name)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if name == missing or name.startswith(missing + "."):
            return None
        raise
