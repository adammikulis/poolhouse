"""Shared-state batches through an isolated loopback System One service."""

from __future__ import annotations

import io
import json

import pytest

from poolhouse.decide.systemone import MAX_BYTES, SystemOne, loads
from poolhouse.decide.types import DecideError
from poolhouse.decide_cli import main

MODEL = "test-decider@pinned"
QUESTIONS = {
    "item-1": {"type": "choice", "instructions": "Which action for item-1?",
               "criteria": {"keep": "Keep", "drop": "Drop"}},
    "item-2": {"type": "choice", "instructions": "Which action for item-2?",
               "criteria": {"keep": "Keep", "drop": "Drop"}},
}


def reply():
    return {"model": MODEL, "answers": {name: {
        "type": "choice", "choice": "keep", "probabilities": {"keep": .8, "drop": .2},
        "confidence": .6} for name in QUESTIONS}, "usage": {"input_tokens": 15, "output_tokens": 2}}


def service(server, value=None):
    seen = []

    def handle(method, path, body):
        seen.append((method, path, json.loads(body)))
        got = reply() if value is None else value
        return 200, got if isinstance(got, bytes) else json.dumps(got).encode()

    return server(handle).base_url, seen


def test_two_questions_use_one_post_and_one_shared_state(server, monkeypatch):
    url, seen = service(server)
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    got = SystemOne(url, MODEL).ask({"items": [1, 2]}, QUESTIONS)
    assert got == reply()
    assert seen == [("POST", "/v1/systemone", {
        "model": MODEL, "state": {"items": [1, 2]}, "questions": QUESTIONS})]


@pytest.mark.parametrize("url", ["http://example.invalid:12", "http://localhost:12",
    "http://127.0.0.1", "http://127.0.0.1:12/path", "http://u:p@127.0.0.1:12",
    "http://127.0.0.1:12?token=secret", "http://127.0.0.1:12#x", "file:///tmp/model"])
def test_nonliteral_or_ambiguous_endpoints_are_refused(url):
    with pytest.raises(DecideError):
        SystemOne(url, MODEL)


@pytest.mark.parametrize("timeout", [0, -1, 61, float("nan"), True])
def test_invalid_timeouts_are_refused(timeout):
    with pytest.raises(DecideError):
        SystemOne("http://127.0.0.1:12", MODEL, timeout)


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(model="wrong"),
    lambda r: r.update(tool_call={"command": "bad"}),
    lambda r: r["answers"]["item-1"].update(confidence=1),
    lambda r: r["answers"]["item-1"].update(approved=True),
    lambda r: r["answers"].pop("item-1"),
    lambda r: r["answers"].update(extra=r["answers"]["item-1"]),
    lambda r: r["answers"]["item-1"].update(type="noul"),
    lambda r: r["answers"]["item-1"].update(choice="drop"),
    lambda r: r["answers"]["item-1"].update(choice=[]),
    lambda r: r["answers"]["item-1"].update(confidence=10 ** 1000),
    lambda r: r["answers"]["item-1"].update(confidence=True),
    lambda r: r["answers"]["item-1"].update(probabilities={"keep": .2, "drop": .2}),
    lambda r: r["answers"]["item-1"].update(probabilities={"keep": 1.1, "drop": -.1}),
    lambda r: r["answers"]["item-1"].update(probabilities={"keep": .8, "extra": .2}),
])
def test_invalid_answer_batches_fail_once_without_serial_retry(server, mutate):
    value = reply()
    mutate(value)
    url, seen = service(server, value)
    with pytest.raises(DecideError):
        SystemOne(url, MODEL).ask({}, QUESTIONS)
    assert len(seen) == 1


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"nested":{"x":1,"x":2}}', b'{"x":NaN}', b'{"x":1e999}',
                                     b'[]', b'x' * (MAX_BYTES + 1)])
def test_strict_json_rejects_ambiguity_nonfinite_and_oversize(raw):
    with pytest.raises(DecideError):
        loads(raw)


def test_oversized_response_is_refused(server):
    url, seen = service(server, b' ' * (MAX_BYTES + 1))
    with pytest.raises(DecideError):
        SystemOne(url, MODEL).ask({}, QUESTIONS)
    assert len(seen) == 1


@pytest.mark.parametrize("state,questions", [
    ({"x": float("nan")}, QUESTIONS), ({}, {}), ({}, {"x": {"type": "tool"}}),
    ({}, {"x": {"type": "choice", "instructions": "q", "criteria": {"a": "one"}}}),
    ({}, {"x": {"type": "noul", "instructions": "q", "url": "http://remote"}}),
    ({}, {"x": {"type": "score", "instructions": "q", "criteria": [{}, []]}}),
])
def test_invalid_input_never_reaches_server(server, state, questions):
    url, seen = service(server)
    with pytest.raises(DecideError):
        SystemOne(url, MODEL).ask(state, questions)
    assert seen == []


def test_noul_and_score_are_validated(server):
    questions = {"yes": {"type": "noul", "instructions": "Is this ready?"},
                 "level": {"type": "score", "instructions": "How ready?",
                           "criteria": ["not ready", "ready"]}}
    value = {"model": MODEL, "answers": {"yes": {"type": "noul", "noul": .75},
        "level": {"type": "score", "score": .8,
                  "probabilities": {"0": .2, "1": .8}, "confidence": .6}}}
    url, seen = service(server, value)
    assert SystemOne(url, MODEL).ask({}, questions) == value
    assert len(seen) == 1


def test_redirect_guard_refuses_self_and_remote_without_a_second_post(monkeypatch):
    from poolhouse.decide import systemone

    def redirected(url, **kwargs):
        guard = kwargs["options"].guard
        assert guard(url) == url
        with pytest.raises(DecideError):
            guard(url)
        guard("https://example.invalid/steal")

    monkeypatch.setattr(systemone, "open_stream", redirected)
    with pytest.raises(DecideError, match="redirects"):
        SystemOne("http://127.0.0.1:12", MODEL).ask({}, QUESTIONS)


def test_cli_reads_one_json_batch_from_stdin(server, monkeypatch, capsys):
    url, seen = service(server)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"state": {}, "questions": QUESTIONS})))
    assert main(["batch", "--url", url, "--model", MODEL, "--request", "-"]) == 0
    assert json.loads(capsys.readouterr().out) == reply()
    assert len(seen) == 1


def test_cli_errors_never_echo_state_or_server_body(server, monkeypatch, capsys):
    url, seen = service(server, b"private-response-secret")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"state": "private-state-secret",
                                                           "questions": QUESTIONS})))
    assert main(["batch", "--url", url, "--model", MODEL]) == 2
    captured = capsys.readouterr()
    assert "private" not in captured.err and not captured.out
    assert len(seen) == 1


def test_server_rounded_probabilities_are_preserved(server):
    value = reply()
    for answer in value["answers"].values():
        answer.update(probabilities={"keep": .6666, "drop": .3333}, confidence=.3333)
    url, _seen = service(server, value)
    assert SystemOne(url, MODEL).ask({}, QUESTIONS) == value


@pytest.mark.parametrize("answer", [
    {"type": "noul", "noul": True},
    {"type": "score", "score": 0, "probabilities": {"0": .2, "1": .8}, "confidence": .6},
    {"type": "score", "score": .8, "probabilities": {"0": .2, "1": .8},
     "confidence": .6, "legend": {"0": "swapped", "1": "ready"}},
])
def test_malformed_typed_answers_are_rejected(server, answer):
    question = ({"type": "noul", "instructions": "q"} if answer["type"] == "noul" else
                {"type": "score", "instructions": "q", "criteria": ["not ready", "ready"]})
    url, seen = service(server, {"model": MODEL, "answers": {"q": answer}})
    with pytest.raises(DecideError):
        SystemOne(url, MODEL).ask({}, {"q": question})
    assert len(seen) == 1


def test_shared_json_object_hook_preserves_nested_objects():
    from poolhouse.json_objects import unique_object

    assert json.loads('{"outer":{"item":1},"items":[{"item":2}]}',
                      object_pairs_hook=unique_object) == {"outer": {"item": 1}, "items": [{"item": 2}]}
