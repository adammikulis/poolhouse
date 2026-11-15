"""Reading a record a newer writer may have added fields to: what this build knows is kept, the rest is named once and ignored."""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["known_only"]

_NAMED: set[tuple[str, str]] = set()
_MAX_NAMED = 256
_SHOWN = 64


def known_only(record: Mapping[str, Any], names: Collection[str], *, what: str) -> dict[str, Any]:
    """``record`` without the keys not in ``names``; each such key is named once per process in a warning.

    An unknown key is only ever dropped: it is never read as a setting, a grant or a comparison input, so a newer writer's
    field cannot widen what this build trusts. Types and required keys are the caller's to check; this does not guess them.
    The name is cut short and the set of names kept is bounded, so a record full of keys cannot fill the log or memory.
    """
    out: dict[str, Any] = {}
    for key, value in record.items():
        if key in names:
            out[key] = value
            continue
        shown = str(key)[:_SHOWN]
        if (what, shown) not in _NAMED and len(_NAMED) < _MAX_NAMED:
            _NAMED.add((what, shown))
            logger.warning("%s has a field %r this build does not know (written by a newer build?): ignored", what, shown)
    return out
