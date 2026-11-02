"""A handler written for the `server` fixture answers with a marked body when it uses `canned_chat_reply`."""

from __future__ import annotations

from poolhouse.client import Client
from poolhouse.testing.canned import (
    CANNED_MARKER,
    CANNED_MODEL_ID,
    canned_chat_reply,
    marked,
    unmarked,
)


def test_a_fixture_handler_answers_a_marked_chat_completion(server):
    asked = []

    def handler(method, path, body):
        asked.append(path)
        return canned_chat_reply("framing only")

    reply = Client(server(handler).base_url).chat([{"role": "user", "content": "hi"}])
    assert reply.content == marked("framing only") and unmarked(reply.content) == "framing only"
    assert asked == ["/v1/chat/completions"]


def test_its_body_names_the_canned_model_and_carries_the_metadata():
    import json

    status, raw = canned_chat_reply("hi", status=503, finish_reason="length")
    body = json.loads(raw)
    assert status == 503 and body["model"] == CANNED_MODEL_ID
    assert body["x_canned"] == body["usage"]["x_canned"] == CANNED_MARKER
    assert body["choices"][0]["finish_reason"] == "length"
    assert body["choices"][0]["message"]["content"].startswith(CANNED_MARKER)
