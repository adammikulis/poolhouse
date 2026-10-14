"""Client response parsing, family inference and server readiness."""

from __future__ import annotations

import json
import time

import pytest
from conftest import json_reply

from poolhouse.client import (
    Client,
    Request,
    ServerError,
    Transport,
    family_by_name,
    family_for_model_id,
    is_healthy,
    quant_from_model_path,
    reported_models,
    serving_params,
    strip_thinking,
    wait_for_health,
)


class TestBuildBody:
    """Split out from ``chat`` precisely so it is testable with no server running."""

    def test_pins_the_kv_slot_when_one_is_given(self):
        body = Client("http://x",
                      request=Request(slot=3)).build_body([{"role": "user", "content": "hi"}])
        assert body["id_slot"] == 3
        assert body["cache_prompt"] is True, "pinning a slot without reusing its cache "\
                                             "is the cost with none of the benefit"

    def test_omits_slot_keys_when_unpinned(self):
        body = Client("http://x").build_body([])
        assert "id_slot" not in body and "cache_prompt" not in body

    def test_normalises_the_json_response_format_alias(self):
        """Callers habitually pass "json"; llama-server wants "json_object"."""
        body = Client("http://x").build_body([], response_format="json")
        assert body["response_format"] == {"type": "json_object"}

    def test_passes_through_an_explicit_response_format(self):
        schema = {"type": "json_schema", "json_schema": {"name": "s"}}
        assert Client("http://x").build_body([], response_format=schema)["response_format"] == schema

    def test_strips_llamacpp_only_keys_for_hosted_openai(self):
        """api.openai.com rejects top_k outright rather than ignoring it."""
        body = Client("https://api.openai.com/v1",
                      request=Request(slot=1, n_predict=512)).build_body([], top_k=40)
        assert "top_k" not in body
        assert "n_predict" not in body and body["max_tokens"] == 512
        assert "id_slot" not in body

    def test_keeps_them_for_a_local_server(self):
        body = Client("http://127.0.0.1:8080",
                      request=Request(n_predict=512)).build_body([], top_k=40)
        assert body["top_k"] == 40 and body["n_predict"] == 512

    def test_output_limits_default_to_none(self):
        assert Request().n_predict is None
        assert Transport().timeout is None
        assert Client("http://x").build_body([])["n_predict"] == -1
        hosted = Client("http://x", transport=Transport(api="openai"))
        assert "max_tokens" not in hosted.build_body([])
        assert hosted.build_body([], n_predict=257)["max_tokens"] == 257

    def test_tools_only_appear_when_supplied(self):
        tool = {"type": "function", "function": {"name": "ping", "parameters": {}}}
        assert "tools" not in Client("http://x").build_body([])
        assert Client("http://x").build_body([], tools=[tool])["tool_choice"] == "auto"

    def test_no_speculative_fields_unless_asked(self):
        body = Client("http://x").build_body([])
        assert not [key for key in body if key.startswith("speculative.")]

    def test_a_draft_depth_is_sent_under_the_servers_own_name(self):
        """The server registers a flat field; a nested object is dropped in silence."""
        body = Client("http://x", request=Request(spec_draft_max=4)).build_body([])
        assert body["speculative.n_max"] == 4
        assert "speculative" not in body

    def test_a_setting_the_server_reads_once_at_startup_is_not_a_request_field(self):
        """llama-server reads the draft's p_min once, at start."""
        with pytest.raises(TypeError, match="spec_p_min"):
            Request(spec_p_min=0.5)

    def test_speculative_fields_are_stripped_for_hosted_openai(self):
        body = Client("https://api.openai.com/v1", request=Request(spec_draft_max=4)).build_body([])
        assert not [key for key in body if key.startswith("speculative.")]


class TestNormalize:
    def test_lifts_a_legacy_function_call_into_tool_calls(self):
        """Some llama.cpp builds emit tool calls only in the legacy field. Callers must
        see one shape, not two."""
        reply = Client.normalize(
            {"choices": [{"message": {"function_call": {"name": "ping", "arguments": "{}"}}}]}
        )
        assert reply.tool_calls is not None
        assert reply.tool_calls[0]["function"]["name"] == "ping"
        assert reply.tool_calls[0]["type"] == "function"

    def test_prefers_real_tool_calls_over_the_legacy_field(self):
        reply = Client.normalize(
            {"choices": [{"message": {
                "tool_calls": [{"id": "a", "type": "function",
                                "function": {"name": "real", "arguments": "{}"}}],
                "function_call": {"name": "legacy", "arguments": "{}"}}}]}
        )
        assert reply.tool_calls[0]["function"]["name"] == "real"

    def test_truncation_is_visible(self):
        reply = Client.normalize({"choices": [{"message": {"content": "cut"},
                                               "finish_reason": "length"}]})
        assert reply.truncated

    def test_rejects_a_non_dict_payload(self):
        with pytest.raises(ServerError):
            Client.normalize(["not", "a", "response"])


    def test_a_reasoning_field_is_carried_as_thinking(self):
        reply = Client.normalize({"choices": [{"message": {
            "role": "assistant", "content": "",
            "reasoning_content": "count the people first"}, "finish_reason": "stop"}]})
        assert reply.content == "" and reply.thinking == "count the people first"

    def test_reasoning_and_think_tags_both_survive(self):
        reply = Client.normalize({"choices": [{"message": {
            "role": "assistant", "content": "<think>tags</think>42",
            "reasoning": "field"}, "finish_reason": "stop"}]})
        assert reply.content == "42"
        assert reply.thinking == "field\ntags"


class TestStripThinking:
    def test_separates_the_trace_from_the_answer(self):
        visible, thinking = strip_thinking("<think>weigh it up</think>The answer is 4.")
        assert visible == "The answer is 4."
        assert thinking == "weigh it up"

    def test_keeps_the_trace_rather_than_discarding_it(self):
        """It is the most useful thing in the response when a tool call comes back wrong."""
        _, thinking = strip_thinking("<think>a</think>x<think>b</think>y")
        assert thinking == "a\nb"

    def test_leaves_ordinary_text_untouched(self):
        assert strip_thinking("plain") == ("plain", None)
        assert strip_thinking(None) == (None, None)


GPT_OSS_ID = "/models/gpt-oss-120b-mxfp4-00001-of-00003.gguf"
QWEN_ID = "/models/Qwen3-4B-Instruct-Q4_K_M.gguf"
UNKNOWN_ID = "/models/mystery-1b-Q4_K_M.gguf"


def _completion(model, content, **message):
    """A whole ``/v1/chat/completions`` payload, the way a served model returns one."""
    return {"model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content, **message}}]}


def _sse(chunks):
    """The chunks as an SSE body, one ``data:`` frame each, terminated."""
    return ("".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n").encode()


TIMINGS = {"prompt_n": 12, "prompt_ms": 30.5, "predicted_n": 4, "predicted_ms": 80.0}
USAGE = {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}


def _stream_of(model, deltas, finish="stop"):
    """Deltas as chunks carrying the served model id, the last one finishing and carrying
    the timings and usage the way llama-server's last chunk does."""
    out = []
    for index, delta in enumerate(deltas):
        chunk = {"model": model,
                 "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
        if index == len(deltas) - 1:
            chunk["choices"][0]["finish_reason"] = finish
            chunk["timings"], chunk["usage"] = TIMINGS, USAGE
        out.append(chunk)
    return out


def _both_ways(server, model, payload, deltas, *, finish="stop"):
    """The same reply fetched whole and streamed. Returns ``(whole, streamed, seen)``."""
    whole_server = server(lambda m, p, b: json_reply({**payload, "timings": TIMINGS,
                                                       "usage": USAGE}))
    whole = Client(whole_server.base_url).chat([{"role": "user", "content": "x"}])

    body = _sse(_stream_of(model, deltas, finish=finish))
    stream_server = server(lambda m, p, b: (200, body))
    seen: list[tuple[str, str]] = []
    streamed = Client(stream_server.base_url).chat(
        [{"role": "user", "content": "x"}], on_delta=lambda k, t: seen.append((k, t)))
    return whole, streamed, seen


def _same(whole, streamed):
    assert whole.content == streamed.content
    assert whole.thinking == streamed.thinking
    assert whole.tool_calls == streamed.tool_calls
    assert whole.finish_reason == streamed.finish_reason
    assert whole.raw.get("timings") == streamed.raw.get("timings") == TIMINGS
    assert whole.raw.get("usage") == streamed.raw.get("usage") == USAGE


class TestFamilies:
    def test_a_model_id_names_its_family(self):
        assert family_for_model_id(GPT_OSS_ID).name == "gpt-oss"
        assert family_for_model_id(QWEN_ID).name == "qwen"

    def test_an_unrecognised_id_falls_back_to_generic(self):
        assert family_for_model_id(UNKNOWN_ID).name == "generic"
        assert family_for_model_id(None).name == "generic"

    def test_a_family_can_be_named(self):
        assert family_by_name("gpt-oss") is family_for_model_id(GPT_OSS_ID)
        assert family_by_name("QWEN").name == "qwen"
        with pytest.raises(ValueError, match="unknown model family"):
            family_by_name("nonesuch")


class TestGptOssReplies:
    def test_the_answer_and_the_reasoning_both_come_through(self, server):
        payload = _completion(GPT_OSS_ID, "4", reasoning_content="user asks 2+2, so 4")
        instance = server(lambda m, p, b: json_reply(payload))
        reply = Client(instance.base_url).chat([{"role": "user", "content": "2+2?"}])
        assert reply.content == "4"
        assert reply.thinking == "user asks 2+2, so 4"

    def test_an_unfinished_turn_keeps_its_reasoning(self, server):
        """Harmony emits the analysis channel before the final one, so a turn cut short
        has text in reasoning_content and nothing in content."""
        payload = _completion(GPT_OSS_ID, "", reasoning_content="counting the people")
        payload["choices"][0]["finish_reason"] = "length"
        instance = server(lambda m, p, b: json_reply(payload))
        reply = Client(instance.base_url).chat([])
        assert reply.content == ""
        assert reply.thinking == "counting the people"
        assert reply.truncated

    def test_a_literal_think_tag_in_the_answer_is_left_alone(self, server):
        """Harmony carries no in-band delimiter, so the tag is the model's own text."""
        payload = _completion(GPT_OSS_ID, "write <think> to open a block",
                              reasoning_content="explain the syntax")
        instance = server(lambda m, p, b: json_reply(payload))
        reply = Client(instance.base_url).chat([])
        assert reply.content == "write <think> to open a block"
        assert reply.thinking == "explain the syntax"

    def test_streaming_lands_where_the_whole_reply_does(self, server):
        whole, streamed, seen = _both_ways(
            server, GPT_OSS_ID,
            _completion(GPT_OSS_ID, "42", reasoning_content="add them up"),
            [{"role": "assistant"}, {"reasoning_content": "add "},
             {"reasoning_content": "them up"}, {"content": "4"}, {"content": "2"}],
        )
        assert whole.content == "42" and whole.thinking == "add them up"
        _same(whole, streamed)
        assert seen == [("thinking", "add "), ("thinking", "them up"),
                        ("content", "4"), ("content", "2")]


class TestQwenReplies:
    def test_an_in_band_block_splits_and_does_not_leak(self, server):
        payload = _completion(QWEN_ID, "<think>weigh it up</think>The answer is 4.")
        instance = server(lambda m, p, b: json_reply(payload))
        reply = Client(instance.base_url).chat([])
        assert reply.content == "The answer is 4."
        assert reply.thinking == "weigh it up"
        assert "<think>" not in reply.content and "weigh" not in reply.content

    def test_streaming_splits_a_block_broken_across_chunks(self, server):
        whole, streamed, seen = _both_ways(
            server, QWEN_ID,
            _completion(QWEN_ID, "<think>weigh it up</think>The answer is 4."),
            [{"role": "assistant"}, {"content": "<th"}, {"content": "ink>weigh "},
             {"content": "it up</thi"}, {"content": "nk>The answer "},
             {"content": "is 4."}],
        )
        assert whole.content == "The answer is 4." and whole.thinking == "weigh it up"
        _same(whole, streamed)
        assert seen == [("thinking", "weigh "), ("thinking", "it up"),
                        ("content", "The answer "), ("content", "is 4.")]

    def test_the_block_never_reaches_the_answer_channel(self, server):
        _, _, seen = _both_ways(
            server, QWEN_ID, _completion(QWEN_ID, "<think>secret</think>said"),
            [{"content": "<think>sec"}, {"content": "ret</think>sa"}, {"content": "id"}],
        )
        answered = "".join(text for kind, text in seen if kind == "content")
        assert answered == "said"
        assert "<think>" not in answered and "secret" not in answered


class TestGenericReplies:
    def test_a_plain_openai_reply_is_untouched(self, server):
        instance = server(lambda m, p, b: json_reply(_completion(UNKNOWN_ID, "The answer is 4.")))
        reply = Client(instance.base_url).chat([])
        assert reply.content == "The answer is 4."
        assert reply.thinking is None
        assert reply.tool_calls is None

    def test_streaming_a_plain_reply_matches(self, server):
        whole, streamed, seen = _both_ways(
            server, UNKNOWN_ID, _completion(UNKNOWN_ID, "The answer is 4."),
            [{"role": "assistant"}, {"content": "The answer "}, {"content": "is 4."}],
        )
        assert whole.content == "The answer is 4."
        _same(whole, streamed)
        assert seen == [("content", "The answer "), ("content", "is 4.")]

    def test_an_unknown_model_still_gives_up_its_reasoning_field(self, server):
        """Detection is not required for correctness: the fallback reads both conventions."""
        instance = server(lambda m, p, b: json_reply(
            _completion(UNKNOWN_ID, "4", reasoning_content="add them up")))
        reply = Client(instance.base_url).chat([])
        assert reply.content == "4" and reply.thinking == "add them up"

    def test_an_unknown_model_still_gives_up_an_in_band_block(self, server):
        instance = server(lambda m, p, b: json_reply(
            _completion(UNKNOWN_ID, "<think>add them up</think>4")))
        reply = Client(instance.base_url).chat([])
        assert reply.content == "4" and reply.thinking == "add them up"

    def test_a_reply_with_no_model_id_at_all_still_reads(self, server):
        payload = _completion(UNKNOWN_ID, "<think>a</think>b", reasoning_content="c")
        payload.pop("model")
        instance = server(lambda m, p, b: json_reply(payload))
        reply = Client(instance.base_url).chat([])
        assert reply.content == "b" and reply.thinking == "c\na"


class TestFamilyOverride:
    def test_a_pinned_family_beats_the_model_id(self, server):
        """gpt-oss carries no in-band delimiter, so pinning it keeps the tag as text."""
        instance = server(lambda m, p, b: json_reply(_completion(UNKNOWN_ID, "<think>a</think>b")))
        assert Client(instance.base_url).chat([]).content == "b"
        pinned = Client(instance.base_url, family="gpt-oss").chat([])
        assert pinned.content == "<think>a</think>b" and pinned.thinking is None

    def test_a_pinned_family_beats_the_model_id_while_streaming(self, server):
        body = _sse(_stream_of(UNKNOWN_ID, [{"content": "<think>a</think>"}, {"content": "b"}]))
        instance = server(lambda m, p, b: (200, body))
        seen: list[tuple[str, str]] = []
        pinned = Client(instance.base_url, family="gpt-oss").chat(
            [], on_delta=lambda k, t: seen.append((k, t)))
        assert pinned.content == "<think>a</think>b"
        assert seen == [("content", "<think>a</think>"), ("content", "b")]

    def test_a_pinned_family_shapes_the_request(self, server):
        """A harmony template has no enable_thinking; it reads reasoning_effort."""
        seen = {}

        def handler(method, path, body):
            seen["body"] = json.loads(body)
            return json_reply(_completion(GPT_OSS_ID, "{}"))

        instance = server(handler)
        Client(instance.base_url, family="gpt-oss").extract(
            "x", {"type": "object", "properties": {}})
        assert seen["body"]["chat_template_kwargs"] == {"reasoning_effort": "low"}

    def test_the_served_model_id_shapes_the_request_when_nothing_is_pinned(self, server):
        seen = {}

        def handler(method, path, body):
            if path == "/v1/models":
                return json_reply({"data": [{"id": GPT_OSS_ID}]})
            seen["body"] = json.loads(body)
            return json_reply(_completion(GPT_OSS_ID, "{}"))

        instance = server(handler)
        Client(instance.base_url).extract("x", {"type": "object", "properties": {}})
        assert seen["body"]["chat_template_kwargs"] == {"reasoning_effort": "low"}

    def test_a_generic_server_keeps_the_enable_thinking_switch(self, server):
        seen = {}

        def handler(method, path, body):
            if path == "/v1/models":
                return json_reply({"data": [{"id": UNKNOWN_ID}]})
            seen["body"] = json.loads(body)
            return json_reply(_completion(UNKNOWN_ID, "{}"))

        instance = server(handler)
        Client(instance.base_url).extract("x", {"type": "object", "properties": {}})
        assert seen["body"]["chat_template_kwargs"] == {"enable_thinking": False}


class TestEmptyAnswerIsLoud:
    """A response that carried text and yielded nothing is a defect in this client, and
    names the fields it did not read."""

    def test_text_in_an_unrecognised_field_is_announced(self, server, caplog):
        payload = _completion(UNKNOWN_ID, "")
        payload["choices"][0]["message"]["output_text"] = "the whole answer"
        instance = server(lambda m, p, b: json_reply(payload))
        with caplog.at_level("WARNING", logger="poolhouse.client.chat"):
            reply = Client(instance.base_url).chat([])
        assert not (reply.content or "").strip()
        said = [r.getMessage() for r in caplog.records]
        assert len(said) == 1 and "output_text" in said[0]
        assert "the whole answer" not in said[0]

    def test_text_in_a_choice_level_field_is_announced(self, server, caplog):
        payload = _completion(UNKNOWN_ID, "")
        payload["choices"][0]["text"] = "the whole answer"
        instance = server(lambda m, p, b: json_reply(payload))
        with caplog.at_level("WARNING", logger="poolhouse.client.chat"):
            Client(instance.base_url).chat([])
        said = [r.getMessage() for r in caplog.records]
        assert len(said) == 1 and "text" in said[0]
        assert "the whole answer" not in said[0]

    def test_a_recovered_reply_stays_quiet(self, server, caplog):
        payload = _completion(GPT_OSS_ID, "", reasoning_content="counting the people")
        instance = server(lambda m, p, b: json_reply(payload))
        with caplog.at_level("WARNING", logger="poolhouse.client.chat"):
            reply = Client(instance.base_url).chat([])
        assert reply.thinking == "counting the people"
        assert caplog.records == []

    def test_a_genuinely_empty_reply_stays_quiet(self, server, caplog):
        instance = server(lambda m, p, b: json_reply(_completion(UNKNOWN_ID, "")))
        with caplog.at_level("WARNING", logger="poolhouse.client.chat"):
            Client(instance.base_url).chat([])
        assert caplog.records == []

    def test_a_tool_call_with_no_prose_stays_quiet(self, server, caplog):
        payload = _completion(UNKNOWN_ID, "", tool_calls=[
            {"id": "c1", "type": "function",
             "function": {"name": "look_up", "arguments": "{}"}}])
        instance = server(lambda m, p, b: json_reply(payload))
        with caplog.at_level("WARNING", logger="poolhouse.client.chat"):
            reply = Client(instance.base_url).chat([])
        assert reply.tool_calls is not None
        assert caplog.records == []

    def test_an_unread_streamed_field_is_announced_too(self, server, caplog):
        """Streaming says the same thing the whole reply does, and keeps the text in raw."""
        body = _sse(_stream_of(UNKNOWN_ID, [
            {"role": "assistant"}, {"output_text": "the whole "}, {"output_text": "answer"}]))
        instance = server(lambda m, p, b: (200, body))
        with caplog.at_level("WARNING", logger="poolhouse.client.chat"):
            reply = Client(instance.base_url).chat([], on_delta=lambda k, t: None)
        said = [r.getMessage() for r in caplog.records]
        assert len(said) == 1 and "output_text" in said[0]
        assert "the whole answer" not in said[0]
        assert reply.raw["choices"][0]["message"]["output_text"] == "the whole answer"


class TestStreamedToolCallsAcrossFamilies:
    def test_a_call_split_across_chunks_matches_the_whole_reply(self, server):
        call = {"id": "c9", "type": "function",
                "function": {"name": "look_up", "arguments": '{"text": "iron"}'}}
        payload = _completion(GPT_OSS_ID, "", tool_calls=[call])
        payload["choices"][0]["finish_reason"] = "tool_calls"
        whole, streamed, _ = _both_ways(
            server, GPT_OSS_ID, payload,
            [{"role": "assistant"},
             {"tool_calls": [{"index": 0, "id": "c9",
                              "function": {"name": "look_up", "arguments": '{"te'}}]},
             {"tool_calls": [{"index": 0, "function": {"arguments": 'xt": "iron"}'}}]}],
            finish="tool_calls",
        )
        assert whole.tool_calls == [call]
        _same(whole, streamed)

    def test_a_call_alongside_an_in_band_block_matches(self, server):
        call = {"id": "c1", "type": "function",
                "function": {"name": "look_at", "arguments": '{"ids": []}'}}
        payload = _completion(QWEN_ID, "<think>look first</think>", tool_calls=[call])
        payload["choices"][0]["finish_reason"] = "tool_calls"
        whole, streamed, seen = _both_ways(
            server, QWEN_ID, payload,
            [{"content": "<think>look "}, {"content": "first</think>"},
             {"tool_calls": [{"index": 0, "id": "c1",
                              "function": {"name": "look_at", "arguments": '{"ids'}}]},
             {"tool_calls": [{"index": 0, "function": {"arguments": '": []}'}}]}],
            finish="tool_calls",
        )
        assert whole.thinking == "look first" and whole.tool_calls == [call]
        _same(whole, streamed)
        assert [kind for kind, _ in seen] == ["thinking", "thinking"]


class TestHealth:
    def test_healthy_server_is_detected(self, server):
        instance = server(lambda m, p, b: json_reply({"status": "ok"}))
        assert is_healthy(instance.base_url)

    def test_dead_port_is_not(self):
        assert not is_healthy("http://127.0.0.1:1", timeout=0.5)

    def test_a_server_answering_only_props_is_healthy(self, server):
        instance = server(lambda m, p, b: json_reply({"n_ctx": 8192})
                          if p == "/props" else (404, b"no such route"))
        assert is_healthy(instance.base_url)

    def test_a_server_answering_only_models_is_healthy(self, server):
        instance = server(lambda m, p, b: json_reply({"data": []})
                          if p == "/models" else (404, b"no such route"))
        assert is_healthy(instance.base_url)

    def test_a_page_that_is_not_json_is_healthy_at_its_own_path(self, server):
        instance = server(lambda m, p, b: (200, b"<html></html>") if p == "/"
                          else (404, b"no such route"))
        assert is_healthy(instance.base_url, path="/")
        assert not is_healthy(instance.base_url)

    def test_wait_returns_as_soon_as_the_server_answers(self, server):
        ready_at = time.monotonic() + 0.5

        def handler(method: str, path: str, body: bytes):
            if time.monotonic() < ready_at:
                return 503, b"loading"
            return json_reply({"status": "ok"})

        instance = server(handler)
        assert wait_for_health(instance.base_url, timeout=10.0)

    def test_process_death_ends_the_wait_immediately(self):
        """A bad model path makes a server exit in under a second. Polling a dead port
        for two minutes turns 'no such file' into 'timed out', which sends the reader
        looking for a slow load that never happened."""
        started = time.monotonic()
        alive = False

        assert not wait_for_health(
            "http://127.0.0.1:1", timeout=30.0, is_alive=lambda: alive
        )
        assert time.monotonic() - started < 2.0, "kept polling a dead process"

    def test_wait_times_out_when_nothing_ever_answers(self):
        assert not wait_for_health("http://127.0.0.1:1", timeout=0.6)

    def test_serving_params_reads_back_what_actually_ran(self, server):
        instance = server(lambda m, p, b: json_reply({
            "model_path": "/models/Qwen3-32B-Q5_K_M.gguf",
            "total_slots": 4,
            "default_generation_settings": {"n_ctx": 8192, "seed": 1234},
        }))
        params = serving_params(instance.base_url)
        assert params is not None
        assert (params.n_ctx, params.total_slots, params.seed) == (8192, 4, 1234)
        assert params.quant == "Q5_K_M"

    def test_serving_params_reads_identity_and_every_sampling_default(self, server):
        instance = server(lambda m, p, b: json_reply({
            "model_alias": "unsloth/Qwen3.5-0.8B-GGUF",
            "model_ftype": "Q4_K - Medium",
            "model_path": "/hub/Qwen3.5-0.8B-Q4_K_M.gguf",
            "build_info": "b7000-fakebuild",
            "total_slots": 4,
            "default_generation_settings": {"n_ctx": 262144, "params": {
                "temperature": 0.8, "top_k": 40, "top_p": 0.95, "presence_penalty": 0.0,
                "n_predict": -1, "reasoning_format": "deepseek",
                "speculative.types": "draft", "speculative.n_max": 16, "lora": []}},
        }))
        params = serving_params(instance.base_url)
        assert (params.model_alias, params.file_type, params.build_info) == (
            "unsloth/Qwen3.5-0.8B-GGUF", "Q4_K - Medium", "b7000-fakebuild")
        assert params.reasoning_format == "deepseek"
        assert params.speculative == {"speculative.n_max": 16, "speculative.types": "draft"}
        assert params.sampling == {"n_predict": -1, "presence_penalty": 0.0,
                                   "temperature": 0.8, "top_k": 40, "top_p": 0.95}

    def test_sampling_defaults_are_read_from_nested_params(self, server):
        instance = server(lambda m, p, b: json_reply({
            "n_parallel": 2,
            "default_generation_settings": {"n_ctx": 4096, "repeat_penalty": 1.0,
                                            "params": {"min_p": 0.05, "repeat_penalty": 1.1}},
        }))
        params = serving_params(instance.base_url)
        assert (params.min_p, params.repeat_penalty, params.total_slots) == (0.05, 1.1, 2)

    def test_sentinel_seed_is_discarded(self, server):
        """Reading -1 back as a real seed makes a run look reproducible when it is not."""
        instance = server(lambda m, p, b: json_reply(
            {"default_generation_settings": {"n_ctx": 512, "seed": -1}}))
        assert serving_params(instance.base_url).seed is None

    def test_no_server_is_none_but_an_odd_shape_is_not(self, server):
        """Collapsing these two turns a llama.cpp version bump into a silent loss of
        provenance."""
        assert serving_params("http://127.0.0.1:1", timeout=0.5) is None

        instance = server(lambda m, p, b: json_reply({"unfamiliar": "shape"}))
        params = serving_params(instance.base_url)
        assert params is not None and params.n_ctx is None

    def test_reported_models_enables_adoption(self, server):
        instance = server(lambda m, p, b: json_reply(
            {"data": [{"id": "qwen3"}, {"id": "gemma4"}, {"no_id": 1}]}))
        assert reported_models(instance.base_url) == ["qwen3", "gemma4"]

    @pytest.mark.parametrize(
        "path,expected",
        [("/m/Qwen3-32B-Q5_K_M.gguf", "Q5_K_M"),
         ("model-Q8_0.gguf", "Q8_0"),
         ("weights-BF16.gguf", "BF16"),
         ("Qwen3-4B.Q4_K_M.gguf", "Q4_K_M"),
         ("Qwen3-30B-A3B-UD-Q4_K_XL.gguf", "Q4_K_XL"),
         ("gemma-IQ4_XS.gguf", "IQ4_XS"),
         ("gpt-oss-120b-mxfp4-00001-of-00003.gguf", "MXFP4"),
         ("Qwen3-8B.gguf", None),
         ("plain.gguf", None)],
    )
    def test_quant_is_recovered_from_the_filename(self, path, expected):
        """The server does not report quantisation, and it is the single most important
        thing to record when comparing two benchmark runs."""
        assert quant_from_model_path(path) == expected


