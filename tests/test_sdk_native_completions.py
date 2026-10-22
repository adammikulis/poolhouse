"""Native SDK payload, frame, endpoint and joined-cancellation contracts."""
from __future__ import annotations

import gzip
import io
import json
import threading
from contextlib import contextmanager, nullcontext
from http.server import BaseHTTPRequestHandler
from types import SimpleNamespace

import pytest
from test_chat_stream import hanging_chat

from poolhouse.client import completion, sdk
from poolhouse.fleet import chat_stream, sdk_chat
from poolhouse.fleet.chat import ChatError, Target
from poolhouse.fleet.chat_stream import Transfer, frame
from poolhouse.http import Server
from poolhouse.testing.canned import (
    CANNED_HEADER,
    CANNED_HEADER_VALUE,
    CANNED_MODEL_ID,
    Canned,
    CannedServer,
    canned_chunk,
    marked,
)

PAYLOAD = {"model": "model", "messages": [{"role": "user", "content": "Inspect the source"}], "stream": True}
DONE = b"data: [DONE]\n\n"


def terminal(reason="stop"):
    return canned_chunk({}, finish=reason)


def two_choices():
    """A chunk that finishes choice 0 and opens a second one, which a native stream never does."""
    chunk = terminal()
    chunk["choices"] += canned_chunk({}, index=1)["choices"]
    return chunk


@contextmanager
def endpoint(answer):
    requests = []
    finished = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.command, dict(self.headers), b""))
            self.send_error(500)
        def do_POST(self):
            requests.append((self.command, dict(self.headers), self.rfile.read(int(self.headers["Content-Length"]))))
            try:
                answer(self)
            finally:
                finished.set()
        def log_message(self, *_arguments):
            pass
    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield Target("model", f"http://127.0.0.1:{server.server_port}/v1/chat/completions", token="fixture-secret"), requests, finished
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_actual_sdk_preserves_exact_native_payload_and_separate_provider_chunks(monkeypatch):
    chunks = [
        canned_chunk({"role": "assistant", "reasoning_content": "Inspect first",
                      "provider_reasoning": {"signature": "opaque"}}, id="native-a",
                     object="chat.completion.chunk", created=1, provider_top={"position": 1}),
        canned_chunk({"tool_calls": [{"index": 0, "id": "call-a", "type": "function",
                                      "function": {"name": "read", "arguments": '{"path":"'}}]}),
        canned_chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'source.py"}'}}]}),
        terminal("tool_calls"),
        {"choices": [], "usage": {"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20}, "provider_final": True},
    ]
    wire = b"".join(frame(chunk) for chunk in chunks) + DONE
    fake = CannedServer(Canned())
    fake.frames = lambda _body: [wire[:4], wire[4:21], wire[21:]]
    opened = []
    original = sdk.open_stream
    def observe(*args, **kwargs):
        response = original(*args, **kwargs)
        opened.append(response)
        return response
    monkeypatch.setattr(sdk, "open_stream", observe)
    tools = [{"type": "function", "function": {"name": "read", "description": "Read source",
               "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                              "required": ["path"], "additionalProperties": False}, "strict": True}}]
    payload = {**PAYLOAD, "messages": [{"role": "user", "content": "Inspect original"}], "tools": tools,
               "tool_choice": {"type": "function", "function": {"name": "read"}}, "parallel_tool_calls": False,
               "max_tokens": 731, "temperature": 0.4, "top_p": 0.9, "stop": ["END"], "seed": 17,
               "presence_penalty": -0.2, "frequency_penalty": 0.1, "reasoning_effort": "low",
               "stream_options": {"include_usage": True}, "store": False, "n": 1,
               "prompt_cache_key": "native-session", "prompt_cache_retention": "in_memory",
               "chat_template_kwargs": {"enable_thinking": True, "preserve_thinking": True, "reasoning_effort": "low"}}
    expected = json.loads(json.dumps(payload))
    try:
        pieces = sdk_chat.completion_stream(Target("model", fake.base_url + "/v1/chat/completions"), sdk_chat.prepare_completion(Target("model", fake.base_url + "/v1/chat/completions"), payload))
        payload["messages"][0]["content"] = "Changed after admission"
        tools[0]["function"]["name"] = "substituted"
        output = []
        for block in pieces:
            if block == DONE:
                assert opened and all(response.closed for response in opened)
                assert not any(thread.name.startswith("asyncio_") for thread in threading.enumerate())
            output.append(block)
        assert len(output) == len(chunks) + 1 and output[-1] == DONE
        assert all(block.count(b"\n\n") == 1 for block in output)
        assert [json.loads(block.removeprefix(b"data: ")) for block in output[:-1]] == chunks
        assert {chunk["model"] for chunk in chunks if chunk["choices"]} == {CANNED_MODEL_ID}, "every frame names the canned id"
        assert fake.sent_to("/v1/chat/completions") == [expected]
    finally:
        fake.close()


@pytest.mark.parametrize("token_key", ["max_tokens", "max_completion_tokens"])
def test_sdk_preserves_the_selected_native_token_field(token_key):
    fake = CannedServer(Canned())
    fake.frames = lambda _body: [frame(terminal()), DONE]
    try:
        assert list(sdk_chat.completion_stream(Target("model", fake.base_url + "/v1/chat/completions"),
                    sdk_chat.prepare_completion(Target("model", fake.base_url + "/v1/chat/completions"), {**PAYLOAD, token_key: 123})))[-1] == DONE
        assert fake.sent_to("/v1/chat/completions") == [{**PAYLOAD, token_key: 123}]
    finally:
        fake.close()


@pytest.mark.parametrize("change", [
    {"model": "other"}, {"url": "https://unselected.invalid"}, {"extra_headers": {"Authorization": "forged"}},
    {"extra_body": {"model": "other"}}, {"timeout": 1}, {"client": "other"}, {"callback": "run"},
    {"n": 2}, {"n": True}, {"stream": False}, {"store": True}, {"max_tokens": True}, {"max_tokens": 0},
    {"max_tokens": 1, "max_completion_tokens": 1}, {"temperature": True}, {"temperature": float("nan")},
    {"top_p": 2}, {"parallel_tool_calls": 1}, {"messages": []}, {"messages": [{"role": "owner"}]},
    {"tools": [lambda: None]}, {"stream_options": {"include_usage": 1}},
    {"stream_options": {"include_obfuscation": True}}, {"reasoning_effort": "unbounded"},
    {"chat_template_kwargs": {"model": "other"}}, {"chat_template_kwargs": {"enable_thinking": 1}},
    {"chat_template_kwargs": {"reasoning_effort": "unbounded"}},
])
def test_invalid_native_payload_cannot_construct_client_or_open_network(monkeypatch, change):
    called = []
    monkeypatch.setattr(sdk_chat, "local_client", lambda *_args, **_kwargs: called.append(True))
    with pytest.raises(ChatError):
        sdk_chat.prepare_completion(Target("model", "http://127.0.0.1:1/v1/chat/completions"), {**PAYLOAD, **change})
    assert called == []


@pytest.mark.parametrize("limit", [0, -1, True, False, 1.5, "128"])
def test_invalid_transport_response_limit_cannot_construct_sdk_client(monkeypatch, limit):
    called = []
    monkeypatch.setattr(sdk, "AsyncOpenAI", lambda **_kwargs: called.append(True))
    with pytest.raises(ValueError):
        sdk.local_client(Target("model", "http://127.0.0.1:1/v1/chat/completions"), "model", response_limit=limit)
    assert called == []


@pytest.mark.parametrize("chunks", [
    [canned_chunk({"content": marked("partial")})],
    [canned_chunk({}, finish="stop", index=1)],
    [canned_chunk({}, finish="stop", index=True)],
    [canned_chunk({}, finish="unknown")],
    [two_choices()],
    [terminal(), canned_chunk({"content": marked("after terminal")})],
    [terminal(), terminal()],
    [terminal(), {"choices": []}],
    [terminal(), {"choices": [], "usage": None}],
    [terminal(), {"choices": [], "usage": {"prompt_tokens": "invalid", "completion_tokens": 1, "total_tokens": 2}}],
    [terminal(), {"choices": [], "usage": {"prompt_tokens": -1, "completion_tokens": 1, "total_tokens": 0}}],
])
def test_native_missing_or_conflicting_terminal_never_emits_done(chunks):
    fake = CannedServer(Canned())
    fake.frames = lambda _body: [*(frame(chunk) for chunk in chunks), DONE]
    seen = []
    try:
        with pytest.raises(ChatError):
            for block in sdk_chat.completion_stream(Target("model", fake.base_url + "/v1/chat/completions"), sdk_chat.prepare_completion(Target("model", fake.base_url + "/v1/chat/completions"), PAYLOAD)):
                seen.append(block)
        assert DONE not in seen
    finally:
        fake.close()


def test_transfer_holds_coalesced_native_done_for_its_parent_commit(monkeypatch):
    monkeypatch.setattr(chat_stream, "turn", lambda *_args, **_kwargs: nullcontext())
    fake = CannedServer(Canned())
    fake.frames = lambda _body: [frame(canned_chunk({"content": marked("answer")})) + frame(terminal()) + DONE]
    output = io.BytesIO()
    try:
        transfer = Transfer(Target("model", fake.base_url + "/v1/chat/completions"),
                            sdk_chat.prepare_completion(Target("model", fake.base_url + "/v1/chat/completions"), PAYLOAD), "native",
                            source=sdk_chat.completion_stream)
        received, status = transfer.relay(SimpleNamespace(wfile=output), defer_done=True)
        assert status == "complete" and received.endswith(DONE)
        assert DONE not in output.getvalue()
        assert transfer.done.is_set() and not transfer.worker.is_alive()
    finally:
        fake.close()


@pytest.mark.parametrize("headers", [False, True])
def test_native_cancel_joins_socket_and_executor_before_device_release(monkeypatch, headers):
    closed_source, released = threading.Event(), threading.Event()
    @contextmanager
    def turn(*_args, **_kwargs):
        try:
            yield
        finally:
            assert closed_source.is_set()
            assert not any(thread.name.startswith("asyncio_") for thread in threading.enumerate())
            released.set()
    def source(*args, **kwargs):
        try:
            yield from sdk_chat.completion_stream(*args, **kwargs)
        finally:
            closed_source.set()
    monkeypatch.setattr(chat_stream, "turn", turn)
    with hanging_chat(headers=headers) as (url, requested, closed, requests):
        transfer = Transfer(Target("model", url), sdk_chat.prepare_completion(Target("model", url), PAYLOAD), "native", source=source)
        transfer.worker.start()
        try:
            assert requested.wait(2)
            if headers:
                while b"first" not in transfer.events.get(timeout=2):
                    pass
            transfer.cancel()
            transfer.cancel()
            transfer.worker.join(2)
            assert transfer.done.is_set() and not transfer.worker.is_alive()
            assert released.is_set() and closed.wait(1)
            assert len(requests) == 1
        finally:
            transfer.cancel()
            transfer.worker.join(3)


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_actual_sdk_redirect_never_reaches_other_listener(status):
    def redirect(handler):
        handler.send_response(status)
        handler.send_header("Location", other.url)
        handler.send_header("Content-Length", "0")
        handler.end_headers()
    with (endpoint(lambda handler: handler.send_error(500)) as (other, foreign_requests, _finished),
          endpoint(redirect) as (selected, requests, _done)):
        with pytest.raises(ChatError):
            list(sdk_chat.completion_stream(selected, sdk_chat.prepare_completion(selected, PAYLOAD)))
        assert len(requests) == 1
        assert requests[0][1]["Authorization"] == "Bearer fixture-secret"
        assert foreign_requests == []


@pytest.mark.parametrize("encodings", [[], ["identity"], [" Identity "]])
def test_actual_sdk_identity_encoding_succeeds(encodings):
    def answer(handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header(CANNED_HEADER, CANNED_HEADER_VALUE)
        for value in encodings:
            handler.send_header("Content-Encoding", value)
        handler.end_headers()
        handler.wfile.write(frame(terminal()) + DONE)
    with endpoint(answer) as (target, requests, _finished):
        assert list(sdk_chat.completion_stream(target, sdk_chat.prepare_completion(target, PAYLOAD)))[-1] == DONE
        assert requests[0][1]["Accept-Encoding"] == "identity"


@pytest.mark.parametrize("encodings", [["gzip"], ["deflate"], ["br"], ["identity,gzip"], ["gzip", "identity"], [""]])
def test_compressed_or_ambiguous_encoding_is_closed_before_sdk_decode(encodings, monkeypatch):
    touched = []
    original = sdk._Body.__aiter__
    async def observing(self):
        touched.append(True)
        async for block in original(self):
            yield block
    monkeypatch.setattr(sdk._Body, "__aiter__", observing)
    def answer(handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        for value in encodings:
            handler.send_header("Content-Encoding", value)
        handler.end_headers()
        handler.wfile.write(gzip.compress(b"data: " + b"x" * 100_000))
        handler.wfile.flush()
    with endpoint(answer) as (target, _requests, finished):
        with pytest.raises(ChatError):
            list(sdk_chat.completion_stream(target, sdk_chat.prepare_completion(target, PAYLOAD)))
        assert touched == [] and finished.wait(1)


def test_oversize_unterminated_event_stops_before_sdk_decoder_receives_excess(monkeypatch):
    monkeypatch.setattr(sdk_chat, "MOST_BODY", 512)
    passed = []
    original = sdk._Body.__aiter__
    async def observing(self):
        async for block in original(self):
            passed.append(len(block))
            yield block
    monkeypatch.setattr(sdk._Body, "__aiter__", observing)
    closed = threading.Event()
    def answer(handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.end_headers()
        handler.wfile.write(b"data: " + b"x" * 4096)
        handler.wfile.flush()
        handler.connection.settimeout(2)
        if handler.connection.recv(1) == b"":
            closed.set()
    with endpoint(answer) as (target, _requests, finished):
        with pytest.raises(ChatError):
            list(sdk_chat.completion_stream(target, sdk_chat.prepare_completion(target, PAYLOAD)))
        assert sum(passed) <= 512 and closed.wait(1) and finished.wait(1)
        assert not any(thread.name.startswith("asyncio_") for thread in threading.enumerate())


def test_aggregate_reserialized_provider_fields_have_an_output_bound(monkeypatch):
    monkeypatch.setattr(sdk_chat, "MOST_BODY", 1536)
    chunks = [canned_chunk({"content": marked("é" * 80)}) for _ in range(3)] + [terminal()]
    fake = CannedServer(Canned())
    fake.frames = lambda _body: [*(b"data:" + json.dumps(chunk, ensure_ascii=False, separators=(",", ":")).encode() + b"\n\n" for chunk in chunks), DONE]
    seen = []
    assert sum(map(len, fake.frames({}))) < 1536, "the input alone is inside the bound; the re-serialized output is not"
    try:
        with pytest.raises(ChatError, match="output exceeds"):
            for block in sdk_chat.completion_stream(Target("model", fake.base_url + "/v1/chat/completions"), sdk_chat.prepare_completion(Target("model", fake.base_url + "/v1/chat/completions"), PAYLOAD)):
                seen.append(block)
        assert sum(map(len, seen)) <= 1536 and DONE not in seen
    finally:
        fake.close()


def test_cleanup_failure_never_exposes_native_done(monkeypatch):
    original = sdk._Body.aclose
    async def broken(self):
        await original(self)
        raise RuntimeError("fixture cleanup failed")
    monkeypatch.setattr(sdk._Body, "aclose", broken)
    fake = CannedServer(Canned())
    fake.frames = lambda _body: [frame(terminal()), DONE]
    seen = []
    try:
        with pytest.raises((ChatError, RuntimeError)):
            for block in sdk_chat.completion_stream(Target("model", fake.base_url + "/v1/chat/completions"), sdk_chat.prepare_completion(Target("model", fake.base_url + "/v1/chat/completions"), PAYLOAD)):
                seen.append(block)
        assert DONE not in seen
        assert not any(thread.name.startswith("asyncio_") for thread in threading.enumerate())
    finally:
        fake.close()


def test_oversized_native_tool_schema_cannot_construct_client(monkeypatch):
    monkeypatch.setattr(sdk_chat, "MOST_BODY", 128)
    called = []
    monkeypatch.setattr(sdk_chat, "local_client", lambda *_args, **_kwargs: called.append(True))
    payload = {**PAYLOAD, "tools": [{"type": "function", "function": {"name": "read",
               "parameters": {"type": "object", "description": "x" * 512}}}]}
    with pytest.raises(ChatError):
        sdk_chat.prepare_completion(Target("model", "http://127.0.0.1:1/v1/chat/completions"), payload)
    assert called == []


def test_prepared_factory_canonical_digest_and_independent_copies(monkeypatch):
    from dataclasses import FrozenInstanceError
    from hashlib import sha256
    target = Target("model", "http://127.0.0.1:1/v1/chat/completions", token="private-target-token")
    payload = {**PAYLOAD, "chat_template_kwargs": {"enable_thinking": True}}
    request = sdk_chat.prepare_completion(target, payload)
    reordered = sdk_chat.prepare_completion(target, dict(reversed(list(payload.items()))))
    assert request.arguments_json == reordered.arguments_json and request.digest == reordered.digest
    assert request.digest == sha256(request.arguments_json.encode()).hexdigest()
    assert json.loads(request.arguments_json)["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True}}
    assert "private-target-token" not in request.arguments_json and target.url not in request.arguments_json
    assert "_origin" not in request.arguments_json
    monkeypatch.setattr(sdk_chat, "local_client", lambda *_args, **_kwargs: pytest.fail("client constructed"))
    assert completion.checked_completion(request) is request
    copy = request.arguments
    copy["messages"][0]["content"] = "mutated copy"
    assert request.arguments["messages"][0]["content"] == "Inspect the source"
    with pytest.raises(FrozenInstanceError):
        request.model = "other"
    messages = [{"role": "user", "content": "one"}, {"role": "assistant", "content": "two"}]
    first = sdk_chat.prepare_completion(target, {**PAYLOAD, "messages": messages})
    second = sdk_chat.prepare_completion(target, {**PAYLOAD, "messages": list(reversed(messages))})
    assert first.digest != second.digest


def test_prepared_constructor_and_unprepared_stream_are_refused_before_client(monkeypatch):
    target = Target("model", "http://127.0.0.1:1/v1/chat/completions")
    monkeypatch.setattr(sdk_chat, "local_client", lambda *_args, **_kwargs: pytest.fail("client constructed"))
    with pytest.raises(TypeError, match="prepare_completion"):
        completion.PreparedCompletion()
    forged = object.__new__(completion.PreparedCompletion)
    for value in (PAYLOAD, forged, {"arguments_json": "{}", "digest": "forged"}):
        with pytest.raises(ValueError):
            completion.checked_completion(value)
        with pytest.raises(ChatError):
            sdk_chat.completion_stream(target, value)
    request = sdk_chat.prepare_completion(target, PAYLOAD)
    with pytest.raises(ChatError, match="selected model"):
        sdk_chat.completion_stream(Target("other", target.url), request)


@pytest.mark.parametrize("field,value", [
    ("_origin", object()), ("digest", "0" * 64), ("arguments_json", "{}"),
    ("model", "other"), ("arguments_json", None), ("digest", None),
])
def test_damaged_factory_record_is_refused_by_checker_property_and_stream(monkeypatch, field, value):
    target = Target("model", "http://127.0.0.1:1/v1/chat/completions")
    request = sdk_chat.prepare_completion(target, PAYLOAD)
    object.__setattr__(request, field, value)
    monkeypatch.setattr(sdk_chat, "local_client", lambda *_args, **_kwargs: pytest.fail("client constructed"))
    with pytest.raises(ValueError):
        completion.checked_completion(request)
    with pytest.raises(ValueError):
        _ = request.arguments
    with pytest.raises(ChatError):
        sdk_chat.completion_stream(target, request)


def test_prepared_translated_canonical_size_is_bounded(monkeypatch):
    payload = {**PAYLOAD, "chat_template_kwargs": {}}
    target = Target("model", "http://127.0.0.1:1/v1/chat/completions")
    request = sdk_chat.prepare_completion(target, payload)
    # Translation wraps the vendor controls in the SDK extra_body object.
    monkeypatch.setattr(sdk_chat, "MOST_BODY", len(request.arguments_json.encode()) - 1)
    monkeypatch.setattr(completion, "MOST_BODY", len(request.arguments_json.encode()) - 1)
    monkeypatch.setattr(sdk_chat, "local_client", lambda *_args, **_kwargs: pytest.fail("client constructed"))
    with pytest.raises(ChatError):
        sdk_chat.prepare_completion(target, payload)
    with pytest.raises(ValueError):
        completion.checked_completion(request)


@pytest.mark.parametrize("replacement", ["model", "spacing", "nonfinite"])
def test_integrity_checks_model_and_canonical_encoding_even_with_matching_hash(monkeypatch, replacement):
    from hashlib import sha256
    target = Target("model", "http://127.0.0.1:1/v1/chat/completions")
    request = sdk_chat.prepare_completion(target, PAYLOAD)
    arguments = request.arguments
    if replacement == "model":
        arguments["model"] = "other"
    if replacement == "nonfinite":
        arguments["temperature"] = float("nan")
    encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    if replacement == "spacing":
        encoded += " "
    object.__setattr__(request, "arguments_json", encoded)
    object.__setattr__(request, "digest", sha256(encoded.encode()).hexdigest())
    monkeypatch.setattr(sdk_chat, "local_client", lambda *_args, **_kwargs: pytest.fail("client constructed"))
    with pytest.raises(ValueError):
        completion.checked_completion(request)


@pytest.mark.parametrize("model", [1, True, None, ""])
def test_factory_refuses_invalid_trusted_target_model_before_client(monkeypatch, model):
    target = Target(model, "http://127.0.0.1:1/v1/chat/completions")
    monkeypatch.setattr(sdk_chat, "local_client", lambda *_args, **_kwargs: pytest.fail("client constructed"))
    with pytest.raises(ChatError):
        sdk_chat.prepare_completion(target, {**PAYLOAD, "model": model})
