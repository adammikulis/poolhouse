"""Chat reasoning, queue status, and cancellation."""

import io
import threading
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest

from poolhouse.fleet.chat import Target, reply_parts
from poolhouse.fleet.chat_stream import Transfer, frame, release, reserve
from poolhouse.fleet.conversations import Conversations

pytest_plugins = ["node_kit"]


def test_reasoning_survives_conversation_reload(tmp_path):
    raw = frame({"choices": [{"delta": {"reasoning_content": "think"}}]})
    raw += frame({"choices": [{"delta": {"content": "answer"}}]})
    content, reasoning = reply_parts(raw)
    assert (content, reasoning) == ("answer", "think")
    store = Conversations(tmp_path)
    chat = store.start()
    store.append(chat.id, "assistant", content, reasoning=reasoning, status="cancelled")
    message = Conversations(tmp_path).get(chat.id).messages[0]
    assert (message.content, message.reasoning, message.status) == ("answer", "think", "cancelled")


def test_pending_conversation_is_reserved_once():
    store = object()
    assert reserve(store, "chat")
    assert not reserve(store, "chat")
    release(store, "chat")
    assert reserve(store, "chat")
    release(store, "chat")


def test_upstream_error_is_an_sse_error(monkeypatch):
    from poolhouse.fleet import chat_stream
    from poolhouse.fleet.chat import ChatError
    monkeypatch.setattr(chat_stream, "turn", lambda *a, **k: nullcontext())
    def fail(*args, **kwargs):
        raise ChatError("upstream failed")
    monkeypatch.setattr(chat_stream, "stream", fail)
    handler = SimpleNamespace(wfile=io.BytesIO())
    raw, status = Transfer(Target("model", "http://test/error"), {}, "chat").relay(handler)
    assert status == "failed"
    assert b'"state": "rebuilding"' in raw
    assert b'"error": {"message": "upstream failed"}' in raw
    assert raw.endswith(b"data: [DONE]\n\n")


def test_disconnect_cancels_worker_and_frees_model_slot(monkeypatch):
    from poolhouse.fleet import chat_stream
    monkeypatch.setattr(chat_stream, "turn", lambda *a, **k: nullcontext())
    stopped = threading.Event()
    def waiting(target, payload, *, control):
        control.cancelled.wait(3)
        stopped.set()
        yield b"data: [DONE]\n\n"
    monkeypatch.setattr(chat_stream, "stream", waiting)
    class Broken:
        def write(self, block):
            raise BrokenPipeError
    transfer = Transfer(Target("model", "http://test/cancel"), {}, "chat")
    _, status = transfer.relay(SimpleNamespace(wfile=Broken()))
    assert status == "cancelled"
    assert not transfer.worker.is_alive()
    assert transfer.cancelled.is_set()


@contextmanager
def hanging_chat(*, headers):
    from http.server import BaseHTTPRequestHandler

    from poolhouse.http import Server

    received, closed = threading.Event(), threading.Event()
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            if headers:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"choices":[{"index":0,"delta":{"content":"first"}}]}\n\n')
                self.wfile.flush()
            received.set()
            self.connection.settimeout(4)
            try:
                if self.connection.recv(1) == b"":
                    closed.set()
            except OSError:
                pass
        def log_message(self, *_args):
            pass
    server = Server(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/chat/completions", received, closed, requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.mark.parametrize("headers", [False, True])
@pytest.mark.parametrize("sdk", [False, True])
@pytest.mark.redteam
def test_cancel_joins_real_upstream_before_releasing_turn(monkeypatch, headers, sdk):
    from poolhouse.fleet import chat_stream, sdk_chat

    source_closed, released = threading.Event(), threading.Event()
    @contextmanager
    def owned_turn(*_args, **_kwargs):
        try:
            yield
        finally:
            assert source_closed.is_set()
            released.set()
    source = sdk_chat.stream if sdk else chat_stream.stream
    def tracked(*args, **kwargs):
        try:
            yield from source(*args, **kwargs)
        finally:
            if sdk:
                assert not any(t.name.startswith("asyncio_") for t in threading.enumerate())
            source_closed.set()
    monkeypatch.setattr(chat_stream, "turn", owned_turn)
    with hanging_chat(headers=headers) as (url, received, closed, requests):
        transfer = Transfer(Target("model", url),
                            {"model": "model", "messages": [{"role": "user", "content": "hello"}],
                             "stream": True}, "chat", source=tracked)
        transfer.worker.start()
        try:
            assert received.wait(2)
            if headers:
                # Wait until the first body has traversed the real raw/SDK transport.
                while b"first" not in transfer.events.get(timeout=2):
                    pass
            transfer.cancel()
            transfer.cancel()
            transfer.worker.join(2)
            assert not transfer.worker.is_alive()
            assert transfer.done.is_set() and released.is_set()
            assert closed.wait(1)
            assert len(requests) == 1
        finally:
            transfer.cancel()
            transfer.worker.join(3)


def test_backpressure_cancellation_closes_source_before_gate(monkeypatch):
    from poolhouse.fleet import chat_stream

    order, full = [], threading.Event()
    @contextmanager
    def owned_turn(*_args, **_kwargs):
        try:
            yield
        finally:
            order.append("gate")
    def source(*_args, control):
        try:
            for _ in range(100):
                if control.events.full():
                    full.set()
                yield b"body"
        finally:
            order.append("source")
    monkeypatch.setattr(chat_stream, "turn", owned_turn)
    transfer = Transfer(Target("model", "http://backpressure.invalid"), {}, "chat", source=source)
    transfer.worker.start()
    try:
        assert full.wait(1)
        transfer.cancel()
        transfer.worker.join(1)
        assert not transfer.worker.is_alive() and transfer.done.is_set()
        assert order == ["source", "gate"]
    finally:
        transfer.cancel()
        transfer.worker.join(2)


def test_relay_waits_for_cleanup_and_keeps_normal_tail(monkeypatch):
    from poolhouse.fleet import chat_stream

    entered, release_cleanup, returned = threading.Event(), threading.Event(), threading.Event()
    def source(*_args, control):
        try:
            yield b"first"
            yield b"tail"
        finally:
            entered.set()
            release_cleanup.wait()
    monkeypatch.setattr(chat_stream, "turn", lambda *a, **k: nullcontext())
    transfer = Transfer(Target("model", "http://cleanup.invalid"), {}, "chat", source=source)
    result = []
    def relay():
        result.append(transfer.relay(SimpleNamespace(wfile=io.BytesIO())))
        returned.set()
    caller = threading.Thread(target=relay)
    caller.start()
    try:
        assert entered.wait(1)
        assert not returned.is_set() and not transfer.done.is_set()
    finally:
        release_cleanup.set()
        caller.join(2)
    assert not caller.is_alive() and not transfer.worker.is_alive()
    assert result[0][0].endswith(b"firsttail") and result[0][1] == "complete"


@pytest.mark.parametrize("phase", ["factory", "iteration", "close"])
def test_source_exception_finishes_relay(monkeypatch, phase):
    from poolhouse.fleet import chat_stream

    monkeypatch.setattr(chat_stream, "turn", lambda *a, **k: nullcontext())
    def source(*_args, **_kwargs):
        if phase == "factory":
            raise RuntimeError("source factory failed")
        def pieces():
            try:
                yield b"body"
                if phase == "iteration":
                    raise RuntimeError("source iteration failed")
            finally:
                if phase == "close":
                    raise RuntimeError("source close failed")
        return pieces()
    transfer = Transfer(Target("model", "http://failure.invalid"), {}, "chat", source=source)
    raw, status = transfer.relay(SimpleNamespace(wfile=io.BytesIO()))
    assert status == "failed" and b'"error"' in raw
    assert transfer.done.is_set() and not transfer.worker.is_alive()


def test_cancel_before_start_never_calls_source():
    called = []
    transfer = Transfer(Target("model", "http://unused.invalid"), {}, "chat",
                        source=lambda *_a, **_k: called.append(True))
    transfer.cancel()
    transfer.cancel()
    transfer.worker.start()
    transfer.worker.join(1)
    assert not transfer.worker.is_alive() and transfer.done.is_set()
    assert called == []


@pytest.mark.redteam
def test_cancel_while_waiting_for_actual_device_turn(workspace_node, monkeypatch):
    import time

    from poolhouse import gate, leasetable

    monkeypatch.setattr(gate, "device_of", lambda _url: "transfer-cancel-test")
    table = leasetable.default()
    called = []
    transfer = Transfer(Target("model", "http://127.0.0.1:1/v1/chat/completions"), {}, "chat",
                        source=lambda *_a, **_k: called.append(True))
    def turns():
        return [row for row in table.listing()["leases"] if row["tag"].startswith("turn.")]
    with gate.turn(transfer.target.url):
        transfer.worker.start()
        try:
            deadline = time.monotonic() + 3
            while len(turns()) < 2 and time.monotonic() < deadline:
                time.sleep(0.005)
            assert sorted(row["state"] for row in turns()) == ["held", "queued"]
            transfer.cancel()
            transfer.worker.join(2)
            assert not transfer.worker.is_alive() and transfer.done.is_set()
            assert [row["state"] for row in turns()] == ["held"]
            assert called == []
        finally:
            transfer.cancel()
            transfer.worker.join(3)
    assert turns() == []


def test_cancel_while_waiting_for_same_target(monkeypatch):
    from poolhouse.fleet import chat_stream

    entered = threading.Event()
    calls = []
    def source(*_args, control):
        calls.append(control.cid)
        entered.set()
        control.cancelled.wait()
        yield b"body"
    monkeypatch.setattr(chat_stream, "turn", lambda *a, **k: nullcontext())
    target = Target("model", "http://same-target.invalid")
    first = Transfer(target, {}, "first", source=source)
    second = Transfer(target, {}, "second", source=source)
    first.worker.start()
    try:
        assert entered.wait(1)
        second.worker.start()
        second.cancel()
        second.worker.join(1)
        assert not second.worker.is_alive() and second.done.is_set()
        assert calls == ["first"]
        assert first.worker.is_alive()
    finally:
        first.cancel()
        second.cancel()
        first.worker.join(2)
        if second.worker.ident is not None:
            second.worker.join(2)


@pytest.mark.redteam
def test_disconnect_does_not_return_while_source_cleanup_is_alive(monkeypatch):
    from poolhouse.fleet import chat_stream

    producing, cleaning, release_cleanup, returned = (threading.Event() for _ in range(4))
    def source(*_args, control):
        try:
            producing.set()
            while not control.cancelled.is_set():
                yield b"body"
        finally:
            cleaning.set()
            release_cleanup.wait()
    class Broken:
        def write(self, _block):
            assert producing.wait(1)
            raise BrokenPipeError
    monkeypatch.setattr(chat_stream, "turn", lambda *a, **k: nullcontext())
    transfer = Transfer(Target("model", "http://joining.invalid"), {}, "chat", source=source)
    result = []
    def relay():
        result.append(transfer.relay(SimpleNamespace(wfile=Broken())))
        returned.set()
    caller = threading.Thread(target=relay)
    caller.start()
    try:
        assert cleaning.wait(1)
        assert not returned.wait(2.2)
        assert transfer.worker.is_alive() and not transfer.done.is_set()
    finally:
        release_cleanup.set()
        caller.join(2)
    assert not caller.is_alive() and not transfer.worker.is_alive()
    assert result[0][1] == "cancelled" and transfer.done.is_set()
