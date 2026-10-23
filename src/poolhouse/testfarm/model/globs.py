"""Globs with ``*``, ``**`` and ``?`` only, matched in time proportional to the text times the pattern."""

from __future__ import annotations

__all__ = ["file_glob_refusal", "match"]

_STAR, _DOUBLE, _ONE = "*", "**", "?"


def _tokens(pattern: str) -> list[str]:
    tokens, index = [], 0
    while index < len(pattern):
        if pattern.startswith("**", index):
            tokens.append(_DOUBLE)
            index += 2
            while pattern.startswith("*", index):
                index += 1
        else:
            tokens.append(_STAR if pattern[index] == "*" else _ONE if pattern[index] == "?" else "=" + pattern[index])
            index += 1
    return tokens


def _closure(tokens: list[str], states: set[int]) -> set[int]:
    pending, seen = list(states), set(states)
    while pending:
        index = pending.pop()
        if index >= len(tokens) or tokens[index] not in (_STAR, _DOUBLE):
            continue
        reachable = [index + 1]
        if tokens[index] == _DOUBLE and tokens[index + 1:index + 2] == ["=/"]:
            reachable.append(index + 2)
        for target in reachable:
            if target not in seen:
                seen.add(target)
                pending.append(target)
    return seen


def match(pattern: str, text: str) -> bool:
    """Whether ``text`` is ``pattern``: ``?`` is one character other than ``/``, ``*`` any run of characters other than ``/``, ``**`` any
    run of characters, and ``**/`` may be empty. Every other character stands for itself."""
    tokens = _tokens(pattern)
    states = _closure(tokens, {0})
    for char in text:
        following: set[int] = set()
        for index in states:
            if index >= len(tokens):
                continue
            token = tokens[index]
            if token == _DOUBLE or (token == _STAR and char != "/"):
                following.add(index)
            elif (token == _ONE and char != "/") or token == "=" + char:
                following.add(index + 1)
        if not following:
            return False
        states = _closure(tokens, following)
    return len(tokens) in states


def file_glob_refusal(pattern: object) -> str | None:
    """The reason ``pattern`` is not a glob of files under the project directory, or None."""
    if not isinstance(pattern, str) or not pattern:
        return "a glob is at least one character of text"
    if "\x00" in pattern:
        return "a glob holds no NUL byte"
    if pattern.startswith("/") or pattern.startswith("\\") or (len(pattern) > 1 and pattern[1] == ":"):
        return "a glob is relative to the project directory"
    if ".." in pattern.split("/"):
        return "a glob stays inside the project directory (no '..')"
    return None
