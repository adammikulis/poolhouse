"""The check that a model request carries its pictures, sounds, videos and files as its own bytes, shared by the model bridge and the daemon's relay."""

from __future__ import annotations

import json
import re

from poolhouse.json_objects import unique_object

__all__ = ["REFUSAL", "UNREADABLE", "Refused", "inline_bytes", "parsed", "relayed", "written"]

KINDS = frozenset({"image_url", "image", "input_image", "input_audio", "audio", "audio_url", "file", "input_file", "video_url", "input_video", "video",
                   "document"})
"""The content parts that carry a picture, a sound, a video or a file, in a message or in a tool's result."""
LOCATIONS = frozenset({"image_url", "audio_url", "video_url", "file_url"})
"""The fields that name where a part's bytes are: a `data:` URL and nothing else."""
CARRIERS = frozenset({"input_audio", "input_video", "video_url", "audio_url", "audio", "video"})
"""The fields whose `data` llama-server takes as base64 bytes, and fetches when it starts `http`."""
BASE64 = re.compile(r"[A-Za-z0-9+/=\s]*")
DEPTH = 24
REFUSAL = "A picture, a sound or a file must be sent as its own bytes (a data: URL), never as an address."
UNREADABLE = "The body is not one JSON object with each field once."
TRANSCRIPTIONS = ("/v1/audio/transcriptions", "/audio/transcriptions")


class Refused(ValueError):
    """A request body that is not passed to a model server; the text says why."""


def parsed(body: bytes) -> object:
    """``body`` as one JSON document with each field once and no constant that is not a number, or `Refused`."""
    def reject(constant: str) -> None:
        raise ValueError(constant)

    try:
        return json.loads(body, object_pairs_hook=unique_object, parse_constant=reject)
    except (ValueError, RecursionError):
        raise Refused(UNREADABLE) from None


def written(value: object) -> bytes:
    """``value`` as the JSON bytes a model server is sent, or `Refused`."""
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, RecursionError):
        raise Refused(UNREADABLE) from None


def inline_bytes(value: object) -> int:
    """How long the media ``value`` carries is; `Refused` when a media part names a place a server would read instead, or nests past `DEPTH`."""
    return _carried(value, "", "", False, 0)


def _carried(value: object, field: str, owner: str, inside: bool, depth: int) -> int:
    """The media length under ``value``, found in ``field`` of ``owner``; ``inside`` says a media part encloses it."""
    if isinstance(value, str):
        return _text(value, field, owner) if inside else 0
    if not isinstance(value, (dict, list)):
        return 0
    if depth > DEPTH:
        raise Refused(UNREADABLE)
    if isinstance(value, list):
        return sum(_carried(each, field, owner, inside, depth + 1) for each in value)
    kind = value.get("type")
    starts = isinstance(kind, str) and kind in KINDS   # a tool's schema has a property named `type` whose value is a dict, a list unhashable here
    if (starts or (inside and field in KINDS | CARRIERS | LOCATIONS)) and _held(value):
        raise Refused(REFUSAL)
    named = kind if starts else "base64" if field == "source" and kind == "base64" else field
    return sum(_carried(each, name, named, inside or starts, depth + 1) for name, each in value.items())


def _held(part: dict) -> bool:
    """Whether ``part`` points at a file the server holds, or its source at a place to fetch."""
    source = part.get("source")
    return "file_id" in part or (isinstance(source, dict) and (source.get("type") in ("url", "file") or "url" in source or "file_id" in source))


def _text(text: str, field: str, owner: str) -> int:
    """The length of ``text`` when it is a media part's bytes, 0 when it is something else; `Refused` when it names a place."""
    if field in LOCATIONS or (field == "url" and owner in KINDS | CARRIERS | LOCATIONS):
        if not text.startswith("data:"):
            raise Refused(REFUSAL)
    elif field in CARRIERS or (field == "data" and owner in CARRIERS):
        if not text.startswith("data:") and (text.startswith("http") or BASE64.fullmatch(text) is None):
            raise Refused(REFUSAL)
    elif not (field == "data" and owner == "base64"):
        return 0
    return len(text)


def relayed(body: bytes, path: str, content_type: str) -> bytes:
    """``body`` as the daemon passes it to a model server at ``path``, or `Refused`. A multipart transcription goes as it came: llama-server reads a form's
    fields as text and its files as bytes, so none of them can name an address."""
    if path in TRANSCRIPTIONS and content_type.startswith("multipart/form-data"):
        return body
    found = parsed(body)
    inline_bytes(found)
    return written(found)
