"""A project on disk: its testfarm.toml, the units its LIST prints and what `check` says about them."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from poolhouse.testfarm.core import spawn, tree
from poolhouse.testfarm.model import Config, ConfigError, Unit, ids, parse, units_of
from poolhouse.testfarm.model.units import read_rows

__all__ = ["CONFIG_NAME", "Listed", "check", "list_units", "load", "report"]

CONFIG_NAME = "testfarm.toml"


@dataclass(frozen=True, slots=True)
class Listed:
    """The units LIST printed and how many lines the project's ``starts`` and ``ends`` left out."""

    units: tuple[Unit, ...]
    ids: int
    ignored: int


def load(root: Path) -> Config:
    """The project's validated testfarm.toml, or `ConfigError` with the way to make one."""
    path = Path(root) / CONFIG_NAME
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise ConfigError(f"no {CONFIG_NAME} in {root}: run `poolhouse-test init` there to write one") from None
    except OSError as error:
        raise ConfigError(f"{path} cannot be read: {error.strerror or error}") from None
    return parse(data)


def _refusal_of(outcome: spawn.Outcome, limit: float) -> str | None:
    if outcome.timed_out:
        return f"LIST ran past its timeout of {limit:g} s and was ended"
    if outcome.returncode != 0:
        first = next((line for line in ids.plain(outcome.stderr.decode("utf-8", "replace")).split("\n") if line.strip()), "")
        return f"LIST exited with status {outcome.returncode}" + (f": {first.strip()}" if first else " and printed nothing on its error output")
    if "�" in outcome.stdout.decode("utf-8", "replace"):
        return "LIST printed text that is not UTF-8"
    return None


def list_units(root: Path, config: Config) -> Listed:
    """Run the project's LIST confined and make units of what it prints; a LIST that fails, is ended or prints no unit is an error."""
    spec = config.list
    if spec is None or spec.run is None:
        return Listed((), 0, 0)
    if spec.timeout is None:
        raise ConfigError("[list] timeout is not set, and no earlier run recorded how long LIST stays silent: set [list] timeout "
                          "so LIST is guarded")
    outcome = spawn.run_confined(spec.run, Path(root), timeout=spec.timeout)
    if (reason := _refusal_of(outcome, spec.timeout)) is not None:
        raise ConfigError(reason)
    refuse = config.run.refuse_prefix if config.run else ()
    rows, ignored = read_rows(outcome.stdout, spec, refuse)
    if not rows and not spec.allow_empty:
        raise ConfigError("LIST printed no unit" + (f" ({ignored} lines were ignored because they do not match list.starts and list.ends)"
                                                    if ignored else "") + "; set [list] allow_empty = true if that is right")
    return Listed(units_of(rows, config), len(rows), ignored)


def report(root: Path, config: Config, listed: Listed, state: Path) -> list[str]:
    """The lines `check` prints."""
    lines = [f"{CONFIG_NAME}: ok (schema 1, sha256 {config.sha256[:12]})", f"project: {config.project_name or Path(root).resolve().name}",
             f"split: {config.split_mode}"]
    if config.list is not None and config.list.run is not None:
        lines.append(f"units: {len(listed.units)} from {listed.ids} ids" + (f"; ignored {listed.ignored} lines" if listed.ignored else ""))
        counts: dict[str, int] = {}
        for unit in listed.units:
            for word in unit.labels:
                counts[word] = counts.get(word, 0) + 1
        lines.append("labels: " + (", ".join(f"{word} {counts[word]}" for word in sorted(counts)) or "none"))
        declared = sum(unit.deps is not None for unit in listed.units)
        lines.append(f"deps: {declared} of {len(listed.units)} units declare their files"
                     + ("; the others depend on the whole tree and are always affected" if declared < len(listed.units) else ""))
    if config.units:
        lines.append("command units: " + ", ".join(unit.name for unit in config.units))
    lines.append("steps: " + (", ".join(step.name for step in config.steps) + " (each runs once per device and is cached by its inputs)"
                              if config.steps else "none"))
    lines += [f"note: {note}" for note in config.notes]
    lines.append(f"state: {state}")
    return lines


def check(root: Path, state: Path) -> list[str]:
    """Validate the project and run its LIST; the lines to print."""
    config = load(root)
    listed = list_units(root, config)
    tree.problems(config, listed.units, tree.files_of(Path(root)))
    return report(root, config, listed, state)
