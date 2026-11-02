"""The canned plumbing server says it is not a model on every reply, and the real code reads it as it would a server.

Nothing here is a model: the marker is the subject. A reply that left it off, on any route, plain or streamed or an
error, is the failure these tests exist to catch.
"""

from __future__ import annotations

import ast
import dataclasses
import http.client
import inspect
import json
import subprocess
import sys
import time
from urllib.parse import urlsplit

import pytest

from poolhouse.client import Client, is_healthy, reported_models, serving_params
from poolhouse.serve import ServerSpec
from poolhouse.serve.matching import model_matches, serving_mismatch
from poolhouse.testing.canned import (
    CANNED_BODY_HEADER,
    CANNED_HEADER,
    CANNED_HEADER_VALUE,
    CANNED_MARKER,
    CANNED_MODEL_ID,
    Canned,
    CannedServer,
    Turn,
    canned_binary,
    canned_server,
    marked,
    unmarked,
)

WEIGHTS = "/models/quince-2b-Q4_K_M.gguf"


def call(server, method, path, body=None, headers=None):
    """``(status, headers, raw body)`` of one request, headers kept so the marker can be read off them."""
    link = http.client.HTTPConnection(urlsplit(server.base_url).netloc, timeout=10)
    try:
        link.request(method, path, body=None if body is None else json.dumps(body),
                     headers={"Content-Type": "application/json", **(headers or {})})
        reply = link.getresponse()
        return reply.status, dict(reply.getheaders()), reply.read()
    finally:
        link.close()


def said(server, method, path, body=None):
    """The decoded JSON of an answer that must carry the header, with the status and headers."""
    status, headers, raw = call(server, method, path, body)
    assert headers.get(CANNED_HEADER) == CANNED_HEADER_VALUE, f"{method} {path}: no marker header"
    return status, headers, json.loads(raw)


CHAT = {"messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture
def server():
    with canned_server(Canned(served_path=WEIGHTS, answer="hello", pieces=("one ", "two"))) as made:
        yield made


# ---------------------------------------------------------------- the marker on every reply type

@pytest.mark.parametrize("path", ["/health", "/props", "/v1/models", "/models", "/metrics", "/slots",
                                  "/nowhere"])
def test_every_get_carries_the_header(server, path):
    status, headers, raw = call(server, "GET", path)
    assert headers.get(CANNED_HEADER) == CANNED_HEADER_VALUE
    assert status == (404 if path == "/nowhere" else 200)
    if path == "/metrics":
        assert CANNED_MARKER in raw.decode()
    elif path != "/slots":
        assert json.loads(raw)["x_canned"] == CANNED_MARKER, "the metadata says so too"


def test_the_model_it_reports_is_the_canned_id_and_the_weights_stay_in_props(server):
    _, _, props = said(server, "GET", "/props")
    _, _, listed = said(server, "GET", "/v1/models")
    assert [row["id"] for row in listed["data"]] == [CANNED_MODEL_ID]
    assert props["model_path"] == WEIGHTS and props["model_alias"] == CANNED_MODEL_ID
    assert props["x_canned"] == CANNED_MARKER
    assert reported_models(server.base_url) == ["CANNED-NOT-A-MODEL"]


def test_a_plain_chat_reply_is_marked_in_header_text_model_and_usage(server):
    _, headers, body = said(server, "POST", "/v1/chat/completions", CHAT)
    content = body["choices"][0]["message"]["content"]
    assert content == f"{CANNED_MARKER} hello" == marked("hello")
    assert body["model"] == CANNED_MODEL_ID
    assert body["usage"]["x_canned"] == CANNED_MARKER and body["x_canned"] == CANNED_MARKER
    assert CANNED_BODY_HEADER not in headers


def test_a_completion_begins_with_the_marker(server):
    _, _, body = said(server, "POST", "/completion", {"prompt": "hi"})
    assert body["content"].startswith(CANNED_MARKER) and body["model"] == CANNED_MODEL_ID


def test_a_streamed_reply_starts_with_the_marker_and_ends_marked(server):
    status, headers, raw = call(server, "POST", "/v1/chat/completions", dict(CHAT, stream=True))
    assert status == 200 and headers.get(CANNED_HEADER) == CANNED_HEADER_VALUE
    frames = [json.loads(line[6:]) for line in raw.decode().splitlines()
              if line.startswith("data: ") and line != "data: [DONE]"]
    deltas = [f["choices"][0]["delta"].get("content") for f in frames if f["choices"][0]["delta"]]
    assert deltas[0] == f"{CANNED_MARKER} ", "the first delta is the marker, before any piece"
    assert deltas[1:] == ["one ", "two"]
    assert {f["model"] for f in frames} == {CANNED_MODEL_ID}
    assert frames[-1]["x_canned"] == CANNED_MARKER and frames[-1]["usage"]["x_canned"] == CANNED_MARKER
    assert raw.endswith(b"data: [DONE]\n\n")


def test_an_embedding_is_valid_in_shape_and_visibly_canned(server):
    _, _, body = said(server, "POST", "/v1/embeddings", {"input": ["a", "b", "c"]})
    assert [row["index"] for row in body["data"]] == [0, 1, 2]
    assert all(len(row["embedding"]) == 3 for row in body["data"])
    assert body["model"] == CANNED_MODEL_ID and body["x_canned"] == CANNED_MARKER


def test_a_rerank_is_valid_in_shape_ordered_by_position_and_visibly_canned(server):
    _, _, body = said(server, "POST", "/v1/rerank", {"query": "q", "documents": ["a", "b", "c"], "top_n": 2})
    assert [row["index"] for row in body["results"]] == [0, 1]
    assert all("relevance_score" in row and row["x_canned"] == CANNED_MARKER for row in body["results"])
    assert body["model"] == CANNED_MODEL_ID and body["x_canned"] == CANNED_MARKER


def test_logprobs_are_valid_in_shape_and_hold_only_the_marker(server):
    _, _, body = said(server, "POST", "/v1/chat/completions", dict(CHAT, logprobs=True, top_logprobs=5))
    held = body["choices"][0]["logprobs"]
    entry = held["content"][0]
    assert entry["token"] == CANNED_MARKER and entry["top_logprobs"][0]["token"] == CANNED_MARKER
    assert isinstance(entry["logprob"], float) and held["x_canned"] == CANNED_MARKER


def test_the_plumbing_routes_carry_the_header_too(server):
    assert said(server, "POST", "/tokenize", {"content": "one two"})[2]["tokens"] == [0, 1]
    assert said(server, "POST", "/detokenize", {"tokens": [1, 2]})[2]["content"] == "1 2"
    assert said(server, "POST", "/slots/0?action=save", {"filename": "s.bin"})[2]["filename"] == "s.bin"
    assert said(server, "POST", "/slots/0?action=restore", {"filename": "s.bin"})[0] == 200
    assert said(server, "POST", "/slots/0?action=burn", {})[0] == 400
    assert server.saved == [(0, "s.bin")] and server.restored == [(0, "s.bin")]


def test_a_tool_call_reply_carries_the_marker_in_its_content():
    turns = [Turn(calls=(("add", '{"a": 1}'),), completion_tokens=7), Turn(text=("the sum",))]
    with canned_server(turns=turns) as server:
        _, _, first = said(server, "POST", "/v1/chat/completions", CHAT)
        message = first["choices"][0]["message"]
        assert message["content"].startswith(CANNED_MARKER) and message["tool_calls"][0]["function"]["name"] == "add"
        assert first["choices"][0]["finish_reason"] == "tool_calls" and first["usage"]["completion_tokens"] == 7
        _, headers, raw = call(server, "POST", "/v1/chat/completions", dict(CHAT, stream=True))
        assert headers.get(CANNED_HEADER) == CANNED_HEADER_VALUE
        assert f'"content": "{CANNED_MARKER} "' in raw.decode(), "the leading delta of a streamed turn"
        assert said(server, "POST", "/v1/chat/completions", CHAT)[2]["choices"][0]["message"]["content"] \
            == marked("done")
        assert len(server.bodies) == 3


def test_every_error_carries_the_header_and_the_metadata():
    with canned_server() as server:
        server.refuse["/props"] = 503
        server.api_key = "secret"
        for path, key, want in (("/props", "secret", 503), ("/v1/models", "wrong", 401)):
            status, headers, raw = call(server, "GET", path, headers={"Authorization": f"Bearer {key}"})
            assert (status, headers.get(CANNED_HEADER)) == (want, CANNED_HEADER_VALUE)
            assert json.loads(raw)["x_canned"] == CANNED_MARKER
        server.api_key = ""
        server.overflow = 1
        status, headers, raw = call(server, "POST", "/v1/chat/completions", CHAT)
        assert status == 400 and headers.get(CANNED_HEADER) == CANNED_HEADER_VALUE
        assert "exceed_context_size_error" in raw.decode()
        assert said(server, "POST", "/v1/chat/completions", CHAT)[0] == 200, "overflow refused one request"


# ---------------------------------------------------------------- the marker cannot be turned off

def test_nothing_in_the_constructors_turns_the_marker_off():
    held = {field.name for field in dataclasses.fields(Canned)}
    assert held == {"served_path", "context", "slots", "metrics", "draft", "spec_type", "draft_max",
                    "counted", "answer", "pieces", "gap", "build_info"}
    assert set(inspect.signature(CannedServer.__init__).parameters) == {"self", "canned", "turns", "port"}


def test_the_text_helper_refuses_a_reply_that_was_never_marked():
    assert unmarked(marked("hello")) == "hello" and unmarked(CANNED_MARKER) == ""
    with pytest.raises(ValueError, match="not a canned reply"):
        unmarked("hello")


def test_unmarked_drops_the_text_prefix_and_says_so_in_header_and_metadata(server):
    with server.unmarked():
        _, headers, body = said(server, "POST", "/v1/chat/completions", CHAT)
        assert headers.get(CANNED_BODY_HEADER) == "unmarked"
        assert body["choices"][0]["message"]["content"] == "hello"
        assert body["model"] == CANNED_MODEL_ID and body["x_canned"] == CANNED_MARKER
        assert body["x_canned_body"] == "unmarked"
        _, _, raw = call(server, "POST", "/v1/chat/completions", dict(CHAT, stream=True))
        assert f'"content": "{CANNED_MARKER}' not in raw.decode(), "no leading marker delta"
        assert '"x_canned"' in raw.decode(), "the final frame still says so"
    assert server.unmarked_replies == 2
    again = said(server, "POST", "/v1/chat/completions", CHAT)[2]
    assert again["choices"][0]["message"]["content"] == marked("hello"), "marked again after the block"


def test_extraction_parses_json_only_inside_the_unmarked_block():
    from poolhouse.http import ServerError

    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    with canned_server(Canned(answer='{"n": 1}')) as server:
        with pytest.raises(ServerError, match="not JSON"):
            Client(server.base_url).extract("one", schema)
        with server.unmarked():
            assert Client(server.base_url).extract("one", schema) == {"n": 1}


# ---------------------------------------------------------------- the real code reads it as a server

def test_the_clients_read_it_the_way_they_read_a_llama_server(server):
    assert is_healthy(server.base_url)
    params = serving_params(server.base_url)
    assert params.model == WEIGHTS and params.quant == "Q4_K_M" and params.model_alias == CANNED_MODEL_ID
    client = Client(server.base_url)
    reply = client.chat([{"role": "user", "content": "hi"}])
    assert reply.content.startswith(CANNED_MARKER) and unmarked(reply.content) == "hello"
    assert client.family.name == "generic", "the canned id names no model family"
    assert client.served_by()["model"] == "quince-2b-Q4_K_M.gguf"
    heard = []
    streamed = client.chat([{"role": "user", "content": "hi"}], on_delta=lambda kind, text: heard.append(text))
    assert streamed.content == marked("one two") and heard[0] == f"{CANNED_MARKER} "


def test_a_server_holding_the_file_asked_for_is_not_a_mismatch_for_its_canned_id(server):
    spec = ServerSpec(model="/elsewhere/quince-2b-Q4_K_M.gguf", context=4096)
    assert serving_mismatch(spec, reported_models(server.base_url), serving_params(server.base_url)) == []
    other = ServerSpec(model="/elsewhere/another-7b-Q4_K_M.gguf", context=4096)
    assert serving_mismatch(other, reported_models(server.base_url), serving_params(server.base_url)) == [
        "model: asked for 'another-7b-Q4_K_M.gguf', serving 'CANNED-NOT-A-MODEL'"]


def test_a_reported_label_does_not_outweigh_the_file_props_says_is_loaded():
    assert model_matches(CANNED_MODEL_ID, "quince-2b.gguf", loaded_file="/m/quince-2b.gguf")
    assert not model_matches(CANNED_MODEL_ID, "other.gguf", loaded_file="/m/quince-2b.gguf")
    assert not model_matches(CANNED_MODEL_ID, "quince-2b.gguf"), "no /props answer: the label alone names nothing"


# ---------------------------------------------------------------- as a program

def test_the_binary_serves_marked_and_says_so_on_stderr_and_in_canned_json(tmp_path):
    from poolhouse.serve import free_port

    binary = canned_binary(tmp_path)
    assert "llama-server" not in binary.name
    if sys.platform == "win32":
        pytest.skip("the POSIX launcher is what is read here")
    tree = ast.parse(binary.read_text())
    execs = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "execv"]
    assert len(execs) == 1 and "llama-server" not in ast.unparse(execs[0].args[1])
    assert subprocess.run([str(binary), "--help"], capture_output=True, text=True, timeout=60,
                          check=True).stdout.count("--port") == 1
    port = free_port()
    child = subprocess.Popen([str(binary), "--port", str(port), "-m", WEIGHTS, "-c", "2048"],
                             stderr=subprocess.PIPE, text=True)
    try:
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 60
        while not is_healthy(url, timeout=1.0) and time.monotonic() < deadline:
            assert child.poll() is None, child.stderr.read()
            time.sleep(0.1)
        assert reported_models(url) == [CANNED_MODEL_ID]
        assert serving_params(url).n_ctx == 2048 and serving_params(url).model == WEIGHTS
        assert Client(url).chat([{"role": "user", "content": "hi"}]).content.startswith(CANNED_MARKER)
        assert json.loads((tmp_path / "canned.json").read_text())["model_id"] == CANNED_MODEL_ID
        assert json.loads((tmp_path / "argv.json").read_text())[-1] == "2048"
    finally:
        child.terminate()
        said_on_stderr = child.communicate(timeout=30)[1]
    assert CANNED_MARKER in said_on_stderr
