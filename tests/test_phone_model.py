"""A phone's model in the pool: off until switched on, given requests only through its own poll, and its answers passed on as text.
What stands in for the phone's model here is marked as not a model: only the plumbing is under test."""

from __future__ import annotations

import io
import json
import threading

import pytest
from phone_kit import linked

from poolhouse.fleet.phone import relay
from poolhouse.fleet.phone.errors import ApiError
from poolhouse.fleet.phone.model import MOST_QUEUED, OFFERED_S, PhoneModels
from poolhouse.phone_mailbox import Store
from poolhouse.testing.canned import marked

MODEL = {"id": "gemma-test", "name": "Gemma", "runtime": "litert-lm", "context": 32768, "max_output": 64}
ASK = {"messages": [{"role": "user", "content": "say hi"}]}


@pytest.fixture
def world(tmp_path):
    store = Store(tmp_path / "phones")
    key = linked(store)
    at = [100.0]
    return PhoneModels(store, clock=lambda: at[0]), key.phone, at


def _poll(models, phone, **more):
    return models.next(phone, {"model": MODEL, "wait_s": 0, **more})


def test_a_phone_that_is_off_is_sent_nothing_and_offers_nothing(world):
    models, phone, _at = world
    assert _poll(models, phone) == {"request": None, "enabled": False}
    assert models.state(phone) == {"enabled": False, "offering": False, "model": None, "blocked": "", "queued": 0}
    with pytest.raises(ApiError, match="not switched"):
        models.ask(phone, ASK, 40)


def test_a_request_goes_to_the_phone_and_its_answer_comes_back(world):
    models, phone, _at = world
    models.switch(phone, True)
    assert _poll(models, phone) == {"request": None, "enabled": True}
    work = models.ask(phone, {**ASK, "max_tokens": 5000, "extra": "dropped"}, 60)
    given = _poll(models, phone)["request"]
    assert given["id"] == work.id and given["max_tokens"] == 64 and "extra" not in given
    assert given["messages"] == ASK["messages"]
    assert models.post(phone, work.id, {"seq": 0, "text": marked("Hel")}) == {"continue": True}
    with pytest.raises(ApiError, match="seq 1"):
        models.post(phone, work.id, {"seq": 3, "text": "x"})
    models.post(phone, work.id, {"seq": 1, "text": "lo", "done": True, "finish_reason": "stop",
                                 "tool_calls": [{"id": "1"}], "usage": {"prompt_tokens": 2, "completion_tokens": 2, "x": "y"}})
    assert "".join(models.pieces(work)) == marked("Hel") + "lo"
    assert work.finished == {"finish_reason": "stop", "usage": {"prompt_tokens": 2, "completion_tokens": 2}}
    assert work.body == {} and models.post(phone, work.id, {"seq": 2}) == {"continue": False}


def test_switching_off_cancels_what_is_in_hand_at_once(world):
    models, phone, _at = world
    models.switch(phone, True)
    _poll(models, phone)
    work = models.ask(phone, ASK, 40)
    _poll(models, phone)
    models.switch(phone, False)
    assert models.post(phone, work.id, {"seq": 0, "text": "late"}) == {"continue": False}
    assert list(models.pieces(work)) == [] and "switched out" in work.finished["error"] and work.body == {}
    assert _poll(models, phone)["enabled"] is False


def test_what_a_phone_cannot_be_asked(world):
    models, phone, at = world
    models.switch(phone, True)
    with pytest.raises(ApiError, match="offers no model"):
        models.ask(phone, ASK, 40)
    _poll(models, phone, blocked="too hot")
    with pytest.raises(ApiError, match="offers no model"):
        models.ask(phone, ASK, 40)
    _poll(models, phone)
    for bad, why in (({**ASK, "tools": [{"type": "function"}]}, "no tools"), ({"messages": []}, "messages"),
                     ({"messages": [{"role": "tool", "content": "x"}]}, "messages"), ({**ASK, "temperature": 9}, "temperature")):
        with pytest.raises(ApiError, match=why):
            models.ask(phone, bad, 40)
    with pytest.raises(ApiError, match="at most"):
        models.ask(phone, ASK, 2 << 20)
    for _ in range(MOST_QUEUED):
        models.ask(phone, ASK, 40)
    with pytest.raises(ApiError, match="all the requests"):
        models.ask(phone, ASK, 40)
    at[0] += OFFERED_S + 1
    assert models.state(phone)["offering"] is False


def test_an_unanswered_request_ends_and_a_huge_piece_is_refused(world):
    models, phone, at = world
    models.switch(phone, True)
    _poll(models, phone)
    work = models.ask(phone, ASK, 40)
    at[0] += 91
    assert list(models.pieces(work)) == [] and "in time" in work.finished["error"]
    _poll(models, phone)
    work = models.ask(phone, ASK, 40)
    _poll(models, phone)
    with pytest.raises(ApiError, match="piece"):
        models.post(phone, work.id, {"seq": 0, "text": "x" * 20_000})


class _Handler:
    """The daemon's handler as the relay writes to it (plumbing only)."""

    command = "POST"

    def __init__(self):
        self.wfile, self.sent = io.BytesIO(), []

    def _send(self, status, body):
        self.sent.append((status, body))

    def send_response(self, status):
        self.sent.append((status, None))

    def send_header(self, *_a):
        pass

    def end_headers(self):
        pass


@pytest.mark.parametrize("stream", [False, True])
def test_the_pool_gets_an_openai_answer_from_the_phone(tmp_path, stream):
    store = Store(tmp_path / "phones")
    phone = linked(store).phone
    models = PhoneModels(store)
    models.switch(phone, True)
    models.next(phone, {"model": MODEL, "wait_s": 0})

    def answering():
        request = models.next(phone, {"model": MODEL, "wait_s": 10})["request"]
        models.post(phone, request["id"], {"seq": 0, "text": marked("hi"), "done": True, "finish_reason": "stop"})

    worker = threading.Thread(target=answering)
    worker.start()
    handler = _Handler()
    service = type("Service", (), {"models": models})()
    relay.answer(handler, service, "/v1/chat/completions", f"phone:{phone}", json.dumps({**ASK, "stream": stream}).encode())
    worker.join(20)
    if stream:
        said = handler.wfile.getvalue().decode()
        assert json.dumps({"content": marked("hi")}) in said and said.endswith("data: [DONE]\n\n") and '"finish_reason": "stop"' in said
    else:
        assert handler.sent[0][0] == 200 and handler.sent[0][1]["choices"][0]["message"]["content"] == marked("hi")


def test_the_relay_answers_chat_only(tmp_path):
    handler = _Handler()
    relay.answer(handler, None, "/v1/chat/completions", "phone:ph-000000000000", b"{}")
    assert handler.sent[0][0] == 404
