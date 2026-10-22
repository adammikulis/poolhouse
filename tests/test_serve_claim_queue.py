"""Native compute waits retain process ownership and never outlive cancellation."""

import json
import os
import socket
import subprocess
import sys
import threading
import time

import pytest

from poolhouse.serve import LlamaServerBackend, ServerManager, broker_holds, broker_wire, gpu
from poolhouse.serve.broker import Broker
from poolhouse.testing.canned import canned_binary

pytest_plugins = ['serve_node']
pytestmark = [pytest.mark.usefixtures('serve_leases'), pytest.mark.redteam]


@pytest.fixture
def setup(tmp_path):
    broker = Broker(ServerManager(LlamaServerBackend(binary=canned_binary(tmp_path)),
                                  state_file=tmp_path / 'servers.json'), scan=lambda: [])
    children = []
    def process():
        child = subprocess.Popen([sys.executable, '-I', '-c', 'import time;time.sleep(120)'])
        children.append(child)
        return child
    yield broker, process
    for child in children:
        child.kill()
        child.wait()


def until(predicate):
    deadline = time.monotonic() + 10
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail('The owned queue operation did not settle')
        time.sleep(.01)


def start_claim(broker, pid, outcome, **kwargs):
    def run():
        try:
            outcome['value'] = broker.claim(gpu.CLAIM, pid, {}, timeout=None, **kwargs)
        except (RuntimeError, ValueError) as error:
            outcome['error'] = str(error)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_unbounded_wait_is_fifo_and_duplicate_held_claim_uses_one_lease(setup):
    broker, process = setup
    first, second, third = process(), process(), process()
    held = broker.claim(gpu.CLAIM, first.pid, {})
    a, b = {}, {}
    ta = start_claim(broker, second.pid, a)
    until(lambda: len(broker.claim_waiters) == 1)
    tb = start_claim(broker, third.pid, b)
    until(lambda: len(broker.claim_waiters) == 2)
    assert broker.supervising() and len(broker.snapshot()['claim_waiters']) == 2
    assert broker.claim(gpu.CLAIM, first.pid, {})['lease'] == held['lease']
    assert broker.unclaim(gpu.CLAIM, first.pid)
    ta.join(10)
    assert a['value']['granted'] and not b
    assert broker.unclaim(gpu.CLAIM, second.pid)
    tb.join(10)
    assert b['value']['granted']
    assert broker.unclaim(gpu.CLAIM, third.pid)
    assert not broker.claim_waiters and not broker.supervising()


def test_dead_or_reused_waiter_cannot_inherit_a_queued_claim(setup, monkeypatch):
    broker, process = setup
    owner, waiter = process(), process()
    broker.claim(gpu.CLAIM, owner.pid, {})
    original = broker_holds.started_at
    changed = [False]
    monkeypatch.setattr(broker_holds, 'started_at', lambda pid: original(pid) + 1 if changed[0] and pid == waiter.pid else original(pid))
    outcome = {}
    thread = start_claim(broker, waiter.pid, outcome)
    until(lambda: bool(broker.claim_waiters))
    changed[0] = True
    with broker._cond:
        broker._cond.notify_all()
    thread.join(10)
    assert 'identity changed' in outcome['error']
    assert not broker.claim_waiters and broker.claims[gpu.CLAIM]['pid'] == owner.pid
    assert broker.unclaim(gpu.CLAIM, owner.pid)


def test_disappearing_owner_after_actual_table_acquire_releases_exact_lease(setup, monkeypatch):
    broker, process = setup
    waiter = process()
    connected = [True]
    acquire = broker._gpu_for
    def lost(name, pid):
        lease = acquire(name, pid)
        connected[0] = False
        return lease
    monkeypatch.setattr(broker, '_gpu_for', lost)
    with pytest.raises(RuntimeError, match='disappeared'):
        broker.claim(gpu.CLAIM, waiter.pid, {}, timeout=None, connected=lambda: connected[0])
    assert not broker.claims and not broker.claim_waiters
    assert not [lease for lease in broker.table.listing()['leases'] if lease['tag'] == 'claim.' + gpu.CLAIM]


def test_zero_and_finite_waits_keep_their_existing_refusal_behavior(setup):
    broker, process = setup
    owner, waiter = process(), process()
    broker.claim(gpu.CLAIM, owner.pid, {})
    assert not broker.claim(gpu.CLAIM, waiter.pid, {})['granted']
    began = time.monotonic()
    assert not broker.claim(gpu.CLAIM, waiter.pid, {}, timeout=.05)['granted']
    assert time.monotonic() - began >= .05
    assert not broker.claim_waiters
    assert broker.unclaim(gpu.CLAIM, owner.pid)


def test_unverifiable_process_is_refused_without_creating_a_waiter(setup, monkeypatch):
    broker, process = setup
    monkeypatch.setattr(broker_holds, 'started_at', lambda pid: None)
    with pytest.raises(RuntimeError, match='identity'):
        broker.claim(gpu.CLAIM, process().pid, {}, timeout=None)
    assert not broker.claims and not broker.claim_waiters


@pytest.fixture
def wire(setup):
    broker, process = setup
    server = broker_wire._Server(broker)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield broker, process, server
    server.shutdown()
    server.server_close()
    thread.join(10)


def request(server, pid, **fields):
    connection = socket.create_connection(server.server_address)
    connection.sendall((json.dumps({'op': 'claim', 'token': server.token, 'pid': pid,
                                    'name': gpu.CLAIM, 'info': {}, 'wait_s': None, **fields}) + '\n').encode())
    return connection


def test_wire_disconnect_cancels_waiter_without_releasing_current_holder(wire):
    broker, process, server = wire
    owner = process()
    held = broker.claim(gpu.CLAIM, owner.pid, {})
    connection = request(server, os.getpid())
    until(lambda: bool(broker.claim_waiters))
    connection.shutdown(socket.SHUT_RDWR)
    connection.close()
    until(lambda: not broker.claim_waiters)
    assert broker.claims[gpu.CLAIM]['lease'] == held['lease']
    assert broker.unclaim(gpu.CLAIM, owner.pid)


def test_wire_cannot_nominate_an_unrelated_live_process_even_with_local_token(wire):
    broker, process, server = wire
    owner = process()
    with request(server, owner.pid) as connection, connection.makefile('rb') as stream:
        reply = json.loads(stream.readline())
    assert not reply['ok'] and 'owned by a connected process' in reply['error']
    assert not broker.claim_waiters and not broker.claims


def test_wire_shutdown_refuses_queued_request_without_orphan_lease(wire):
    broker, process, server = wire
    owner = process()
    broker.claim(gpu.CLAIM, owner.pid, {})
    with request(server, os.getpid()) as connection:
        until(lambda: bool(broker.claim_waiters))
        server._cancel_waiters()
        with connection.makefile('rb') as stream:
            reply = json.loads(stream.readline())
        assert not reply['ok']
    until(lambda: not broker.claim_waiters)
    assert broker.claims[gpu.CLAIM]['pid'] == owner.pid
    assert broker.unclaim(gpu.CLAIM, owner.pid)


def test_claim_none_preserves_unbounded_wire_deadline(monkeypatch):
    captured = {}
    monkeypatch.setattr(broker_wire, 'call', lambda op, **fields: captured.update(fields) or {'granted': True})
    assert broker_wire.claim(gpu.CLAIM, {}, timeout=None)['granted']
    assert captured['timeout'] is None and captured['wait_s'] is None


def test_wire_current_process_can_wait_grant_and_repeat_without_a_second_lease(wire):
    broker, _, server = wire
    record = {"port": server.server_address[1], "token": server.token}
    body = {"op": "claim", "pid": os.getpid(), "name": gpu.CLAIM, "info": {}, "wait_s": None}
    first = broker_wire._send(record, body, timeout=None)
    second = broker_wire._send(record, body, timeout=None)
    assert first["ok"] and second["lease"] == first["lease"]
    released = broker_wire._send(record, {"op": "unclaim", "pid": os.getpid(), "name": gpu.CLAIM}, timeout=5)
    assert released["released"] and not broker.claims


def test_wire_cannot_cancel_another_process_claim(wire):
    broker, process, server = wire
    owner = process()
    held = broker.claim(gpu.CLAIM, owner.pid, {})
    record = {"port": server.server_address[1], "token": server.token}
    reply = broker_wire._send(record, {"op": "unclaim", "pid": owner.pid, "name": gpu.CLAIM}, timeout=5)
    assert not reply["ok"] and broker.claims[gpu.CLAIM]["lease"] == held["lease"]
    assert broker.unclaim(gpu.CLAIM, owner.pid)


def test_dead_waiter_is_removed_without_interrupting_the_current_owner(setup):
    broker, process = setup
    owner, waiter = process(), process()
    held = broker.claim(gpu.CLAIM, owner.pid, {})
    outcome = {}
    thread = start_claim(broker, waiter.pid, outcome)
    until(lambda: bool(broker.claim_waiters))
    waiter.kill()
    waiter.wait()
    thread.join(10)
    assert 'identity changed' in outcome['error']
    assert not broker.claim_waiters and broker.claims[gpu.CLAIM]['lease'] == held['lease']
    assert broker.unclaim(gpu.CLAIM, owner.pid)


def test_wire_unverifiable_socket_owner_is_refused_before_admission(wire, monkeypatch):
    broker, _, server = wire
    monkeypatch.setattr(broker_wire, 'owns_connection', lambda *args: False)
    with request(server, os.getpid()) as connection, connection.makefile('rb') as stream:
        reply = json.loads(stream.readline())
    assert not reply['ok'] and not broker.claim_waiters and not broker.claims
