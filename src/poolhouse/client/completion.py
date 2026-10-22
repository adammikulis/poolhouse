"""Immutable canonical completion data within the trusted Python process."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from poolhouse.fleet.framing import MOST_BODY

_PREPARED = object()


@dataclass(frozen=True, slots=True, init=False)
class PreparedCompletion:
    """Factory-owned canonical SDK arguments within the trusted Python process."""

    model: str
    arguments_json: str
    digest: str
    _origin: object = field(repr=False, compare=False)

    def __new__(cls):
        raise TypeError("Use prepare_completion")

    @property
    def arguments(self) -> dict[str, Any]:
        """Return an independent copy of the checked SDK arguments."""
        return json.loads(checked_completion(self).arguments_json)


def _freeze(model: str, arguments_json: str) -> PreparedCompletion:
    """Freeze canonical arguments from a trusted validated producer."""
    request = object.__new__(PreparedCompletion)
    for name, value in (("model", model), ("arguments_json", arguments_json),
                        ("digest", sha256(arguments_json.encode()).hexdigest()), ("_origin", _PREPARED)):
        object.__setattr__(request, name, value)
    return checked_completion(request)


def checked_completion(request: PreparedCompletion) -> PreparedCompletion:
    """Check factory provenance and canonical request integrity without networking."""
    try:
        if (type(request) is not PreparedCompletion or request._origin is not _PREPARED
                or type(request.model) is not str or not request.model
                or type(request.arguments_json) is not str or type(request.digest) is not str):
            raise ValueError("Invalid prepared completion provenance")
        encoded = request.arguments_json.encode()
        if len(encoded) > MOST_BODY or sha256(encoded).hexdigest() != request.digest:
            raise ValueError("Invalid prepared completion digest or size")
        arguments = json.loads(encoded)
        if (not isinstance(arguments, dict) or arguments.get("model") != request.model
                or json.dumps(arguments, sort_keys=True, separators=(",", ":"), allow_nan=False) != request.arguments_json):
            raise ValueError("Invalid prepared completion canonical arguments")
    except (AttributeError, TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Invalid prepared completion request") from exc
    return request

