"""What a guest's request may carry to the model server. Everything not listed is dropped: a slot (``id_slot``), the server's cache
switches, an adapter, a prompt template, a second choice (``n``). A picture, sound or file must be in the request itself (a ``data:``
URL or base64): llama-server fetches an ``http`` one, which would let a guest reach into the host's network. A schema may refer only
to itself."""

from __future__ import annotations

import json
from typing import Any

__all__ = ["ROUTES", "clean"]

_SAMPLING = ("temperature", "top_p", "top_k", "min_p", "seed", "presence_penalty", "frequency_penalty", "repeat_penalty")
ROUTES: dict[str, tuple[tuple[str, ...], str]] = {
    "/v1/chat/completions": (("model", "messages", "stream", "stream_options", "stop", "tools", "tool_choice", "parallel_tool_calls",
                              "response_format", "json_schema", "grammar", "reasoning_effort", "reasoning_format",
                              "chat_template_kwargs", "logprobs", "top_logprobs", *_SAMPLING), "max_tokens"),
    "/v1/messages": (("model", "messages", "system", "stream", "stop_sequences", "tools", "tool_choice", "thinking", *_SAMPLING),
                     "max_tokens"),
    "/v1/messages/count_tokens": (("model", "messages", "system", "tools", "tool_choice", "thinking"), ""),
    "/v1/responses": (("model", "input", "instructions", "stream", "tools", "tool_choice", "parallel_tool_calls", "reasoning", "text",
                       *_SAMPLING), "max_output_tokens"),
}
"""Each path a guest may POST to: the fields it passes on, and the field that bounds the reply ("" where nothing is generated)."""

_ASKED_LIMIT = ("max_tokens", "max_completion_tokens", "max_output_tokens")
_LINKS = ("image_url", "video_url", "audio_url", "file_url")
MOST_DEPTH = 64


def _held_here(value: object) -> bool:
    if isinstance(value, dict):
        value = value.get("url", value.get("data"))
        if value is None:
            return True
    return isinstance(value, str) and value.startswith("data:")


def _check(value: Any, depth: int = 0) -> None:
    if depth > MOST_DEPTH:
        raise ValueError("the request is nested too deeply")
    if isinstance(value, list):
        for one in value:
            _check(one, depth + 1)
        return
    if not isinstance(value, dict):
        return
    if "$ref" in value and isinstance(value["$ref"], str) and not value["$ref"].startswith("#"):
        raise ValueError("a schema may refer only to itself")
    media = str(value.get("type", "")) in ("image", "image_url", "input_image", "input_file", "document", "video_url", "input_video")
    for key, one in value.items():
        if key in _LINKS and not _held_here(one):
            raise ValueError("pictures, sound and files are sent inside the request, never as a link")
        if media and ((key == "url" and not _held_here(one))
                      or (key == "source" and not (isinstance(one, dict) and one.get("type") in ("base64", "text")))):
            raise ValueError("pictures, sound and files are sent inside the request, never as a link")
        if not (key == "input" and value.get("type") == "tool_use"):   # a tool call's own arguments are data, read by nobody here
            _check(one, depth + 1)


def clean(path: str, raw: bytes, most_output: int) -> bytes:
    """The request the model server is sent for a guest's ``raw`` request to ``path``; `ValueError`, quoting nothing of it, when it
    is not one."""
    fields, bound = ROUTES[path]
    try:
        asked = json.loads(raw)
    except (ValueError, RecursionError):
        raise ValueError("the request is not JSON") from None
    if not isinstance(asked, dict):
        raise ValueError("the request is a JSON object")
    sent = {key: asked[key] for key in fields if key in asked}
    _check(sent)
    if bound:
        wanted = next((asked[k] for k in _ASKED_LIMIT if type(asked.get(k)) is int and asked[k] > 0), most_output)
        sent[bound] = min(wanted, most_output)
    return json.dumps(sent).encode()
