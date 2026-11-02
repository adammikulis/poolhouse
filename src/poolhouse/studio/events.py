"""Strict service-owned event envelopes; payload fields never carry authority."""
from __future__ import annotations

import copy
import json

from poolhouse.graph.guard import MAX_BODY

from .contracts import _finite
from .store import digest

FIELDS = {"id", "subscription", "sequence", "source", "generation", "payload", "digest", "input_node", "output_nodes"}


def checked_event(value: dict) -> dict:
    if (not isinstance(value, dict) or set(value) != FIELDS or not _finite(value)
            or any(not isinstance(value[name], str) or not value[name] or len(value[name]) > 256
                   for name in ("id", "subscription", "source", "generation", "digest", "input_node"))
            or type(value["sequence"]) is not int or value["sequence"] < 1
            or not isinstance(value["payload"], dict) or not isinstance(value["output_nodes"], list)
            or any(not isinstance(node, str) for node in value["output_nodes"])
            or len(json.dumps(value["payload"]).encode()) > MAX_BODY
            or digest(value["payload"]) != value["digest"]):
        raise ValueError("Invalid subscription event envelope.")
    return copy.deepcopy(value)
