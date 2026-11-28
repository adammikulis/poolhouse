"""Which parts of a model request make the server read somewhere else: a picture, a sound or a file named by an address or a
path instead of sent as bytes, and a JSON schema that refers to another document."""

from __future__ import annotations

__all__ = ["DEPTH", "MEDIA", "REMOTE", "TooDeep", "names_elsewhere"]

MEDIA = frozenset({"image_url", "image", "input_image", "input_audio", "audio", "audio_url", "file", "input_file", "video_url",
                   "input_video", "document"})
"""The content parts that carry a picture, a sound, a video or a file: each is read for a place the server could fetch from."""
LOCATIONS = ("image_url", "audio_url", "video_url", "file_url", "url")
DEPTH = 24
REMOTE = "A picture, a sound or a file must be sent as its own bytes (a data: URL), never as an address."


class TooDeep(ValueError):
    """The request nests further than is read."""


def _remote(part: dict) -> bool:
    """Whether a media part names a place the server could fetch from (a URL that is not `data:`, a file the server holds) and not
    bytes it was sent."""
    for key in (*LOCATIONS, *MEDIA):
        held = part.get(key)
        named = held.get("url") if isinstance(held, dict) else held if key in LOCATIONS else None
        if isinstance(named, str) and not named.startswith("data:"):
            return True
    source = part.get("source")
    return "file_id" in part or (isinstance(source, dict) and (source.get("type") in ("url", "file") or "url" in source or "file_id" in source))


def names_elsewhere(value: object, depth: int = 0) -> bool:
    """Whether anything under ``value``, at any depth a message nests it, names a place the server would read. Raises `TooDeep`."""
    if depth > DEPTH:
        raise TooDeep("the request nests too deeply")
    if isinstance(value, dict):
        kind = value.get("type")
        # a tool's schema has a property named `type` whose value is a dict, a list unhashable here
        if isinstance(kind, str) and kind in MEDIA and _remote(value):
            return True
        referred = value.get("$ref")
        if isinstance(referred, str) and not referred.startswith("#"):
            return True
        return any(names_elsewhere(each, depth + 1) for each in value.values() if isinstance(each, (dict, list)))
    if isinstance(value, list):
        return any(names_elsewhere(each, depth + 1) for each in value)
    return False
