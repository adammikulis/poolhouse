"""The replies a test's own handler sends are marked too, the way the canned server marks its own.

Nothing here is a model: the marker is the subject. The `server` fixture sets no headers, so the id, the text
and the metadata carry it, and a bare body says that it is bare.
"""

from __future__ import annotations

import json

from poolhouse.client import Client
from poolhouse.testing.canned import (
    CANNED_MARKER,
    CANNED_MODEL_ID,
    canned_chat_reply,
    canned_chunk,
    canned_completion_reply,
    canned_embedding_reply,
    canned_stream_reply,
    marked,
)


def body(made):
    return json.loads(made[1])


def test_a_chat_reply_names_the_canned_id_and_begins_with_the_marker():
    status, raw = canned_chat_reply("hello", message={"reasoning_content": "why"}, finish_reason="length",
                                    top={"timings": {"predicted_n": 4}})
    assert status == 200
    held = json.loads(raw)
    assert held["model"] == CANNED_MODEL_ID and held["x_canned"] == CANNED_MARKER
    assert held["usage"]["x_canned"] == CANNED_MARKER
    assert held["timings"] == {"predicted_n": 4}
    choice = held["choices"][0]
    assert choice["message"]["content"] == marked("hello") and choice["message"]["reasoning_content"] == "why"
    assert choice["finish_reason"] == "length"
    assert "x_canned_body" not in held


def test_a_bare_chat_reply_carries_the_text_alone_and_says_it_is_bare():
    held = body(canned_chat_reply('{"a": 1}', bare=True))
    assert held["choices"][0]["message"]["content"] == '{"a": 1}'
    assert held["x_canned_body"] == "unmarked" and held["model"] == CANNED_MODEL_ID


def test_a_completion_reply_is_marked_unless_bare_and_keeps_its_fields():
    held = body(canned_completion_reply("cut off", stopped_limit=True))
    assert held["content"] == marked("cut off") and held["stopped_limit"] is True
    assert held["model"] == CANNED_MODEL_ID and held["x_canned"] == CANNED_MARKER
    bare = body(canned_completion_reply("{}", bare=True))
    assert bare["content"] == "{}" and bare["x_canned_body"] == "unmarked" and bare["x_canned"] == CANNED_MARKER


def test_an_embedding_reply_is_visibly_canned():
    held = body(canned_embedding_reply([0.1, 0.2], [0.3, 0.4]))
    assert [row["embedding"] for row in held["data"]] == [[0.1, 0.2], [0.3, 0.4]]
    assert held["model"] == CANNED_MODEL_ID and held["x_canned"] == CANNED_MARKER
    assert body(canned_embedding_reply())["data"] == []


def test_a_chunk_names_the_canned_id_and_keeps_what_rides_beside_it():
    chunk = canned_chunk({"content": marked("hi")}, finish="stop", index=1, usage={"total_tokens": 2})
    assert chunk["model"] == CANNED_MODEL_ID and chunk["x_canned"] == CANNED_MARKER
    (choice,) = chunk["choices"]
    assert (choice["index"], choice["delta"], choice["finish_reason"]) == (1, {"content": marked("hi")}, "stop")
    assert chunk["usage"] == {"total_tokens": 2}
    assert "finish_reason" not in canned_chunk()["choices"][0]


def test_a_bare_stream_has_no_marker_delta_and_every_chunk_still_names_the_canned_id():
    _, raw = canned_stream_reply([{"role": "assistant"}, {"tool_calls": []}], bare=True)
    frames = [json.loads(line[6:]) for line in raw.decode().splitlines()
              if line.startswith("data: ") and line != "data: [DONE]"]
    assert [frame["choices"][0]["delta"] for frame in frames] == [{"role": "assistant"}, {"tool_calls": []}]
    assert {frame["model"] for frame in frames} == {CANNED_MODEL_ID}
    assert {frame["x_canned"] for frame in frames} == {CANNED_MARKER}


def test_a_stream_starts_with_the_marker_and_the_client_reads_it_back(server):
    status, raw = canned_stream_reply([{"content": "4"}, {"content": "2"}], finish="stop",
                                      timings={"predicted_n": 2})
    assert status == 200 and raw.endswith(b"data: [DONE]\n\n")
    frames = [json.loads(line[6:]) for line in raw.decode().splitlines()
              if line.startswith("data: ") and line != "data: [DONE]"]
    assert frames[0]["choices"][0]["delta"] == {"content": f"{CANNED_MARKER} "}
    assert frames[-1]["choices"][0]["finish_reason"] == "stop" and frames[-1]["timings"] == {"predicted_n": 2}
    assert {frame["model"] for frame in frames} == {CANNED_MODEL_ID}
    instance = server(lambda m, p, b: (status, raw))
    seen: list[tuple[str, str]] = []
    reply = Client(instance.base_url).chat([], on_delta=lambda kind, text: seen.append((kind, text)))
    assert reply.content == marked("42") and seen[0] == ("content", f"{CANNED_MARKER} ")
