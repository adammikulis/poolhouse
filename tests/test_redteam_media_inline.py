"""Pictures, sounds and videos through the model bridge and the daemon's relay: each is sent as its own bytes, and no caller makes a model server fetch an
address. The upstream here is a plumbing server that is NOT a model: it keeps what it is sent, says in every reply that it is canned, and stands in for the
one thing of llama-server's media loader under attack, which is to fetch every address it is handed. A canary listener counts what reaches it.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import threading
import urllib.error
import urllib.request

import pytest

from poolhouse.fleet.serving import Serving
from poolhouse.http import MOST_BODY, build_request
from poolhouse.isolation import Lease, ModelBridge
from tests.test_fleet_serving import Running
from tests.test_redteam_isolation_bridge import (
    ALIAS,
    BODY,
    KEY,
    LEASE,
    Upstream,
    asked,
    status_of,
    talk,
)


class Canary:
    """A listener nothing may reach; ``hits`` counts the connections it got."""

    def __init__(self) -> None:
        self.hits = 0
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}/stolen"
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                connection, _ = self.sock.accept()
            except OSError:
                return
            self.hits += 1
            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            connection.close()


class Fetching(Upstream):
    """NOT a model server: it fetches each address under ``self.canary`` that a request hands it, before it answers."""

    def __init__(self, canary: Canary) -> None:
        super().__init__()
        self.requests = Loading(canary)

    def posted(self) -> list[bytes]:
        return [each for each in self.requests if each.startswith(b"POST ")]


class Loading(list):
    """The requests a `Fetching` server kept, each fetched from as it arrives."""

    def __init__(self, canary: Canary) -> None:
        super().__init__()
        self.canary = canary

    def append(self, request: bytes) -> None:
        if self.canary.url.encode() in request:
            with socket.create_connection(("127.0.0.1", self.canary.port), timeout=5) as sock:
                sock.recv(4096)   # the canary counts the connection before it answers
        super().append(request)


@pytest.fixture
def canary():
    made = Canary()
    yield made
    made.sock.close()


@pytest.fixture
def fetching(canary):
    server = Fetching(canary)
    yield server
    server.close()


@pytest.fixture
def bridged(fetching):
    made = ModelBridge(fetching.base_url, LEASE, key=KEY, idle=5.0)
    made.start()
    yield made
    made.stop()


class Pointed(Serving):
    """The registry of a daemon whose one model server is the plumbing server on ``port``."""

    def __init__(self, path, port: int) -> None:
        super().__init__(path)
        self.port = port

    def port_for(self, model: str = "") -> int:
        return self.port


@pytest.fixture
def relayed(tmp_path, fetching):
    daemon = Running(tmp_path, Pointed(tmp_path / "serving.json", fetching.port))
    yield daemon
    daemon.close()


def user(part: dict) -> dict:
    return {"messages": [{"role": "user", "content": [part]}]}


def tool_result(part: dict) -> dict:
    return {"messages": [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": [part]}]}]}


def output(part: dict) -> dict:
    return {"input": [{"type": "function_call_output", "call_id": "c", "output": [part]}]}


def item(part: dict) -> dict:
    return {"input": [{"role": "user", "content": [part]}]}


CHAT, MESSAGES, RESPONSES = "/v1/chat/completions", "/v1/messages", "/v1/responses"
SHAPES = {
    "chat image_url": (CHAT, lambda at: user({"type": "image_url", "image_url": {"url": at}})),
    "chat input_audio data": (CHAT, lambda at: user({"type": "input_audio", "input_audio": {"data": at, "format": "wav"}})),
    "chat input_audio url": (CHAT, lambda at: user({"type": "input_audio", "input_audio": {"url": at}})),
    "chat input_video data": (CHAT, lambda at: user({"type": "input_video", "input_video": {"data": at}})),
    "chat input_video url": (CHAT, lambda at: user({"type": "input_video", "input_video": {"url": at}})),
    "chat video_url url": (CHAT, lambda at: user({"type": "video_url", "video_url": {"url": at}})),
    "chat video_url data": (CHAT, lambda at: user({"type": "video_url", "video_url": {"data": at}})),
    "chat tool message": (CHAT, lambda at: {"messages": [{"role": "tool", "tool_call_id": "c", "content": [
        {"type": "input_audio", "input_audio": {"data": at}}]}]}),
    "chat data wins over url": (CHAT, lambda at: user({"type": "input_audio", "input_audio": {"data": at, "url": "data:audio/wav;base64,AAAA"}})),
    "messages image source": (MESSAGES, lambda at: user({"type": "image", "source": {"type": "url", "url": at}})),
    "messages tool result image": (MESSAGES, lambda at: tool_result({"type": "image", "source": {"type": "url", "url": at}})),
    "messages tool result audio": (MESSAGES, lambda at: tool_result({"type": "input_audio", "input_audio": {"data": at}})),
    "messages input_video": (MESSAGES, lambda at: user({"type": "input_video", "input_video": {"data": at}})),
    "responses input_image": (RESPONSES, lambda at: item({"type": "input_image", "image_url": at})),
    "responses input_audio": (RESPONSES, lambda at: item({"type": "input_audio", "input_audio": {"data": at}})),
    "responses tool output image": (RESPONSES, lambda at: output({"type": "input_image", "image_url": at})),
    "responses tool output video": (RESPONSES, lambda at: output({"type": "input_video", "input_video": {"url": at}})),
}


@pytest.mark.parametrize("shape", SHAPES)
def test_no_media_shape_makes_the_server_behind_the_bridge_fetch_an_address(bridged, fetching, canary, shape):
    route, body = SHAPES[shape]
    answer = talk(bridged.port, asked("POST", route, bridged.token, json.dumps({"model": ALIAS, **body(canary.url)}).encode()))
    assert (canary.hits, status_of(answer), fetching.posted()) == (0, 400, []), answer[:300]


@pytest.mark.parametrize("shape", SHAPES)
def test_no_media_shape_makes_the_server_behind_the_relay_fetch_an_address(relayed, fetching, canary, shape):
    route, body = SHAPES[shape]
    try:
        status = relayed.post(f"/infer{route}", body(canary.url)).status
    except urllib.error.HTTPError as refused:
        status = refused.code
        assert b"data: URL" in refused.read()
    assert (canary.hits, status, fetching.posted()) == (0, 400, [])


@pytest.mark.parametrize("route", ["/v1/embeddings", "/embeddings", "/apply-template", "/v1/chat/completions/input_tokens"])
def test_the_relays_other_routes_that_load_media_fetch_no_address_either(relayed, fetching, canary, route):
    part = {"type": "image_url", "image_url": {"url": canary.url}}
    body = {"input": [{"content": [part]}]} if "embeddings" in route else user(part)
    with pytest.raises(urllib.error.HTTPError) as refused:
        relayed.post(f"/infer{route}", body)
    assert (canary.hits, refused.value.code, fetching.posted()) == (0, 400, [])


def picture(size: int) -> str:
    return "data:image/png;base64," + base64.b64encode(os.urandom(size)).decode()


def test_a_picture_of_a_few_megabytes_passes_bridge_and_relay_and_the_text_beside_it_keeps_its_own_limit(bridged, relayed, fetching):
    shot, wave = picture(3 << 20), base64.b64encode(b"RIFF" + os.urandom(1 << 20)).decode()
    parts = [{"type": "image_url", "image_url": {"url": shot}}, {"type": "input_audio", "input_audio": {"data": wave, "format": "wav"}}]
    body = {"model": ALIAS, "messages": [{"role": "user", "content": parts}]}
    answer = talk(bridged.port, asked("POST", CHAT, bridged.token, json.dumps(body).encode()), patience=20)
    assert status_of(answer) == 200, answer[:300]
    assert relayed.post(f"/infer{CHAT}", body).status == 200
    for sent in fetching.posted():
        assert json.loads(sent.partition(b"\r\n\r\n")[2])["messages"] == body["messages"]
    assert len(fetching.posted()) == 2
    screenshot = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": shot.partition(",")[2]}}
    answer = talk(bridged.port, asked("POST", MESSAGES, bridged.token, json.dumps({"model": ALIAS, **tool_result(screenshot)}).encode()), patience=20)
    assert status_of(answer) == 200, answer[:300]
    wordy = {"model": ALIAS, "messages": [{"role": "user", "content": [parts[0], {"type": "text", "text": "x" * (LEASE.window * 4)}]}]}
    answer = talk(bridged.port, asked("POST", CHAT, bridged.token, json.dumps(wordy).encode()), patience=20)
    assert status_of(answer) == 413 and len(fetching.posted()) == 3, answer[:300]
    most = LEASE.window * 4 + MOST_BODY
    head = asked("POST", CHAT, bridged.token).replace(b"Content-Length: 0", b"Content-Length: " + str(most + 1).encode())
    assert status_of(talk(bridged.port, head)) == 413


def test_a_request_with_a_picture_is_read_only_with_a_slot_and_a_busy_model_still_answers_429():
    hold = threading.Event()
    held = Upstream(hold)
    made = ModelBridge(held.base_url, Lease(ALIAS, 4096, 1), key=KEY, idle=3.0)
    made.start()
    try:
        first = threading.Thread(target=talk, args=(made.port, asked("POST", MESSAGES, made.token, BODY)), kwargs={"patience": 20}, daemon=True)
        first.start()
        assert held.entered.wait(5)
        body = json.dumps({"model": ALIAS, **user({"type": "image_url", "image_url": {"url": picture(2 << 20)}})}).encode()
        answer = talk(made.port, asked("POST", CHAT, made.token, body), patience=20)
        assert status_of(answer) == 429 and b"Retry-After:" in answer and len(held.requests) == 1, answer[:300]
    finally:
        hold.set()
        first.join(10)
        made.stop()
        held.close()


def raw_post(daemon, path: str, body: bytes, headers: dict[str, str]) -> int:
    request = build_request(f"http://127.0.0.1:{daemon.port}{path}", data=body, method="POST", headers=headers, token=daemon.token)
    try:
        return urllib.request.urlopen(request, timeout=30).status   # noqa: S310 (a loopback address of this test, and a body no helper would send)
    except urllib.error.HTTPError as refused:
        return refused.code


def test_the_relay_sends_on_the_body_it_read_and_passes_an_audio_form_as_it_came(relayed, fetching, canary):
    kind = {"Content-Type": "application/json"}
    twice = f'{{"messages":[{{"role":"user","content":[{{"type":"image_url","image_url":{{"url":"{canary.url}","url":"data:,"}}}}]}}]}}'
    assert raw_post(relayed, f"/infer{CHAT}", twice.encode(), kind) == 400
    assert raw_post(relayed, f"/infer{CHAT}", b"not json", kind) == 400
    form = b'--b\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n\r\nRIFF\x00\xff\r\n--b--\r\n'
    assert raw_post(relayed, f"/infer{CHAT}", form, {"Content-Type": "multipart/form-data; boundary=b"}) == 400
    assert fetching.posted() == [] and canary.hits == 0
    assert raw_post(relayed, "/infer/v1/audio/transcriptions", form, {"Content-Type": "multipart/form-data; boundary=b"}) == 200
    assert fetching.posted()[0].partition(b"\r\n\r\n")[2] == form
    assert raw_post(relayed, f"/infer{CHAT}", b'{"messages": []}', {**kind, "Content-Encoding": "gzip"}) == 200
    assert b"content-encoding" not in fetching.posted()[1].partition(b"\r\n\r\n")[0].lower()
