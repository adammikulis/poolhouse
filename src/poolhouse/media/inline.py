"""The check that a model request carries its pictures, sounds, videos and files as its own bytes, shared by the model bridge, the daemon's relay and the
chat of the web interface."""

from __future__ import annotations

import json
import re
import threading

from poolhouse.json_objects import unique_object

__all__ = ["BUSY", "REFUSAL", "UNREADABLE", "Busy", "Refused", "inline_bytes", "parsed", "relayed", "written"]

KINDS = frozenset({"image_url", "image", "input_image", "input_audio", "audio", "audio_url", "file", "input_file", "video_url", "input_video", "video",
                   "document"})
"""The content parts that carry a picture, a sound, a video or a file, in a message or in a tool's result."""
LOCATIONS = frozenset({"image_url", "audio_url", "video_url", "file_url"})
"""The fields that name where a part's bytes are: a `data:` URL and nothing else."""
CARRIERS = frozenset({"input_audio", "input_video", "video_url", "audio_url", "audio", "video", "image", "input_image", "image_embeds"})
"""The fields whose text a server takes as base64 bytes or as a place to fetch (llama-server reads their `data`, and fetches it when it starts `http`)."""
UNTYPED = LOCATIONS | CARRIERS
"""The fields that make a part with no `type` a media part, as vLLM and SGLang read one."""
HOLDERS = frozenset({"content", "input", "documents", "query"})
"""The fields of a request under which a part with no `type` is read for media."""
TOOL_CALLS = frozenset({"tool_use", "server_tool_use", "mcp_tool_use"})
"""The parts whose `input` is the arguments of a call: text to a server, never media."""
SCHEMAS = frozenset({"tools", "tool_choice", "functions", "response_format", "text", "json_schema"})
"""The top-level fields that describe tools and the shape of an answer: a server reads no media from them."""
BASE64 = re.compile(r"[A-Za-z0-9+/=\s]*")
DEPTH = 24
OUTSIDE, HELD, MEDIA = 0, 1, 2
"""Where a value sits: outside any media, under a field of `HOLDERS`, or inside a media part."""
REFUSAL = "A picture, a sound or a file must be sent as its own bytes (a data: URL), never as an address."
UNREADABLE = "The body is not one JSON object with each field once."
BUSY = "This machine is reading as many large requests as it has room for; ask again in a moment."
TRANSCRIPTIONS = ("/v1/audio/transcriptions", "/audio/transcriptions")
LARGE = 1 << 18
"""A relayed body past this many bytes is read one at a time: reading JSON takes many times its size in memory."""
WAIT_SECONDS = 20.0
_READING = threading.BoundedSemaphore(1)


class Refused(ValueError):
    """A request body that is not passed to a model server; the text says why."""


class Busy(Exception):
    """A large body that found no room to be read in; the sender asks again."""

    def __init__(self) -> None:
        super().__init__(BUSY)


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


def inline_bytes(request: object) -> int:
    """How long the media ``request`` carries is; `Refused` when a media part names a place a server would read instead, or nests past `DEPTH`. The
    fields that describe tools (`SCHEMAS`) and the arguments of a tool call are not read."""
    if not isinstance(request, dict):
        return _carried(request, "", "", OUTSIDE, 0)
    return sum(_carried(each, name, name, HELD if name in HOLDERS else OUTSIDE, 1) for name, each in request.items() if name not in SCHEMAS)


def _carried(value: object, field: str, owner: str, place: int, depth: int) -> int:
    """The media length under ``value``, found in ``field`` of ``owner``; ``place`` says whether a media part (`MEDIA`) or only a field of `HOLDERS`
    (`HELD`) encloses it."""
    if isinstance(value, str):
        return _text(value, field, owner) if place == MEDIA else 0
    if not isinstance(value, (dict, list)):
        return 0
    if depth > DEPTH:
        raise Refused(UNREADABLE)
    if isinstance(value, list):
        return sum(_carried(each, field, owner, place, depth + 1) for each in value)
    kind = value.get("type")
    starts = isinstance(kind, str) and kind in KINDS   # a tool's schema has a property named `type` whose value is a dict, a list unhashable here
    untyped = place == HELD and not isinstance(kind, str) and not UNTYPED.isdisjoint(value)
    if (starts or untyped or (place == MEDIA and field in KINDS | UNTYPED)) and _held(value):
        raise Refused(REFUSAL)
    named = kind if starts else "base64" if field == "source" and kind == "base64" else field
    total = 0
    for name, each in value.items():
        if name == "input" and isinstance(kind, str) and kind in TOOL_CALLS:
            continue
        inside = place == MEDIA or starts or (untyped and name in UNTYPED)
        total += _carried(each, name, named, MEDIA if inside else HELD if place == HELD or name in HOLDERS else OUTSIDE, depth + 1)
    return total


def _held(part: dict) -> bool:
    """Whether ``part`` points at a file the server holds, or its source at a place to fetch."""
    source = part.get("source")
    return "file_id" in part or (isinstance(source, dict) and (source.get("type") in ("url", "file") or "url" in source or "file_id" in source))


def _text(text: str, field: str, owner: str) -> int:
    """The length of ``text`` when it is a media part's bytes, 0 when it is something else; `Refused` when it names a place."""
    if field in LOCATIONS or (field == "url" and owner in KINDS | UNTYPED):
        if not text.startswith("data:"):
            raise Refused(REFUSAL)
    elif field in CARRIERS or (field == "data" and owner in CARRIERS):
        if not text.startswith("data:") and (text.startswith("http") or BASE64.fullmatch(text) is None):
            raise Refused(REFUSAL)
    elif not (field == "data" and owner == "base64"):
        return 0
    return len(text)


def relayed(body: bytes, path: str, content_type: str) -> bytes:
    """``body`` as the daemon passes it to a model server at ``path``, or `Refused`; `Busy` when a large one finds no room. A multipart transcription
    goes as it came: llama-server reads a form's fields as text and its files as bytes, so none of them can name an address."""
    if path in TRANSCRIPTIONS and content_type.startswith("multipart/form-data"):
        return body
    if len(body) <= LARGE:
        return _checked(body)
    if not _READING.acquire(timeout=WAIT_SECONDS):
        raise Busy
    try:
        return _checked(body)
    finally:
        _READING.release()


def _checked(body: bytes) -> bytes:
    found = parsed(body)
    inline_bytes(found)
    return written(found)
