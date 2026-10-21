"""Canonical conversation creation intent and retained receipt validation."""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from poolhouse.graph.columns import column
from poolhouse.studio.store import digest

from .conversation_settings import checked
from .conversation_types import safe

ERROR = "This conversation request cannot be reused. Reconnect the original window."


class Conflict(ValueError):
    """The original creation intent cannot be reconciled."""


def operation(value):
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{32}", value) is None:
        raise ValueError("A conversation operation identifier is required.")
    return "conversation-create:" + value


def request(value):
    if not isinstance(value, dict) or set(value) - {"model", "title", "settings"}:
        raise ValueError("Invalid conversation creation fields.")
    result = {"model": value.get("model", ""), "title": value.get("title", ""), "settings": value.get("settings", {})}
    if any(not isinstance(result[key], str) or len(result[key]) > 4096 for key in ("model", "title")):
        raise ValueError("Conversation model and title must be text.")
    if not isinstance(result["settings"], dict):
        raise ValueError("Conversation settings must be an object.")
    checked(result["settings"])
    encoded = json.dumps(result, allow_nan=False, sort_keys=True)
    if len(encoded.encode()) > 64 * 1024:
        raise ValueError("Conversation creation fields exceed their limit.")
    return json.loads(encoded)


def binding(root: Path, identity):
    if (not isinstance(identity, dict) or set(identity) != {"principal", "scope", "device"}
            or any(not isinstance(value, str) or not value or len(value) > 256
                   or any(ord(char) < 33 or ord(char) > 126 for char in value) for value in identity.values())):
        raise PermissionError(ERROR)
    canonical = root.resolve(strict=True)
    info = root.lstat()
    if (canonical != root.absolute() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o022):
        raise PermissionError(ERROR)
    return {**identity, "storage": [str(canonical), info.st_dev, info.st_ino]}


def current(callback):
    if callback() is not True:
        raise PermissionError(ERROR)


def receipt(store, key, bound, *, lookup=False):
    row = store.get_doc(key)
    markers = store.query("MATCH (n:Node {id:$id}) RETURN n.attrs AS attrs", {"id": key})
    if row is None and not markers:
        return None
    if lookup and (not isinstance(row, dict) or row.get("binding") != bound):
        return None
    if (not isinstance(row, dict) or set(row) != {"version", "binding", "request", "response"}
            or type(row["version"]) is not int or row["version"] != 1 or row["binding"] != bound
            or not isinstance(row["response"], dict) or not isinstance(row["response"].get("id"), str)
            or not safe(row["response"]["id"])
            or row["response"].get("operation_id") != key.removeprefix("conversation-create:")
            or len(markers) != 1):
        raise Conflict(ERROR)
    marker = column(markers[0]["attrs"], key)
    edges = store.query("MATCH (n:Node {id:$id})-[:Edge {rel:'created'}]->(c:Node) RETURN c.id AS id", {"id": key})
    if marker != {"digest": digest(row)} or edges != [{"id": "conversation:" + row["response"]["id"]}]:
        raise Conflict(ERROR)
    return row
