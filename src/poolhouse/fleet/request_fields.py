"""Typed JSON request fields for workspace and simulation routes."""

import json

from poolhouse.json_objects import unique_object


def object_body(context):
    length = int(context.header("Content-Length", "0") or 0)
    if not 0 <= length <= 10_000_000:
        raise ValueError("Request body must be at most 10 MB")
    value = json.loads(context.handler.rfile.read(length) or b"{}")
    if not isinstance(value, dict):
        raise ValueError("Request body must be a JSON object")
    return value


def field(request, name, kind, default=None):
    value = request.get(name, default)
    if type(value) is not kind:
        raise ValueError(f"{name} must be {kind.__name__}")
    return value


def exact_body(context, keys):
    length = int(context.header('Content-Length', '0') or 0)
    if not 0 <= length <= 64 * 1024:
        raise ValueError('Invalid selection request size')
    body = json.loads(context.handler.rfile.read(length) or b'{}', object_pairs_hook=unique_object)
    if not isinstance(body, dict) or set(body) != set(keys):
        raise ValueError('Invalid selection fields')
    return body

