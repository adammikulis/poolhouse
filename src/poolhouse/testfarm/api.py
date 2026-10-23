"""The Python surface of the test runner: what ``ph.test`` offers besides running tests on other devices."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from poolhouse import errors
from poolhouse.testfarm.core import project, select as selection
from poolhouse.testfarm.model import Config, ConfigError, Unit

__all__ = ["config", "units"]


def config(root: str | Path = ".") -> Config:
    """The validated testfarm.toml of the project at ``root``. Raises `Error` with one sentence when the file is missing or wrong."""
    try:
        return project.load(Path(root))
    except ConfigError as error:
        raise errors.Error(str(error)) from error


def units(root: str | Path = ".", select: Sequence[str] = ()) -> list[Unit]:
    """The units the project's LIST prints, run confined, narrowed by ``select``: ``@NAME`` for a set, an id, a glob over ids or a unit
    name. Raises `Error` when the file is wrong, LIST fails or prints nothing, or the selection matches no unit."""
    try:
        loaded = project.load(Path(root))
        return list(selection.select(project.list_units(Path(root), loaded).units, loaded, select))
    except ConfigError as error:
        raise errors.Error(str(error)) from error
