"""Guest access on a real llama.cpp server: two guests alternate, neither rereads her own conversation, neither is answered from the
other's, and a marker prompt is in no file afterwards."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from poolhouse import home, serverkeys
from poolhouse.guest import local
from poolhouse.guest.engine import PrivateServers
from poolhouse.guest.gateway import Gateway
from poolhouse.guest.store import Store
from poolhouse.http import Options, request_json
from poolhouse.serve import LlamaServerBackend, ServerManager

pytest_plugins = ["real_model"]
pytestmark = pytest.mark.real_model

MARKER = "heliotrope walrus 5521"
STORY = f"Remember the phrase {MARKER}. " + "The harbor keeps a ledger of every tide and the ships that waited on it. " * 40


def turn(guest, record, text):
    body = json.dumps({"messages": [{"role": "user", "content": text}], "max_tokens": 4, "temperature": 0}).encode()
    connection, response = local.connect(guest, record, "/v1/chat/completions", body)
    try:
        said = json.loads(response.read())
        assert response.status == 200, said
        return said["timings"]
    finally:
        connection.close()


def test_two_guests_keep_their_own_conversations_and_nothing_is_written(real, tmp_path):
    model = real.prepared("other")
    manager = ServerManager(LlamaServerBackend(binary=real.binary(), sandboxed=False))
    engine = PrivateServers(model, context=4096, cache_mb=64, manager=manager)
    host = Store()
    gateway = Gateway(engine, store=host, port=0)
    gateway.start()
    try:
        guests = []
        for name in ("one", "two"):
            computer = Store(tmp_path / name)
            on_host = host.add("guest", name, computer.identity().der)
            guests.append((computer, computer.add("host", "them", host.identity().der, (f"127.0.0.1:{gateway.port}",)), on_host))
        (a, to_a, a_there), (b, to_b, b_there) = guests
        first_a = turn(a, to_a, STORY)
        first_b = turn(b, to_b, STORY)
        again_a = turn(a, to_a, STORY)
        again_b = turn(b, to_b, STORY)
        assert first_a.get("cache_n", 0) == 0
        assert first_b.get("cache_n", 0) == 0, "the second guest matches nothing of the first's identical conversation"
        for first, again in ((first_a, again_a), (first_b, again_b)):
            assert again["cache_n"] > first["prompt_n"] // 2, "her own next turn does not read the conversation again"
        urls = {g: engine.acquire(g.id)[0] for g in (a_there, b_there)}
        assert len(set(urls.values())) == 2
        for url in urls.values():
            for path in ("/slots", "/metrics"):
                with pytest.raises(Exception):  # noqa: B017 - the server has neither route
                    request_json(url + path, method="GET", options=Options(timeout=10))
        key = serverkeys.for_url(urls[a_there])
        assert key and key != serverkeys.for_url(urls[b_there])
        host.revoke(a_there.id)
        engine.sweep(lambda guest: guest != a_there.id)
        assert engine.active() == [b_there.id]
    finally:
        gateway.close()
        manager.close()
    roots = {tmp_path, home.home(), home.cache(), Path(tempfile.gettempdir()) / "poolhouse"}
    files = [p for root in roots if root.is_dir() for p in root.rglob("*") if p.is_file() and not p.is_symlink()]
    assert files and [str(p) for p in files if MARKER.encode() in p.read_bytes()] == []
