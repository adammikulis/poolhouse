"""Cancellation and explicit timeouts before HTTP response headers."""
from __future__ import annotations

import asyncio
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler

import httpx
import pytest

from poolhouse.client.sdk import FleetTransport
from poolhouse.http import Server

pytest_plugins = ['node_kit']


@contextmanager
def pending_headers():
    received, closed = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            received.set()
            self.connection.settimeout(3)
            try:
                assert self.connection.recv(1) == b''
                closed.set()
            except OSError:
                pass
        def log_message(self, *_args):
            pass
    server = Server(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1/chat/completions', received, closed
    finally:
        server.shutdown()
        server.server_close()
        worker.join(1)


def test_unlimited_sdk_opening_can_be_cancelled_before_headers():
    with pending_headers() as (url, received, closed):
        async def run():
            transport = FleetTransport(url, '')
            request = httpx.Request('POST', url, json={})
            task = asyncio.create_task(transport.handle_async_request(request))
            assert await asyncio.to_thread(received.wait, 2)
            began = time.monotonic()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            assert time.monotonic() - began < 1
            assert await asyncio.to_thread(closed.wait, 1)
            await transport.aclose()
        asyncio.run(run())


def test_explicit_sdk_opening_timeout_remains_effective():
    with pending_headers() as (url, received, closed):
        async def run():
            transport = FleetTransport(url, '', timeout=0.1)
            began = time.monotonic()
            response = await transport.handle_async_request(httpx.Request('POST', url, json={}))
            assert response.status_code == 502
            assert 0.09 <= time.monotonic() - began < 1
            assert received.is_set() and await asyncio.to_thread(closed.wait, 1)
            await transport.aclose()
        asyncio.run(run())


def test_unlimited_sdk_can_cancel_during_tls_handshake():
    import socketserver

    received, closed = threading.Event(), threading.Event()
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            try:
                assert self.request.recv(4096)
                received.set()
                while self.request.recv(4096):
                    pass
                closed.set()
            except OSError:
                pass
    with socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        async def run():
            url = f'https://127.0.0.1:{server.server_address[1]}/v1/chat/completions'
            transport = FleetTransport(url, '')
            task = asyncio.create_task(transport.handle_async_request(httpx.Request('POST', url, json={})))
            assert await asyncio.to_thread(received.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            assert await asyncio.to_thread(closed.wait, 1)
            await transport.aclose()
        try:
            asyncio.run(run())
        finally:
            server.shutdown()
            worker.join(1)


def test_cancelling_queued_request_gives_its_place_in_the_line_back(workspace_node, monkeypatch):
    from poolhouse import gate, leasetable
    from poolhouse.http import ServerError, request_json
    from poolhouse.http_cancel import Cancellation, scope

    monkeypatch.setattr(gate, 'device_of', lambda _url: 'cancel-test')
    table = leasetable.default()

    def turns():
        return [row for row in table.listing()['leases'] if row['tag'].startswith('turn.')]

    control, errors = Cancellation(), []
    def work():
        try:
            with scope(control):
                request_json('http://127.0.0.1:1/v1/chat/completions', payload={}, timeout=None)
        except ServerError as error:
            errors.append(error)
    with gate.turn('http://127.0.0.1:1/v1/chat/completions'):
        worker = threading.Thread(target=work)
        worker.start()
        deadline = time.monotonic() + 5
        while len(turns()) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert sorted(row['state'] for row in turns()) == ['held', 'queued']
        control.set()
        worker.join(5)
        assert not worker.is_alive()
        assert len(errors) == 1 and errors[0].status == 429
        assert 'cancelled while waiting' in str(errors[0])
        assert [row['state'] for row in turns()] == ['held']
    assert turns() == []


def test_cancel_during_hostname_resolution_releases_sdk_opening_thread(monkeypatch):
    import socket

    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    def blocked_resolver(*_args):
        entered.set()
        release.wait(3)
        exited.set()
        return []
    monkeypatch.setattr(socket, 'getaddrinfo', blocked_resolver)
    async def run():
        url = 'http://unresolved.invalid/v1/chat/completions'
        transport = FleetTransport(url, '')
        task = asyncio.create_task(transport.handle_async_request(httpx.Request('POST', url, json={})))
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        await transport.aclose()
    began = time.monotonic()
    try:
        asyncio.run(run())
        assert time.monotonic() - began < 1
        assert not exited.is_set()
        assert len([t for t in threading.enumerate() if t.name.startswith('poolhouse-dns-')]) <= 2
    finally:
        release.set()
        assert exited.wait(1)


def test_explicit_sdk_timeout_applies_during_hostname_resolution(monkeypatch):
    import socket

    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    def blocked_resolver(*_args):
        entered.set()
        release.wait(3)
        exited.set()
        return []
    monkeypatch.setattr(socket, 'getaddrinfo', blocked_resolver)
    async def run():
        url = 'http://unresolved.invalid/v1/chat/completions'
        transport = FleetTransport(url, '', timeout=0.1)
        began = time.monotonic()
        response = await transport.handle_async_request(httpx.Request('POST', url, json={}))
        assert response.status_code == 502
        assert 0.09 <= time.monotonic() - began < 1
        assert entered.is_set() and not exited.is_set()
        await transport.aclose()
    try:
        asyncio.run(run())
    finally:
        release.set()
        assert exited.wait(1)
