"""JSON object hooks that reject ambiguous repeated fields."""

from typing import Any


def unique_object(entries: list[tuple[str, Any]]) -> dict[str, Any]:
    """Return an object whose keys occur once, or raise ValueError."""
    result: dict[str, Any] = {}
    for key, value in entries:
        if key in result:
            raise ValueError("duplicate JSON object field")
        result[key] = value
    return result
