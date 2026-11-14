"""Names that load their module on first use: a package's exports, and an optional dependency a caller may never need."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from importlib import import_module
from typing import Any

__all__ = ["exports", "load"]


def exports(package: str, namespace: dict[str, Any], home: Mapping[str, tuple[str, str]],
            ) -> tuple[Callable[[str], Any], Callable[[], list[str]]]:
    """The module-level ``__getattr__`` and ``__dir__`` of ``package``, for the names in ``home``.

    ``home`` maps an exported name to ``(module, name in that module)``. The first use of a name imports
    its module and binds the value in ``namespace``, so later uses are plain attribute reads. Importing one
    submodule of the package (`poolhouse.serve.backend`) then loads only what that submodule imports, not
    every module the package re-exports.
    """
    def resolve(name: str) -> Any:
        where = home.get(name)
        if where is None:
            raise AttributeError(f"module {package!r} has no attribute {name!r}")
        value = getattr(import_module(where[0]), where[1])
        namespace[name] = value
        return value

    def listing() -> list[str]:
        return sorted({*namespace, *home})

    return resolve, listing


def load(module: str, name: str = "") -> Any:
    """``module``, or ``name`` in it, imported now and not when the caller's own module is.

    For the one dependency a module has that its lightest caller never uses (the pool's sentinel behind a
    default policy, the node's request queue behind an HTTP call), so that caller does not pay for the pool.
    It is not for a cycle: a module that needs another at import time imports it at the top.
    """
    found = import_module(module)
    return getattr(found, name) if name else found
