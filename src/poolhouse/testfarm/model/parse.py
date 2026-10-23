"""testfarm.toml bytes to a validated `Config`: strict keys, kinds, cross-references between tables and the hang-guard rule."""

from __future__ import annotations

import hashlib
import tomllib
from collections.abc import Mapping

from poolhouse.testfarm.model import kinds as k
from poolhouse.testfarm.model.config import (
    CommandUnit,
    Config,
    ConfigError,
    DepsSpec,
    FailfirstSpec,
    HooksSpec,
    LabelSpec,
    ListSpec,
    RequiresSpec,
    ReuseSpec,
    RunSpec,
    ScheduleSpec,
    SetSpec,
    Step,
)
from poolhouse.testfarm.model.schema import TABLES, TOP

__all__ = ["parse"]

_PRESET_KEYS = tuple(key for key in TOP if key not in ("schema", "preset", "presets"))


def _read(data: bytes) -> dict:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ConfigError(f"testfarm.toml: not UTF-8 text (byte {error.start})") from None
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"testfarm.toml: not valid TOML: {error}") from None
    except (RecursionError, MemoryError):
        raise ConfigError("testfarm.toml: nested too deeply to read") from None
    except ValueError:
        raise ConfigError("testfarm.toml: a number is longer than the parser reads") from None


def _fields(where: str, table: object, section: str) -> dict:
    """The values of one table, each checked by the kind its key holds."""
    keys = TABLES[section]
    return {key: keys[key](f"{where} {key}", value) for key, value in k.strict(where, table, keys).items()}


def _merge(base: Mapping, over: Mapping) -> dict:
    merged = dict(base)
    for key, value in over.items():
        merged[key] = _merge(merged[key], value) if isinstance(value, dict) and isinstance(merged.get(key), dict) else value
    return merged


def _by_name(where: str, table: object, section: str) -> list[tuple[str, dict]]:
    """``[section.NAME]`` tables as (name, raw table) tuples."""
    if not isinstance(table, dict):
        raise k.fail(where, "must hold one table per name, such as [" + where.strip("[]") + ".NAME]")
    return [(k.name(f"{where}.{name}", name), k.strict(f"[{section}.{name}]", value, TABLES[section])) for name, value in table.items()]


def _array(where: str, value: object) -> list:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise k.fail(where, f"must be written as [[{where.strip('[]')}]] tables")
    return value


def _resolve(raw: dict) -> tuple[dict, str | None, tuple[str, ...]]:
    """``raw`` with its preset's table under it; the preset's name; the sentences about keys ignored."""
    if raw.get("schema") != 1 or isinstance(raw.get("schema"), bool):
        raise k.fail("schema", "must be 1 (the schema version this runner reads)" if "schema" in raw else "is required: write schema = 1")
    name = raw.get("preset")
    if name is None:
        k.strict("top level", raw, TOP)
        return raw, None, ()
    k.name("preset", name)
    k.strict("top level", raw, TOP)
    defined = raw.get("presets", {})
    if name not in defined:
        have = ", ".join(sorted(defined)) or "none"
        raise k.fail("preset", f"{name!r} is not a preset this install ships or this file defines under [presets.NAME] (defined here: {have})")
    notes = ()
    table = k.strict(f"[presets.{name}]", defined[name], (*_PRESET_KEYS, "confine"))
    if "confine" in table:
        notes = (f"[presets.{name}] confine is ignored: a project cannot widen its own sandbox",)
        table = {key: value for key, value in table.items() if key != "confine"}
    return _merge(table, {key: value for key, value in raw.items() if key not in ("preset", "presets")}), name, notes


def _list_spec(table: dict) -> ListSpec | None:
    if "list" not in table:
        return None
    spec = ListSpec(**{key.replace("-", "_"): value for key, value in _fields("[list]", table["list"], "list").items()})
    if spec.run is None and spec.glob is None:
        raise k.fail("[list]", "needs run (the command that prints the ids) or glob")
    return spec


def _run_spec(table: dict) -> RunSpec | None:
    if "run" not in table:
        return None
    values = _fields("[run]", table["run"], "run")
    spec = RunSpec(**values)
    if spec.argv is None and spec.slice is None:
        raise k.fail("[run]", "needs argv (the command that runs the ids)")
    if spec.slice is not None:
        wanted = ("{slice_index}", "{slice_count}")
        for holder in wanted:
            if sum(part.count(holder) for part in spec.slice) != 1:
                raise k.fail("[run] slice", f"must hold exactly one {holder}")
    if spec.slice is not None and spec.slice_base is None:
        raise k.fail("[run] slice_base", "is required whenever slice is set: 0 or 1, where the project's tool starts counting its slices")
    if "list" in table and spec.argv is not None and not any(part == "{ids}" or "{idfile}" in part for part in spec.argv):
        raise k.fail("[run] argv", "must hold {ids} or {idfile} so the ids reach the command when there is a [list]")
    return spec


def _deps_spec(table: dict) -> DepsSpec:
    values = _fields("[deps]", table.get("deps", {}), "deps")
    if "self" in values:
        values["self_files"] = values.pop("self")
    return DepsSpec(**values)


def _step(raw: dict) -> Step:
    where = "[[step]]"
    keys = TABLES["step"]
    known = k.strict(where, raw, keys)
    if "name" not in known or "run" not in known:
        raise k.fail(where, "needs name and run")
    return Step(**{key: keys[key](f"[step {known['name']}] {key}", value) for key, value in known.items()})


def _label(name: str, raw: dict) -> LabelSpec:
    keys = TABLES["label"]
    return LabelSpec(name=name, **{key: keys[key](f"[label.{name}] {key}", value) for key, value in raw.items()})


def _set(name: str, raw: dict) -> SetSpec:
    keys = TABLES["set"]
    return SetSpec(name=name, **{key: keys[key](f"[set.{name}] {key}", value) for key, value in raw.items()})


def _unit(raw: dict) -> CommandUnit:
    keys = TABLES["unit"]
    known = k.strict("[[unit]]", raw, keys)
    if "name" not in known or "run" not in known:
        raise k.fail("[[unit]]", "needs name and run")
    return CommandUnit(**{key: keys[key](f"[unit {known['name']}] {key}", value) for key, value in known.items()})


def _unique(where: str, items: list[str]) -> None:
    for index, item in enumerate(items):
        if item in items[:index]:
            raise k.fail(where, f"{item!r} is named twice")


def _references(config: Config) -> None:
    """Every ``{step:NAME}``, ``needs`` and label a table names is one the file defines."""
    steps = {step.name for step in config.steps}
    commands = [("[list] run", config.list.run if config.list else None), ("[run] argv", config.run.argv if config.run else None),
                ("[deps] command", config.deps.command)]
    commands += [(f"[step {step.name}] run", step.run) for step in config.steps]
    commands += [(f"[unit {unit.name}] run", unit.run) for unit in config.units]
    for where, command in commands:
        for part in command or ():
            for holder in k.placeholders(part, where):
                kind, colon, target = holder.partition(":")
                if colon and kind in ("step", "shared") and target not in steps:
                    raise k.fail(where, f"{{{holder}}} names no step; the steps are {', '.join(sorted(steps)) or 'none'}")
    needs = [("[run] needs", config.run.needs if config.run else None)] + [(f"[label.{label.name}] needs", label.needs) for label in config.labels]
    for where, wanted in needs:
        for name in wanted or ():
            if name not in steps:
                raise k.fail(where, f"{name!r} names no step; the steps are {', '.join(sorted(steps)) or 'none'}")
    _unique("[[step]]", [step.name for step in config.steps])
    _unique("[[unit]]", [unit.name for unit in config.units])


def _hang_guard(config: Config) -> None:
    """A run with no hang guard is refused: both margins, or a timeout on every command the runner starts."""
    schedule = config.schedule
    if schedule.silence_margin is not None and schedule.total_margin is not None:
        return
    unguarded = []
    if config.list and config.list.run and config.list.timeout is None:
        unguarded.append("LIST")
    if config.run and config.run.argv and config.run.timeout is None:
        unguarded.append("RUN")
    if config.deps.command and config.deps.timeout is None:
        unguarded.append("the deps command")
    if (config.hooks.before_plan or config.hooks.before_run or config.hooks.after_run) and config.hooks.timeout is None:
        unguarded.append("the hooks")
    unguarded += [f"step {step.name}" for step in config.steps if step.timeout is None]
    unguarded += [f"unit {unit.name}" for unit in config.units if unit.timeout is None]
    if unguarded:
        raise ConfigError(f"testfarm.toml: [schedule] silence_margin and total_margin are not both set, and {unguarded[0]} has no timeout: "
                          "a run with no hang guard is not allowed")


def parse(data: bytes) -> Config:
    """The `Config` the bytes of a testfarm.toml describe, or `ConfigError` saying in one sentence what is wrong with them."""
    raw, preset, notes = _resolve(_read(data))
    steps = tuple(_step(item) for item in _array("[[step]]", raw.get("step", [])))
    units = tuple(_unit(item) for item in _array("[[unit]]", raw.get("unit", [])))
    config = Config(
        sha256=hashlib.sha256(data).hexdigest(), preset=preset,
        project_name=_fields("[project]", raw.get("project", {}), "project").get("name"),
        tree_include=_fields("[tree]", raw.get("tree", {}), "tree").get("include", ()),
        tree_exclude=_fields("[tree]", raw.get("tree", {}), "tree").get("exclude", ()),
        list=_list_spec(raw), run=_run_spec(raw), deps=_deps_spec(raw), steps=steps,
        labels=tuple(_label(name, item) for name, item in _by_name("[label]", raw.get("label", {}), "label")),
        sets=tuple(_set(name, item) for name, item in _by_name("[set]", raw.get("set", {}), "set")),
        reuse=ReuseSpec(**_fields("[reuse]", raw.get("reuse", {}), "reuse")),
        failfirst=FailfirstSpec(**_fields("[failfirst]", raw.get("failfirst", {}), "failfirst")),
        schedule=ScheduleSpec(**_fields("[schedule]", raw.get("schedule", {}), "schedule")),
        requires=RequiresSpec(**_fields("[requires]", raw.get("requires", {}), "requires")),
        units=units, hooks=HooksSpec(**_fields("[hooks]", raw.get("hooks", {}), "hooks")), notes=notes)
    if config.list is None and config.run is None and not units:
        raise k.fail("top level", "describes nothing to run: give [list] and [run], [run], or [[unit]] tables")
    _references(config)
    _hang_guard(config)
    return config
