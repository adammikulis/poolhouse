"""The attack lab's page server and collector are plain loopback servers: they are what the
attacks aim at, never a model, so nothing that asks them for a completion is answered like one."""

from __future__ import annotations

import inspect
import json

import pytest

from poolhouse.redteam.evidence import Honeypot, Pages
from poolhouse.redteam.scenarios.fleet import get, post, raw

MODEL_ROUTES = ("/health", "/props", "/v1/models", "/slots", "/metrics")


@pytest.fixture
def pages():
    server = Pages()
    yield server
    server.close()


@pytest.fixture
def honeypot():
    server = Honeypot()
    yield server
    server.close()


def test_neither_server_is_built_on_a_model_server():
    for server in (Pages, Honeypot):
        assert not [c for c in inspect.getmro(server) if c.__module__ == "poolhouse.testing.fakes"]


def test_the_page_server_answers_no_model_route(pages):
    for path in MODEL_ROUTES:
        got = raw(pages.base_url, get(path))
        assert got.status == 404 and got.body == b"not found", path


def test_the_page_server_answers_a_completion_request_with_not_found_and_keeps_it(pages):
    request = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
    got = raw(pages.base_url, post("/v1/chat/completions", request))
    assert got.status == 404 and b"choices" not in got.body
    assert pages.requests == [("POST", "/v1/chat/completions", request)]


def test_the_page_server_serves_the_pages_it_was_given(pages):
    pages.put("/a", "<p>hello</p>")
    got = raw(pages.base_url, get("/a?x=1"))
    assert got.status == 200 and got.body == b"<p>hello</p>"
    assert raw(pages.base_url, get("/b")).status == 404


def test_the_honeypot_answers_a_bare_ok_and_keeps_what_reached_it(honeypot):
    request = b'{"stolen": "KEY-1"}'
    first = raw(honeypot.base_url, post("/v1/chat/completions", request))
    second = raw(honeypot.base_url, get("/collect?k=KEY-1"))
    assert (first.status, first.body, second.status, second.body) == (200, b"ok", 200, b"ok")
    assert honeypot.hits() == [("POST", "/v1/chat/completions", request.decode()),
                               ("GET", "/collect?k=KEY-1", "")]
    honeypot.reset()
    assert honeypot.hits() == []
