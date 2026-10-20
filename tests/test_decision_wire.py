"""Resident scores come from the exact daemon-bound process, never a locator alone."""
import os
import threading
from types import SimpleNamespace

import pytest

from poolhouse.serve import broker_wire as wire
from poolhouse.serve.process import started_at

RECORD = {"question": "Choose", "state": "record", "options": {"a": "first", "b": "second"}}
RESULT = {"choice": "a", "scores": {"a": 0.75, "b": 0.25}, "confidence": 0.75}
REQUEST = {"permit_id": "permit", "request_id": "request", "model_digest": "d" * 64, "record": RECORD}


@pytest.fixture
def broker_socket(monkeypatch):
    monkeypatch.setattr(wire.broker_runtime, "snapshot", lambda: {})
    calls = []
    class Authorization:
        def __init__(self, owned, request, worker):
            assert owned.instance == "a" * 64
            calls.append((request, worker))
        def __call__(self, stage):
            calls.append(stage)
            return "authorized-source"
    def fixed_module(name):
        assert name == "poolhouse.serve.decision_authority"
        return SimpleNamespace(DecisionAuthorization=Authorization)
    monkeypatch.setattr(wire.optional, "module", fixed_module)
    def decide(record, request_id, owner, resolve, connected):
        assert connected() and resolve("queue") == "authorized-source"
        calls.append(owner)
        return RESULT
    broker = SimpleNamespace(_cond=threading.Condition(), close_decisions=lambda: None, decision_once=decide)
    server = wire._Server(broker, owned=SimpleNamespace(instance="a" * 64))
    running = threading.Thread(target=server.serve_forever)
    running.start()
    expected = {"pid": os.getpid(), "started": started_at(os.getpid()), "instance": "a" * 64,
                "port": server.server_address[1]}
    monkeypatch.setattr(wire, "_record", lambda: {"pid": expected["pid"], "pid_started": expected["started"],
                                                "port": expected["port"], "token": server.token})
    try:
        yield server, expected, calls
    finally:
        server.shutdown()
        server.server_close()
        running.join(2)
        assert not running.is_alive()


def test_real_private_socket_binds_broker_instance_worker_and_typed_result(broker_socket):
    _server, expected, calls = broker_socket
    assert wire.decision(REQUEST, expected) == RESULT
    assert calls[0] == (REQUEST, {"pid": os.getpid(), "started": started_at(os.getpid())})
    assert calls[-1].scope == "permit:permit" and calls[-1].pid == os.getpid()


def test_locator_cannot_replace_expected_process_or_port(broker_socket, monkeypatch):
    _server, expected, calls = broker_socket
    record = wire._record()
    monkeypatch.setattr(wire, "_record", lambda: {**record, "pid": expected["pid"] + 1})
    with pytest.raises(PermissionError, match="locator"):
        wire.decision(REQUEST, expected)
    assert calls == []


def test_socket_owner_is_verified_before_any_request_is_sent(broker_socket, monkeypatch):
    _server, expected, calls = broker_socket
    monkeypatch.setattr(wire, "owns_connection", lambda *_args: False)
    clock = iter([0, 6])
    monkeypatch.setattr(wire.time, "monotonic", lambda: next(clock))
    with pytest.raises(PermissionError, match="connection"):
        wire.decision(REQUEST, expected)
    assert calls == []


def test_instance_substitution_never_resolves_model(broker_socket):
    _server, expected, calls = broker_socket
    with pytest.raises(PermissionError, match="different broker"):
        wire.decision(REQUEST, {**expected, "instance": "b" * 64})
    assert calls == []


def test_broker_generation_changed_after_compute_withholds_scores(broker_socket, monkeypatch):
    _server, expected, calls = broker_socket
    actual_started = wire.started_at
    def observed(pid):
        if calls and hasattr(calls[-1], "scope"):
            return expected["started"] + 1
        return actual_started(pid)
    monkeypatch.setattr(wire, "started_at", observed)
    with pytest.raises(PermissionError, match="generation changed"):
        wire.decision(REQUEST, expected)
    assert calls and hasattr(calls[-1], "scope")  # A result existed but was withheld.


@pytest.mark.parametrize("extra", [{"source": "/chosen/model"}, {"url": "https://example.invalid"},
                                   {"module": "chosen.module"}])
def test_worker_record_cannot_nominate_a_source_endpoint_or_code(broker_socket, extra):
    _server, expected, calls = broker_socket
    with pytest.raises(ValueError):
        wire.decision({**REQUEST, **extra}, expected)
    assert calls == []


def test_unowned_broker_even_with_its_token_has_no_resident_authority(broker_socket):
    server, expected, calls = broker_socket
    server.owned = None
    body = {"op": "decision", "pid": os.getpid(), "token": server.token,
            "broker_instance": expected["instance"], **REQUEST}
    with pytest.raises(PermissionError, match="owned broker"):
        server.answer(body, connected=lambda: True)
    assert calls == []


def test_connected_worker_generation_is_observed_not_supplied(broker_socket):
    server, expected, calls = broker_socket
    body = {"op": "decision", "pid": os.getpid(), "token": server.token,
            "broker_instance": expected["instance"], **REQUEST}
    with pytest.raises(PermissionError, match="connected"):
        server.answer(body, connected=lambda: False)
    with pytest.raises(ValueError):
        server.answer({**body, "worker_started": 1.0}, connected=lambda: True)
    assert calls == []
