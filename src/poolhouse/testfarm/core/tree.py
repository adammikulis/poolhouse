"""The files of a project directory, and the dependency statements checked against them."""

from __future__ import annotations

from pathlib import Path

from poolhouse.testfarm.model import Config, Unit, globs
from poolhouse.testfarm.model.config import ConfigError

__all__ = ["declared_globs", "files_of", "problems"]


def files_of(root: Path) -> list[str]:
    """The regular files under ``root`` as sorted ``/``-separated relative paths; ``.git`` and links are left out."""
    found = []
    for directory, names, files in Path(root).walk(follow_symlinks=False):
        names[:] = [name for name in names if name != ".git" and not (directory / name).is_symlink()]
        found += [(directory / name).relative_to(root).as_posix() for name in files if not (directory / name).is_symlink()]
    return sorted(found)


def declared_globs(config: Config) -> list[tuple[str, str]]:
    """Every file glob the file declares as a statement of what exists, with the key that holds it."""
    found = [(f"[deps] globs[{pattern!r}]", item) for pattern, items in config.deps.globs for item in items]
    found += [("[deps] everything", item) for item in config.deps.everything] + [("[deps] self", item) for item in config.deps.self_files]
    found += [(f"[step {step.name}] inputs", item) for step in config.steps for item in step.inputs]
    found += [(f"[unit {unit.name}] inputs", item) for unit in config.units for item in unit.inputs]
    return found


def problems(config: Config, units: tuple[Unit, ...], files: list[str]) -> None:
    """Raise `ConfigError` for a declared glob that matches no file, and for a unit that is a file whose dependencies leave it out."""
    for key, pattern in declared_globs(config):
        if not any(globs.match(pattern, path) for path in files):
            raise ConfigError(f"testfarm.toml: {key}: {pattern!r} matches no file; an empty set is not an unknown, so name files that exist")
    present = set(files)
    for unit in units:
        if unit.deps is not None and unit.name in present and not any(globs.match(pattern, unit.name) for pattern in unit.deps):
            raise ConfigError(f"testfarm.toml: the dependencies of {unit.name} leave out {unit.name} itself; "
                              "a unit that is a file depends on its own file")
