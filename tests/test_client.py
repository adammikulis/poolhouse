"""The client, against a real HTTP server on a real socket.

Nothing here mocks ``urlopen``. The failures these modules exist to prevent are
transport-shaped -- a server that answers /health while still loading, one that 500s on a
concurrent request, one that ignores a grammar -- and a mocked transport cannot produce
any of them.
"""

from __future__ import annotations

import json
import uuid
from typing import ClassVar

import pytest
from conftest import json_reply

from poolhouse.client import (
    Client,
    EmbeddingError,
    GrammarUnsupportedError,
    Request,
    ServerError,
    ServerUnreachable,
    Transport,
    embed,
    estimate_tokens,
    heuristic_tokens,
)
from poolhouse.extraction import Checking, Prompting
from poolhouse.http import Options, Retry
from poolhouse.testing.canned import (
    CANNED_MARKER,
    CANNED_MODEL_ID,
    canned_chat_reply,
    canned_completion_reply,
    canned_embedding_reply,
    canned_stream_reply,
    marked,
)


class TestRetry:
    def test_retries_a_local_500_and_succeeds(self, server):
        """Local servers commonly answer 500 to a request that overlaps another."""
        # A request that is not this test's (another worker's stale poller can land on a port
        # that was just handed to this server) is answered 404 and counted nowhere: only the
        # requests carrying this test's own model name are the attempts under test.
        mine = f"retry-{uuid.uuid4().hex}"
        state = {"calls": 0}
        strangers: list[bytes] = []

        def handler(method: str, path: str, body: bytes):
            try:
                ours = json.loads(body).get("model") == mine
            except ValueError:
                ours = False
            if not ours:
                strangers.append(body)
                return 404, b"not for this test"
            state["calls"] += 1
            if state["calls"] < 3:
                return 500, b"busy"
            return canned_embedding_reply([0.1, 0.2])

        instance = server(handler)
        # one stranger on purpose, so the isolation above is exercised every run, not only under load
        with pytest.raises(ServerError):
            embed("someone else", base_url=instance.base_url, model="not-mine", options=Options(timeout=60.0, retry=Retry(tries=1)))
        assert len(strangers) == 1 and state["calls"] == 0
        vectors = embed("hello", base_url=instance.base_url, model=mine, options=Options(timeout=60.0, retry=Retry(tries=3)))
        assert vectors == [[0.1, 0.2]]
        assert state["calls"] == 3, f"strangers on this port: {strangers}"

    def test_never_retries_a_4xx(self, server):
        """The request is wrong and will stay wrong; hammering only delays the report."""
        state = {"calls": 0}

        def handler(method: str, path: str, body: bytes):
            state["calls"] += 1
            return 400, b'{"error": "bad request"}'

        instance = server(handler)
        with pytest.raises(ServerError):
            embed("x", base_url=instance.base_url, options=Options(timeout=60.0, retry=Retry(tries=5)))
        assert state["calls"] == 1

    def test_unreachable_raises_the_specific_error(self):
        with pytest.raises(ServerUnreachable):
            embed("x", base_url="http://127.0.0.1:1", options=Options(timeout=0.5, retry=Retry(tries=1)))


class TestChat:
    def test_round_trip(self, server):
        instance = server(lambda m, p, b: canned_chat_reply("hello there"))
        reply = Client(instance.base_url).chat([{"role": "user", "content": "hi"}])
        assert reply.content == marked("hello there")
        assert reply.raw["model"] == CANNED_MODEL_ID, "the reply says it is not a model"

    def test_thinking_is_split_off_the_content(self, server):
        instance = server(lambda m, p, b: canned_chat_reply("<think>hmm</think>42"))
        reply = Client(instance.base_url).chat([])
        assert reply.content == marked("42") and reply.thinking == "hmm"

    def test_the_request_actually_carries_the_slot(self, server):
        instance = server(lambda m, p, b: canned_chat_reply("ok"))
        Client(instance.base_url, request=Request(slot=7)).chat([{"role": "user", "content": "x"}])
        _, path, body = instance.requests[-1]
        assert path == "/v1/chat/completions"
        assert json.loads(body)["id_slot"] == 7


    def test_streaming_deltas_are_reported_and_assembled(self, server):
        stream = canned_stream_reply([{"reasoning_content": "hmm "}, {"reasoning_content": "ok"},
                                      {"content": "4"}, {"content": "2"}])
        instance = server(lambda m, p, b: stream)
        got: list[tuple[str, str]] = []
        reply = Client(instance.base_url).chat(
            [{"role": "user", "content": "x"}], on_delta=lambda k, t: got.append((k, t)))
        assert reply.content == marked("42") and reply.thinking == "hmm ok"
        assert reply.finish_reason == "stop"
        assert got == [("content", f"{CANNED_MARKER} "), ("thinking", "hmm "), ("thinking", "ok"),
                       ("content", "4"), ("content", "2")]
        assert json.loads(instance.requests[-1][2])["stream"] is True

    def test_streamed_tool_calls_are_assembled_across_chunks(self, server):
        stream = canned_stream_reply([
            {"tool_calls": [{"index": 0, "id": "c9", "function": {"name": "look_up", "arguments": "{\"te"}}]},
            {"tool_calls": [{"index": 0, "function": {"arguments": "xt\": \"iron\"}"}}]}],
            finish="tool_calls")
        instance = server(lambda m, p, b: stream)
        reply = Client(instance.base_url).chat([], on_delta=lambda k, t: None)
        assert reply.tool_calls == [{"id": "c9", "type": "function", "function": {
            "name": "look_up", "arguments": '{"text": "iron"}'}}]


class TestGrammarTripwire:
    def test_passes_when_the_grammar_is_honoured(self, server):
        instance = server(lambda m, p, b: canned_completion_reply("ok", bare=True))
        Client(instance.base_url).assert_grammar_support()

    def test_sends_a_non_empty_prompt(self, server):
        """llama-server predicts zero tokens for an empty prompt, grammar or not."""
        instance = server(lambda m, p, b: canned_completion_reply("ok", bare=True))
        Client(instance.base_url).assert_grammar_support()
        _, _, body = instance.requests[-1]
        assert json.loads(body)["prompt"].strip()

    def test_fails_loudly_when_the_grammar_is_ignored(self, server):
        """A server that ignores GBNF does not error -- it returns fluent prose where a
        single token was required, and every downstream parse produces confident nonsense."""
        instance = server(lambda m, p, b: canned_completion_reply(
            "Certainly! I would be happy to help you with that."))
        with pytest.raises(GrammarUnsupportedError, match="not working"):
            Client(instance.base_url).assert_grammar_support()

    def test_fails_when_the_server_is_absent(self):
        with pytest.raises(GrammarUnsupportedError, match="could not run"):
            Client("http://127.0.0.1:1", transport=Transport(timeout=0.5)).assert_grammar_support()

    def test_budget_exhaustion_retries_once_at_double_with_a_fresh_seed(self, server):
        """The fresh seed matters: the same seed re-walks the same path into the same
        ceiling."""
        seen: list[dict] = []

        def handler(method: str, path: str, body: bytes):
            payload = json.loads(body)
            seen.append(payload)
            if len(seen) == 1:
                return canned_completion_reply("{partial", bare=True, stopped_limit=True)
            return canned_completion_reply('{"done": true}', bare=True)

        instance = server(handler)
        out = Client(instance.base_url).complete("x", grammar="root ::= .+", n_predict=64)

        assert out == '{"done": true}'
        assert len(seen) == 2
        assert seen[1]["n_predict"] == 128, "did not double the budget"
        assert seen[1].get("seed") != seen[0].get("seed"), "reused the seed that hit the ceiling"

    def test_no_retry_without_a_grammar(self, server):
        """Plain truncation is short, not unparseable -- a different problem."""
        calls = {"n": 0}

        def handler(method: str, path: str, body: bytes):
            if path.endswith("/completion"):     # the family probe is not a retry
                calls["n"] += 1
            return canned_completion_reply("cut off", stopped_limit=True)

        instance = server(handler)
        Client(instance.base_url).complete("x", n_predict=16)
        assert calls["n"] == 1


class TestEmbeddings:
    def test_dimension_mismatch_raises_rather_than_padding(self, server):
        """Padding or trimming to the expected width so an insert can never fail turns a
        model mismatch into a store full of garbage vectors that still cosine-compare."""
        instance = server(lambda m, p, b: canned_embedding_reply([0.1] * 384))
        with pytest.raises(EmbeddingError, match="corrupt the index"):
            embed("x", base_url=instance.base_url, expect_dim=768, options=Options(timeout=60.0, retry=Retry(tries=1)))

    def test_unwraps_a_nested_pooled_vector(self, server):
        instance = server(lambda m, p, b: canned_embedding_reply([[1.0, 2.0]]))
        assert embed("x", base_url=instance.base_url) == [[1.0, 2.0]]

    def test_a_short_batch_is_an_error_not_a_silent_truncation(self, server):
        instance = server(lambda m, p, b: canned_embedding_reply([1.0]))
        with pytest.raises(EmbeddingError, match="asked for 2"):
            embed(["a", "b"], base_url=instance.base_url, options=Options(timeout=60.0, retry=Retry(tries=1)))

    def test_empty_input_does_not_hit_the_network(self, server):
        instance = server(lambda m, p, b: canned_embedding_reply())
        assert embed([], base_url=instance.base_url) == []
        assert not instance.requests

    def test_cosine_of_identical_vectors_is_one(self):
        from poolhouse.client import cosine

        assert cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_cosine_rejects_a_dimension_mismatch(self):
        """Incomparable vectors are a caller's bug, so this is not a ServerError: a
        handler waiting for an unreachable server must not swallow it."""
        from poolhouse.client import VectorMismatch, cosine
        from poolhouse.http import ServerError

        with pytest.raises(VectorMismatch):
            cosine([1.0], [1.0, 2.0])
        assert not issubclass(VectorMismatch, ServerError)

    def test_rank_pairs_returns_every_pair_once_best_first(self):
        from poolhouse.client import rank_pairs

        vectors = [[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]]
        pairs = rank_pairs(vectors)
        assert [(i, j) for i, j, _ in pairs] == [(0, 1), (1, 2), (0, 2)]
        assert pairs[0][2] > pairs[-1][2]

    def test_rank_pairs_breaks_ties_by_index_so_the_order_is_stable(self):
        from poolhouse.client import rank_pairs

        vectors = [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]]
        assert [(i, j) for i, j, _ in rank_pairs(vectors)] == [(0, 1), (0, 2), (1, 2)]

    def test_rank_pairs_limit_keeps_the_best(self):
        from poolhouse.client import rank_pairs

        vectors = [[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]]
        assert [(i, j) for i, j, _ in rank_pairs(vectors, limit=1)] == [(0, 1)]

    def test_rank_pairs_of_one_vector_is_empty(self):
        from poolhouse.client import rank_pairs

        assert rank_pairs([[1.0, 0.0]]) == []


class TestTokenEstimate:
    def test_over_counts_rather_than_under(self):
        """A prompt the assembler believes fits must always fit, so the estimate has to
        err high. Real Qwen BPE on this sentence is around 14 tokens."""
        text = "The quick brown fox jumps over the lazy dog and keeps running."
        assert estimate_tokens(text) >= len(text.split())

    def test_empty_is_zero(self):
        assert estimate_tokens("") == 0

    def test_punctuation_soup_costs_more_than_prose_of_the_same_length(self):
        assert heuristic_tokens("!!!???<<<>>>...") > heuristic_tokens("aaaaaaaaaaaaaaa")

    def test_an_installed_counter_is_used_and_can_be_removed(self):
        from poolhouse.client import set_token_counter

        try:
            set_token_counter(lambda _: 99)
            assert estimate_tokens("anything") == 99
            assert heuristic_tokens("anything") != 99, "the heuristic must stay reachable"
        finally:
            set_token_counter(None)
        assert estimate_tokens("anything") != 99


class TestTokenize:
    """The server's tokenizer is the one the model actually reads with. When it
    disagrees with the tokenizer the model was TRAINED with, nothing errors --
    generation stays fluent while every sequence means something else."""

    def test_returns_the_servers_ids(self, server):
        instance = server(lambda m, p, b: json_reply({"tokens": [1, 2087, 9]}))
        assert Client(instance.base_url).tokenize("go") == [1, 2087, 9]

    def test_with_pieces_is_forwarded_so_a_mismatch_can_be_explained(self, server):
        seen = {}

        def handler(method, path, body):
            seen["path"] = path
            seen["body"] = json.loads(body)
            return json_reply({"tokens": [{"id": 2087, "piece": "go"}]})

        instance = server(handler)
        out = Client(instance.base_url).tokenize("go", with_pieces=True)
        assert seen["path"] == "/tokenize"
        assert seen["body"]["with_pieces"] is True
        assert out == [{"id": 2087, "piece": "go"}]

    def test_a_response_without_tokens_is_empty_not_a_crash(self, server):
        instance = server(lambda m, p, b: json_reply({}))
        assert Client(instance.base_url).tokenize("go") == []

    def test_detokenize_round_trips_through_content(self, server):
        instance = server(lambda m, p, b: json_reply({"content": "<user> go"}))
        assert Client(instance.base_url).detokenize([1, 2, 3]) == "<user> go"

    def test_detokenize_posts_the_ids_it_was_given(self, server):
        seen = {}

        def handler(method, path, body):
            seen["path"] = path
            seen["body"] = json.loads(body)
            return json_reply({"content": "x"})

        instance = server(handler)
        Client(instance.base_url).detokenize((4, 5))
        assert seen["path"] == "/detokenize"
        assert seen["body"]["tokens"] == [4, 5]


class TestExtract:
    """A schema in, a dict out, over a real socket. The default path goes through the
    server's chat template; ``prompt=`` keeps the raw endpoint under a grammar."""

    SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "people": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"},
                                   "role": {"type": "string"}},
                },
            },
        },
    }

    @staticmethod
    def chat_reply(content):
        return canned_chat_reply(content, bare=True)

    def test_a_document_comes_back_as_a_dict(self, server):
        found = {"people": [{"name": "Ada", "role": "engineer"}]}
        instance = server(lambda m, p, b: self.chat_reply(json.dumps(found)))

        out = Client(instance.base_url).extract("Ada is an engineer.", self.SCHEMA)
        assert out == found

    def test_it_asks_the_chat_endpoint_under_the_schema_with_thinking_off(self, server):
        seen = {}

        def handler(method, path, body):
            seen["path"] = path
            seen["body"] = json.loads(body)
            return self.chat_reply('{"people": []}')

        instance = server(handler)
        out = Client(instance.base_url).extract(
            "Ada is an engineer.", self.SCHEMA,
            prompting=Prompting(instructions="Pull out the people.", n_predict=256))

        assert out == {"people": []}
        assert seen["path"] == "/v1/chat/completions"
        assert seen["body"]["response_format"]["type"] == "json_schema"
        from poolhouse.client.chat import strict_schema
        assert seen["body"]["response_format"]["json_schema"]["schema"] == strict_schema(self.SCHEMA)
        assert seen["body"]["response_format"]["json_schema"]["name"] == "extraction"
        assert seen["body"]["chat_template_kwargs"]["enable_thinking"] is False
        assert seen["body"]["n_predict"] == 256
        assert seen["body"]["messages"] == [
            {"role": "system", "content": "Pull out the people."},
            {"role": "user", "content": "Ada is an engineer."},
        ]

    def test_the_default_system_message_asks_for_json_and_no_prose(self, server):
        seen = {}

        def handler(method, path, body):
            seen["body"] = json.loads(body)
            return self.chat_reply("{}")

        instance = server(handler)
        Client(instance.base_url).extract("Ada is an engineer.", self.SCHEMA)

        assert seen["body"]["messages"][0] == {
            "role": "system",
            "content": "Return only a JSON document that matches the schema. No prose.",
        }

    def test_thinking_can_be_turned_back_on(self, server):
        seen = {}

        def handler(method, path, body):
            seen["body"] = json.loads(body)
            return self.chat_reply("{}")

        instance = server(handler)
        Client(instance.base_url).extract("Ada is an engineer.", self.SCHEMA,
                                          prompting=Prompting(think=True))

        assert seen["body"]["chat_template_kwargs"]["enable_thinking"] is True

    def test_a_schema_name_reaches_the_response_format(self, server):
        seen = {}

        def handler(method, path, body):
            seen["body"] = json.loads(body)
            return self.chat_reply("{}")

        instance = server(handler)
        Client(instance.base_url).extract("Ada is an engineer.", self.SCHEMA,
                                          prompting=Prompting(schema_name="people"))

        assert seen["body"]["response_format"]["json_schema"]["name"] == "people"

    def test_supplied_messages_are_sent_word_for_word(self, server):
        seen: list[dict] = []
        convo = [{"role": "system", "content": "You index people."},
                 {"role": "user", "content": "Ada is an engineer."},
                 {"role": "assistant", "content": "Understood."}]

        def handler(method, path, body):
            seen.append(json.loads(body))
            return self.chat_reply("{}")

        instance = server(handler)
        Client(instance.base_url).extract("ignored", self.SCHEMA,
                                          prompting=Prompting(instructions="ignored",
                                                              messages=convo))

        assert seen[0]["messages"] == convo

    def test_malformed_json_is_retried_once_with_a_fresh_seed(self, server):
        seen: list[dict] = []

        def handler(method, path, body):
            seen.append(json.loads(body))
            if len(seen) == 1:
                return self.chat_reply("Here you go: {people:")
            return self.chat_reply('{"people": []}')

        instance = server(handler)
        out = Client(instance.base_url).extract("Ada is an engineer.", self.SCHEMA)

        assert out == {"people": []}
        assert len(seen) == 2
        assert seen[1].get("seed") != seen[0].get("seed")

    def test_two_malformed_replies_raise_with_what_came_back(self, server):
        instance = server(lambda m, p, b: self.chat_reply("I cannot do that."))

        with pytest.raises(ServerError, match=r"I cannot do that\."):
            Client(instance.base_url).extract("Ada is an engineer.", self.SCHEMA)

    def test_the_raw_text_in_the_error_is_truncated(self, server):
        instance = server(lambda m, p, b: self.chat_reply("no " * 500))

        with pytest.raises(ServerError) as exc:
            Client(instance.base_url).extract("x", self.SCHEMA)
        assert len(str(exc.value)) < 300

    def test_an_accepted_answer_is_returned_without_asking_again(self, server):
        found = {"people": [{"name": "Ada", "role": "engineer"}]}
        seen: list[dict] = []

        def handler(method, path, body):
            seen.append(json.loads(body))
            return self.chat_reply(json.dumps(found))

        instance = server(handler)
        out = Client(instance.base_url).extract("Ada is an engineer.", self.SCHEMA,
                                                checking=Checking(check=lambda obj: []))

        assert out == found
        assert "_objections" not in out
        assert len(seen) == 1

    def test_a_rejected_answer_comes_back_as_a_turn_of_its_own(self, server):
        seen: list[dict] = []

        def handler(method, path, body):
            seen.append(json.loads(body))
            if len(seen) == 1:
                return self.chat_reply('{"people": []}')
            return self.chat_reply('{"people": [{"name": "Ada"}]}')

        instance = server(handler)
        out = Client(instance.base_url).extract(
            "Ada is an engineer.", self.SCHEMA,
            checking=Checking(check=lambda obj: [] if obj["people"] else ["no people were found"]))

        assert out == {"people": [{"name": "Ada"}]}
        assert "_objections" not in out
        assert len(seen) == 2

        messages = seen[1]["messages"]
        assert messages[0]["role"] == "system"
        assert messages[:2] == seen[0]["messages"]
        assert messages[2] == {"role": "assistant", "content": '{"people": []}'}
        assert messages[3]["role"] == "user"
        assert "no people were found" in messages[3]["content"]
        assert "no people were found" not in messages[2]["content"]

    def test_objections_that_survive_every_try_come_back_on_the_object(self, server):
        seen: list[dict] = []

        def handler(method, path, body):
            seen.append(json.loads(body))
            return self.chat_reply('{"people": []}')

        instance = server(handler)
        out = Client(instance.base_url).extract(
            "Ada is an engineer.", self.SCHEMA,
            checking=Checking(tries=3,
                              check=lambda obj: ["no people were found", "role is missing"]))

        assert len(seen) == 3
        assert out["people"] == []
        assert out["_objections"] == ["no people were found", "role is missing"]

    def test_one_try_returns_the_rejected_answer_without_asking_again(self, server):
        seen: list[dict] = []

        def handler(method, path, body):
            seen.append(json.loads(body))
            return self.chat_reply('{"people": []}')

        instance = server(handler)
        out = Client(instance.base_url).extract(
            "Ada is an engineer.", self.SCHEMA,
            checking=Checking(tries=1, check=lambda obj: ["no people were found"]))

        assert len(seen) == 1
        assert out["_objections"] == ["no people were found"]

    def test_a_supplied_prompt_asks_the_completion_endpoint_with_the_grammar(self, server):
        from poolhouse.contracts import grammar_for

        seen: list[dict] = []

        def handler(method, path, body):
            seen.append((path, json.loads(body)))
            return canned_completion_reply("{}", bare=True)

        instance = server(handler)
        Client(instance.base_url).extract(
            "Ada is an engineer.", self.SCHEMA,
            prompting=Prompting(instructions="ignored", n_predict=256,
                                prompt="<|user|>find the people<|assistant|>"))

        assert seen[0][0] == "/completion"
        assert seen[0][1]["prompt"] == "<|user|>find the people<|assistant|>"
        assert seen[0][1]["grammar"] == grammar_for(self.SCHEMA)
        assert seen[0][1]["n_predict"] == 256

    def test_the_rejection_goes_before_the_assistant_opener(self, server):
        seen: list[dict] = []

        def handler(method, path, body):
            seen.append(json.loads(body))
            return canned_completion_reply('{"people": []}', bare=True)

        instance = server(handler)
        base = "<|im_start|>user\nfind the people<|im_end|>\n<|im_start|>assistant\n"
        Client(instance.base_url).extract("Ada is an engineer.", self.SCHEMA,
                                          prompting=Prompting(prompt=base),
                                          checking=Checking(
                                              check=lambda obj: ["no people were found"]))

        retried = seen[1]["prompt"]
        assert len(seen) == 2
        assert retried.endswith("<|im_start|>assistant\n")
        assert "no people were found" in retried
        assert retried.index("no people were found") < retried.index(
            "<|im_start|>assistant\n")
        assert retried.startswith("<|im_start|>user\nfind the people<|im_end|>\n")

    def test_a_rejection_with_no_assistant_opener_is_appended(self, server):
        seen: list[dict] = []

        def handler(method, path, body):
            seen.append(json.loads(body))
            return canned_completion_reply('{"people": []}', bare=True)

        instance = server(handler)
        Client(instance.base_url).extract("Ada is an engineer.", self.SCHEMA,
                                          prompting=Prompting(prompt="find the people\nJSON:\n"),
                                          checking=Checking(
                                              check=lambda obj: ["no people were found"]))

        assert seen[1]["prompt"].startswith(seen[0]["prompt"])
        assert "no people were found" in seen[1]["prompt"]

    def test_a_schema_it_cannot_constrain_never_reaches_the_server(self, server):
        from poolhouse.contracts import ContractError

        calls = {"n": 0}

        def handler(method, path, body):
            calls["n"] += 1
            return canned_completion_reply("{}", bare=True)

        instance = server(handler)
        with pytest.raises(ContractError):
            Client(instance.base_url).extract("x",
                                              {"type": "object",
                                               "properties": {"a": {"type": "date"}}},
                                              prompting=Prompting(prompt="find it\nJSON:\n"))
        assert calls["n"] == 0



class TestStrictSchema:
    def test_every_object_requires_all_its_properties_and_nothing_else(self, server):
        """Without ``required`` the server's schema-to-grammar makes every key optional,
        and a model that skips ``title`` produces a record nothing downstream can use."""
        from poolhouse.client.chat import strict_schema
        schema = {"type": "object", "properties": {"items": {"type": "array", "items": {
            "type": "object", "properties": {"title": {"type": "string"}, "kind": {"type": "string"}}}}}}
        out = strict_schema(schema)
        assert out["required"] == ["items"] and out["additionalProperties"] is False
        inner = out["properties"]["items"]["items"]
        assert inner["required"] == ["title", "kind"] and inner["additionalProperties"] is False
        assert "required" not in schema  # the caller's schema is left alone

    def test_the_chat_request_carries_the_hardened_schema(self, server):
        instance = server(lambda m, p, b: canned_chat_reply('{"a": "x"}', bare=True))
        Client(instance.base_url).extract("t", {"type": "object", "properties": {"a": {"type": "string"}}})
        _, _, body = instance.requests[-1]
        sent = json.loads(body)["response_format"]["json_schema"]["schema"]
        assert sent["required"] == ["a"] and sent["additionalProperties"] is False


def test_a_model_card_informs_but_is_never_sent_on_its_own():
    """A card is general advice from a publisher who does not know the task.

    Applying it silently would mean the library overruling a caller who measured: gemma-4's
    card asks for temperature 1.0, which on a tool-calling task measured 15 points worse
    than greedy. So it is readable, and it is never what goes out.
    """
    from poolhouse.client.chat import Client
    from poolhouse.client.families import GEMMA, GPT_OSS

    gemma = Client("http://nowhere.invalid", family=GEMMA)
    assert gemma.card == {"temperature": 1.0, "top_p": 0.95, "top_k": 64}
    assert gemma.sampling == {"temperature": 0.0}          # greedy, whatever the card says
    assert "top_p" not in gemma.build_body([{"role": "user", "content": "x"}])

    # a caller who chooses gets what they chose, and only that
    chosen = Client("http://nowhere.invalid", family=GEMMA,
                    request=Request(temperature=0.7, top_k=40))
    assert chosen.sampling == {"temperature": 0.7, "top_k": 40}

    # and a card that says nothing leaves an empty record rather than an invented one
    oss = Client("http://nowhere.invalid", family=GPT_OSS)
    assert oss.card == {}
    assert oss.sampling == {"temperature": 0.0}


def test_the_card_comes_from_the_served_model_before_the_family(monkeypatch, tmp_path):
    """A family table is a guess about a whole lineage; the GGUF is the file being served.
    Qwen3.8-Flash-Next asks for top_k 20 where gemma-4 asks for 64 -- one table cannot
    hold both, and the file already knows."""
    import struct

    from poolhouse.client.chat import Client
    from poolhouse.client.families import GEMMA

    blob = bytearray(b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0)
                     + struct.pack("<Q", 1))
    key = b"general.sampling.top_k"
    blob += struct.pack("<Q", len(key)) + key + struct.pack("<I", 6) + struct.pack("<f", 20.0)
    where = tmp_path / "served.gguf"
    where.write_bytes(bytes(blob))

    import poolhouse.client.chat as chat
    monkeypatch.setattr(chat, "request_json", lambda *a, **k: {"model_path": str(where)},
                        raising=False)

    served = Client("http://nowhere.invalid", family=GEMMA)
    monkeypatch.setattr(served, "_from_gguf", lambda: {"top_k": 20.0})
    assert served.card == {"top_k": 20.0}, "the file being served wins"
    assert served.sampling == {"temperature": 0.0}, "and none of it is sent"

    # a server that will not say falls back to what the family knows
    quiet = Client("http://nowhere.invalid", family=GEMMA)
    monkeypatch.setattr(quiet, "_from_gguf", lambda: {})
    assert quiet.card == {"temperature": 1.0, "top_p": 0.95, "top_k": 64}


def test_think_becomes_the_familys_template_flag_and_never_a_body_key():
    """`chat(think=False)` must reach the server as the chat template's own switch;
    as a body key it was ignored and the model thought anyway (Flash-Next, 2026-09-02)."""
    from poolhouse.client import families
    from poolhouse.client.chat import Client

    client = Client("http://127.0.0.1:1", family="qwen")
    body = client.build_body([{"role": "user", "content": "hi"}], think=False)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "think" not in body
    body = client.build_body([{"role": "user", "content": "hi"}], think=True,
                             chat_template_kwargs={"other": 1})
    assert body["chat_template_kwargs"] == {"other": 1, "enable_thinking": True}
    plain = Client("http://127.0.0.1:1", family=families.GENERIC.name)
    body = plain.build_body([{"role": "user", "content": "hi"}], think=False)
    assert "think" not in body      # whatever the family's switch is, the word itself never goes


# -- the draft depth a request carries ----------------------------------------------------

def test_a_measured_draft_depth_goes_out_with_the_request():
    from poolhouse.client.chat import Client

    body = Client("http://127.0.0.1:1", request=Request(spec_draft_max=2)).build_body(
        [{"role": "user", "content": "hi"}])
    assert body["speculative.n_max"] == 2
    assert "speculative" not in body, "the server registers a flat field, not an object"


def test_a_hosted_endpoint_is_never_asked_to_guess_ahead():
    from poolhouse.client.chat import Client

    client = Client("https://api.openai.com/v1", request=Request(spec_draft_max=2),
                    transport=Transport(api="openai"))
    assert client.speculative == {}
    body = client.build_body([{"role": "user", "content": "hi"}])
    assert not [key for key in body if key.startswith("speculative")]


def test_a_server_that_refuses_the_depth_is_asked_again_without_it(monkeypatch):
    """A build without the per-request override must still answer, at the served depth."""
    from poolhouse.client import chat as chat_mod

    chat_mod.forget_speculative()
    sent: list[dict] = []

    def answering(url, *, payload, **_):
        sent.append(dict(payload))
        if any(key.startswith("speculative.") for key in payload):
            raise chat_mod.ServerError("400: unknown field 'speculative.n_max'")
        return json.loads(canned_chat_reply("ok")[1])

    monkeypatch.setattr(chat_mod, "request_json", answering)
    client = chat_mod.Client("http://127.0.0.1:1", request=Request(spec_draft_max=2))
    assert client.chat([{"role": "user", "content": "hi"}]).content == marked("ok")
    assert len(sent) == 2
    assert not [key for key in sent[1] if key.startswith("speculative.")]

    sent.clear()
    chat_mod.Client("http://127.0.0.1:1",
                    request=Request(spec_draft_max=2)).chat([{"role": "user", "content": "hi"}])
    assert len(sent) == 1, "the server is asked once, not once per client"
    chat_mod.forget_speculative()

@pytest.mark.parametrize("model", ["bottlecapai/ThinkingCap-Qwen3.8-27B", "ThinkingCap-Qwen3.8-27B-IQ4_XS.gguf"])
def test_thinkingcap_template_defaults_and_explicit_overrides(model):
    client = Client("http://127.0.0.1:1", model=model)
    messages = [{"role": "user", "content": "fixture"}]
    body = client.build_body(messages)
    assert body["chat_template_kwargs"] == {"enable_thinking": True, "reasoning_effort": "xhigh"}
    assert "thinking_budget" not in body
    assert client.build_body(messages, chat_template_kwargs={"reasoning_effort": "medium"})["chat_template_kwargs"]["reasoning_effort"] == "medium"
    assert client.build_body(messages, reasoning_effort="low")["chat_template_kwargs"]["reasoning_effort"] == "low"
    assert client.build_body(messages, think=False)["chat_template_kwargs"] == {"enable_thinking": False}
    assert client.build_body(messages, chat_template_kwargs={"enable_thinking": False})["chat_template_kwargs"] == {"enable_thinking": False}
    generic = Client("http://127.0.0.1:1", model="Qwen3.8-27B")
    assert "chat_template_kwargs" not in generic.build_body(messages)