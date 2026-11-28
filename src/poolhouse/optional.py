"""Modules of the experimental layer and libraries a machine may lack, loaded by name when it holds them."""

from __future__ import annotations

import sys
from importlib import import_module
from importlib.util import find_spec
from types import ModuleType

__all__ = ["command", "held", "load", "mlx_core", "module"]


def mlx_core() -> ModuleType:
    """``mlx.core``, for a caller that may go on without it. A try that fails (no Metal device, say) is remembered, so every later import of it
    in this process raises `ImportError`: a second try at the half-loaded extension ends the process."""
    try:
        return import_module("mlx.core")
    except (ImportError, OSError, RuntimeError):
        sys.modules["mlx.core"] = None   # an import of a name held as None raises ImportError
        raise


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
