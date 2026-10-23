"""The value kinds a testfarm.toml key can hold. Each checks one value and returns it in the form `Config` keeps; none takes a pattern."""

from __future__ import annotations

import difflib
import math
import re
import threading
from collections.abc import Callable, Iterable

from poolhouse.testfarm.model import globs
from poolhouse.testfarm.model.config import ConfigError

__all__ = ["Kind", "argv", "argvs", "boolean", "choice", "env_names", "envmap", "exe_list", "fail", "file_glob", "file_globs", "glob_map", "lease",
           "literal", "margin", "name", "names", "positive_int", "rate", "seconds", "slice_count", "strict", "strings", "template", "text", "version",
           "word_list"]

Kind = Callable[[str, object], object]

NAME = re.compile(r"[a-z0-9_-]+")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
WORD = re.compile(r"[A-Za-z0-9_.-]+")
_PLACEHOLDER = re.compile(r"\{\{|\}\}|\{([^{}]*)\}|[{}]")
STEP_FORM = re.compile(r"(step|shared):([a-z0-9_-]+)")


def fail(where: str, message: str) -> ConfigError:
    """The error for a key: ``testfarm.toml: [run] argv: message``."""
    return ConfigError(f"testfarm.toml: {where}: {message}")


def strict(where: str, table: object, allowed: Iterable[str]) -> dict:
    """``table`` as a dict holding only ``allowed`` keys; an unknown key is named with the nearest known one."""
    if not isinstance(table, dict):
        raise fail(where, f"must be a table, not {type(table).__name__}")
    known = sorted(allowed)
    for key in table:
        if key not in known:
            near = difflib.get_close_matches(str(key), known, n=1)
            raise fail(where, f"{key!r} is not a key" + (f"; did you mean {near[0]!r}?" if near else f"; the keys are {', '.join(known)}"))
    return table


def text(where: str, value: object) -> str:
    """A non-empty string with no NUL byte."""
    if not isinstance(value, str) or not value:
        raise fail(where, "must be non-empty text")
    if "\x00" in value:
        raise fail(where, "holds a NUL byte")
    return value


def literal(where: str, value: object) -> str:
    """Literal text a command line compares against (never a pattern)."""
    return text(where, value)


def boolean(where: str, value: object) -> bool:
    """true or false."""
    if not isinstance(value, bool):
        raise fail(where, "must be true or false")
    return value


def choice(*options: str | int) -> Kind:
    """One of the named words or numbers."""
    def check(where: str, value: object) -> str | int:
        if isinstance(value, bool) or value not in options:
            raise fail(where, f"must be one of {', '.join(map(str, options))}")
        return value
    return check


def _number(where: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise fail(where, "must be a number")
    if not math.isfinite(value):
        raise fail(where, f"must be a finite number, not {value}")
    return float(value)


def seconds(where: str, value: object) -> float:
    """A finite number of seconds above zero that a timer of this platform accepts."""
    number = _number(where, value)
    if number <= 0:
        raise fail(where, f"must be above zero, not {value}")
    if number > threading.TIMEOUT_MAX:
        raise fail(where, f"is more seconds than this platform's timers take ({threading.TIMEOUT_MAX:g})")
    return number


def margin(where: str, value: object) -> float:
    """A multiplier of at least 1."""
    number = _number(where, value)
    if number < 1:
        raise fail(where, f"must be at least 1, not {value}")
    return number


def rate(where: str, value: object) -> float:
    """A share above 0 and at most 1."""
    number = _number(where, value)
    if not 0 < number <= 1:
        raise fail(where, f"must be above 0 and at most 1, not {value}")
    return number


def positive_int(where: str, value: object) -> int:
    """A whole number of at least 1."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise fail(where, f"must be a whole number of at least 1, not {value!r}")
    return value


def name(where: str, value: object) -> str:
    """A name of a set, label, step or unit: lower case letters, digits, ``_`` and ``-``."""
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise fail(where, f"{value!r} is not a name (lower case letters, digits, '_' and '-')")
    return value


def version(where: str, value: object) -> str:
    """A version bound such as ``>=3.13``."""
    if not isinstance(value, str) or not re.fullmatch(r"(>=|==|<=|>|<)?[0-9]+(\.[0-9]+)*", value):
        raise fail(where, f"{value!r} is not a version bound such as '>=3.13'")
    return value


def word_list(where: str, value: object) -> tuple[str, ...]:
    """Words of ``[A-Za-z0-9_.-]`` that do not begin with ``-`` or ``.``."""
    items = strings(where, value)
    for item in items:
        if not WORD.fullmatch(item) or item[0] in "-.":
            raise fail(where, f"{item!r} is not a name of letters, digits, '_', '.' and '-' that does not begin with '-' or '.'")
    return items


def _items(where: str, value: object) -> list:
    if not isinstance(value, list):
        raise fail(where, "must be an array")
    return value


def strings(where: str, value: object) -> tuple[str, ...]:
    """An array of non-empty strings."""
    return tuple(text(f"{where}[{index}]", item) for index, item in enumerate(_items(where, value)))


def names(where: str, value: object) -> tuple[str, ...]:
    """An array of names."""
    return tuple(name(f"{where}[{index}]", item) for index, item in enumerate(_items(where, value)))


def file_globs(where: str, value: object) -> tuple[str, ...]:
    """An array of globs of files under the project directory (``*``, ``**`` and ``?`` only)."""
    found = []
    for index, item in enumerate(_items(where, value)):
        if (reason := globs.file_glob_refusal(item)) is not None:
            raise fail(f"{where}[{index}]", reason)
        found.append(item)
    return tuple(found)


def file_glob(where: str, value: object) -> str:
    """One glob (or file name) under the project directory, relative to it."""
    if (reason := globs.file_glob_refusal(value)) is not None:
        raise fail(where, reason)
    return str(value)


def exe_list(where: str, value: object) -> tuple[str, ...]:
    """Programs as ``name`` or ``name>=version``."""
    items = strings(where, value)
    for item in items:
        program, _, bound = item.partition(">=")
        if not WORD.fullmatch(program) or program[0] in "-." or (_ and not re.fullmatch(r"[0-9]+(\.[0-9]+)*", bound)):
            raise fail(where, f"{item!r} is not a program name or name>=version")
    return items


def env_names(where: str, value: object) -> tuple[str, ...]:
    """An array of environment variable names."""
    items = strings(where, value)
    for item in items:
        if not ENV_NAME.fullmatch(item):
            raise fail(where, f"{item!r} is not an environment variable name")
    return items


def slice_count(where: str, value: object) -> int | str:
    """``auto`` or a whole number of at least 1."""
    return value if value == "auto" else positive_int(where, value)


def glob_map(where: str, value: object) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """A table of id patterns to arrays of globs of files."""
    if not isinstance(value, dict):
        raise fail(where, "must be a table of id patterns to arrays of file globs")
    return tuple((text(f"{where}.{key}", key), file_globs(f"{where}[{key!r}]", item)) for key, item in value.items())


def envmap(where: str, value: object) -> tuple[tuple[str, str], ...]:
    """A table of environment variable names to values."""
    if not isinstance(value, dict):
        raise fail(where, "must be a table of variable names to values")
    entries = []
    for key, item in value.items():
        if not ENV_NAME.fullmatch(key):
            raise fail(where, f"{key!r} is not an environment variable name")
        if not isinstance(item, str) or "\x00" in item:
            raise fail(f"{where}.{key}", "must be text with no NUL byte")
        entries.append((key, item))
    return tuple(entries)


def lease(where: str, value: object) -> tuple[tuple[str, int | None], ...]:
    """Resources as ``name`` or ``name:amount``."""
    found = []
    for index, item in enumerate(strings(where, value)):
        label, colon, amount = item.partition(":")
        if not NAME.fullmatch(label) or (amount and not amount.isdecimal()) or (colon and not amount):
            raise fail(f"{where}[{index}]", f"{item!r} is not a resource name or name:amount")
        found.append((label, int(amount) if amount else None))
    return tuple(found)


def placeholders(argument: str, where: str) -> list[str]:
    """The ``{name}`` placeholders of one argument; ``{{`` and ``}}`` are literal braces and any other brace is an error."""
    found = []
    for match in _PLACEHOLDER.finditer(argument):
        token = match.group(0)
        if token in ("{{", "}}"):
            continue
        if match.group(1) is None:
            raise fail(where, f"{argument!r} has a brace that is not a placeholder (write '{{{{' and '}}}}' for literal braces)")
        found.append(match.group(1))
    return found


def template(*allowed: str) -> Kind:
    """Text with ``{name}`` placeholders from ``allowed``."""
    def check(where: str, value: object) -> str:
        line = text(where, value)
        for holder in placeholders(line, where):
            if holder not in allowed:
                raise fail(where, f"{{{holder}}} is not a placeholder here; the placeholders are {', '.join(allowed)}")
        return line
    return check


def argv(allowed: Iterable[str] = (), whole: Iterable[str] = ()) -> Kind:
    """An argument vector (an array of strings, never a shell string) whose placeholders are among ``allowed`` (and ``step:NAME`` and
    ``shared:NAME``); those in ``whole`` fill a whole argument."""
    permitted, alone = frozenset(allowed), frozenset(whole)

    def check(where: str, value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            raise fail(where, "must be an array of strings (an argument vector); a shell string is never run")
        items = strings(where, value)
        if not items:
            raise fail(where, "must hold at least the program")
        for index, item in enumerate(items):
            for holder in placeholders(item, f"{where}[{index}]"):
                if not (holder in permitted or STEP_FORM.fullmatch(holder)):
                    raise fail(f"{where}[{index}]", f"{{{holder}}} is not a placeholder here")
                if index == 0:
                    raise fail(f"{where}[0]", "the program name holds no placeholder")
                if holder in alone and item != "{" + holder + "}":
                    raise fail(f"{where}[{index}]", f"{{{holder}}} is a whole argument of its own")
        return items
    return check


def argvs(allowed: Iterable[str] = (), whole: Iterable[str] = ()) -> Kind:
    """An array of argument vectors."""
    one = argv(allowed, whole)

    def check(where: str, value: object) -> tuple[tuple[str, ...], ...]:
        return tuple(one(f"{where}[{index}]", item) for index, item in enumerate(_items(where, value)))
    return check
