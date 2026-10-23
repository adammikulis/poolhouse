"""LIST output to rows, and rows to units: the granule that is scheduled, keyed and reported."""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass

from poolhouse.testfarm.model import globs, ids, kinds
from poolhouse.testfarm.model.config import Config, ConfigError, ListSpec

__all__ = ["Row", "Unit", "read_rows", "units_of"]

_ROW_KEYS = ("id", "labels", "deps", "group", "def", "seconds")


@dataclass(frozen=True, slots=True)
class Row:
    """One id LIST printed, with what the row said about it."""

    id: str
    labels: tuple[str, ...] = ()
    deps: tuple[str, ...] = ()
    group: str | None = None
    digest: str | None = None
    seconds: float | None = None


@dataclass(frozen=True, slots=True)
class Unit:
    """The ids that always run together, with the words on them and the files they depend on (None: the whole tree)."""

    name: str
    ids: tuple[str, ...]
    labels: tuple[str, ...] = ()
    deps: tuple[str, ...] | None = None
    seconds: float | None = None
    digests: tuple[tuple[str, str], ...] = ()


def _refused(number: int, reason: str) -> ConfigError:
    return ConfigError(f"LIST line {number}: {reason}")


def _tree_path(number: int, value: object) -> str:
    if (reason := globs.file_glob_refusal(value)) is not None or "\\" in str(value):
        raise _refused(number, f"deps hold paths relative to the project: {reason or 'no backslash'}")
    return str(value)


def _jsonl(number: int, line: str, refuse: Sequence[str]) -> Row:
    try:
        record = json.loads(line)
    except (ValueError, RecursionError):
        raise _refused(number, "is not a JSON object") from None
    if not isinstance(record, dict) or any(key not in _ROW_KEYS for key in record):
        raise _refused(number, f"is not an object of {', '.join(_ROW_KEYS)}")
    if (reason := ids.refusal(record.get("id"), refuse)) is not None:
        raise _refused(number, reason)
    try:
        labels = kinds.names("labels", record.get("labels", []))
    except ConfigError as error:
        raise _refused(number, str(error)) from None
    deps = record.get("deps", [])
    if not isinstance(deps, list):
        raise _refused(number, "deps is an array of paths")
    seconds = record.get("seconds")
    if seconds is not None and (isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds)
                                or not 0 <= seconds <= threading.TIMEOUT_MAX):
        raise _refused(number, "seconds is a finite number of seconds from zero")
    for key in ("group", "def"):
        if key in record and (not isinstance(record[key], str) or not record[key] or "\x00" in record[key]):
            raise _refused(number, f"{key} is non-empty text")
    if "group" in record and (reason := ids.refusal(record["group"])) is not None:
        raise _refused(number, f"group: {reason}")
    return Row(record["id"], labels, tuple(_tree_path(number, item) for item in deps), record.get("group"), record.get("def"),
               None if seconds is None else float(seconds))


def read_rows(output: bytes, spec: ListSpec, refuse_prefix: Sequence[str] = ()) -> tuple[list[Row], int]:
    """The rows LIST printed and how many non-empty lines ``starts`` and ``ends`` left out. Lines end at a newline only, a duplicate id is
    an error, and no error repeats the text of a line."""
    try:
        text = output.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ConfigError(f"LIST printed text that is not UTF-8 (byte {error.start})") from None
    rows: list[Row] = []
    seen: dict[str, int] = {}
    ignored = 0
    for number, line in enumerate(text.split("\n"), 1):
        line = line.removesuffix("\r")
        if not line:
            continue
        if (spec.starts and not line.startswith(spec.starts)) or (spec.ends and not line.endswith(spec.ends)):
            ignored += 1
            continue
        if spec.format == "jsonl":
            row = _jsonl(number, line, refuse_prefix)
        elif (reason := ids.refusal(line, refuse_prefix)) is not None:
            raise _refused(number, reason)
        else:
            row = Row(line)
        if row.id in seen:
            raise _refused(number, f"repeats the id of line {seen[row.id]}")
        seen[row.id] = number
        rows.append(row)
    return rows, ignored


def _group(row: Row, spec: ListSpec | None) -> str:
    if row.group is not None:
        if (reason := ids.refusal(row.group)) is not None:
            raise ConfigError(f"LIST group: {reason}")
        return row.group
    if spec is not None and spec.group and spec.group in row.id:
        group = row.id.split(spec.group, 1)[0]
        if (reason := ids.refusal(group)) is not None:
            raise ConfigError(f"LIST group: {reason}")
        return group
    return row.id


def units_of(rows: Sequence[Row], config: Config) -> tuple[Unit, ...]:
    """The units of ``rows`` under ``config``'s group rule, label matches and dependency globs, sorted by name."""
    grouped: dict[str, list[Row]] = {}
    for row in rows:
        grouped.setdefault(_group(row, config.list), []).append(row)
    made = []
    for name in sorted(grouped, key=lambda text: text.encode("utf-8")):
        members = grouped[name]
        words = {word for row in members for word in row.labels}
        words |= {label.name for label in config.labels if any(globs.match(pattern, row.id) for pattern in label.match for row in members)}
        declared = [item for pattern, items in config.deps.globs if any(globs.match(pattern, row.id) for row in members) for item in items]
        files = tuple(dict.fromkeys([*(path for row in members for path in row.deps), *declared]))
        timed = [row.seconds for row in members]
        made.append(Unit(name, tuple(row.id for row in members), tuple(sorted(words)), files or None,
                         None if None in timed else sum(timed), tuple((row.id, row.digest) for row in members if row.digest)))
    return tuple(made)
