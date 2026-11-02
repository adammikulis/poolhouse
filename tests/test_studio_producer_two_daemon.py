"""Two real TLS daemons on one Mac, each on its own real node of one pool, not physical cross-device or holder proof."""
import socket
import sys
import tempfile
import time
from pathlib import Path

import pytest
from service_kit import build, node_binary  # noqa: F401  (fixtures)
from test_fleet_ui import Serving
from test_studio_producer_flow import (
    SOURCE,
    Driver,
    api,
    events,
    runtime_app as _runtime_app,
    start,
    wait,
)

from poolhouse.fleet import producer_bootstrap, producer_control, producer_process, tls
from poolhouse.fleet.daemon import load_or_create_token
from poolhouse.fleet.producer_runtime import Runtime
from poolhouse.fleet.remote import Member
from poolhouse.fleet.service_pool import Pool

runtime_app = _runtime_app
REAL_DISCOVER = Member.discover  # the one-daemon fixture it shares replaces it with an empty list


@pytest.fixture(scope='module')
def studio_node(node_binary, tmp_path_factory):  # noqa: F811
    """Two nodes joined into one pool for a module of tests: a node allows one compute service per device, so each daemon has its own."""
    patches = pytest.MonkeyPatch()
    try:
        yield from build(node_binary, tmp_path_factory.mktemp('services'), patches, 2)
    finally:
        patches.undo()


def configure(receiver, root, nodes):
    receiver.ui.root = root / 'traind'
    receiver.ui.settings.setup_done = True
    identity = nodes.idb
    receiver.ui.studio_identity = lambda: identity
    receiver.ui.studio_endpoint = lambda: f'https://127.0.0.1:{receiver.port}'
    receiver.ui.studio_token = lambda: load_or_create_token(receiver.ui.root)
    nodes.bind(nodes.b, identity, f'https://127.0.0.1:{receiver.port}')    # the second node's service, in the same pool
    nodes.settle()
    receiver.httpd.tls = tls.member_context(identity, receiver.ui.pool)
    secret = receiver.ui.root / 'app-identity'
    secret.write_text('cd' * 32)
    secret.chmod(0o600)
    session = receiver.ui.sessions.open('owner-session', 'launch-ticket')
    cookie = 'poolhouse_ui=' + session.sid
    api(receiver, cookie, '/sources')
    setup = receiver.ui.studio_webhook_setup
    setup.factory = lambda: Runtime(receiver.ui, receiver.ui.studio_streams, setup.local)
    return cookie, setup._runtime(create=True)


def launch_sdk(producer, outgoing):
    with socket.socket(socket.AF_UNIX) as channel:
        channel.connect(outgoing.server.bootstrap_path)
        nonce, deadline = 'b' * 64, time.monotonic() + 5
        producer_control.send_frame(channel, {'nonce': nonce}, deadline=deadline)
        anchor = producer_bootstrap.authenticate(producer.ui.root, nonce,
            producer_control.receive_frame(channel, deadline=deadline), channel)
    return Driver(anchor)


@pytest.fixture
def connected(runtime_app, monkeypatch, studio_node):
    producer, _producer_cookie, outgoing = runtime_app
    with tempfile.TemporaryDirectory(prefix='recv-', dir='/tmp') as temporary:
        root = Path(temporary).resolve()
        receiver = Serving(root, name='Receiving daemon', pool=Pool(studio_node.b.state),
                             service_identity=lambda: studio_node.idb)
        incoming, driver = None, None
        try:
            cookie, incoming = configure(receiver, root, studio_node)
            # Each daemon lists the other through its own node, and every request crosses actual TLS listeners.
            monkeypatch.setattr(Member, 'discover', REAL_DISCOVER)
            monkeypatch.setattr(producer_process, '_command', lambda fd: [sys.executable, '-I', '-B', '-c',
                f'import sys;sys.path.insert(0,{SOURCE!r});from poolhouse.fleet.producer_process import child;child({fd})'])
            driver = launch_sdk(producer, outgoing)
            receiver_id = incoming.devices._receiver(incoming.devices._self())['id']
            assert receiver_id in {item['id'] for item in driver.call('receivers')}
            offer = driver.call('offer', receiver_id, 'Separate software publisher')
            inventory = api(receiver, cookie, '/sources')
            provider = next(item for item in inventory['providers'] if item['id'] == 'software-producer')
            assert any(item['id'] == offer['id'] for item in provider['offers'])
            source = api(receiver, cookie, '/sources', {'kind': 'webhook', 'provider': 'software-producer',
                'label': 'Connected software records', 'producer': {'id': offer['id'], 'revision': offer['revision']},
                'idempotency_key': 'connect-source'})['source']
            assert source['available'], source
            assert incoming.host.rows == {} and len(outgoing.host.rows) == 1
            assert outgoing.loaded_identity.beacon != incoming.loaded_identity.beacon
            yield (receiver, cookie, incoming, driver, offer, source), outgoing
        finally:
            failing, clean = sys.exc_info()[0] is not None, True
            try:
                if driver is not None:
                    clean = driver.close()
            finally:
                try:
                    owned = [row['owned'] for row in outgoing.host.rows.values()]
                    outgoing.close()
                    assert all(owner.process.poll() is not None for owner in owned)
                finally:
                    try:
                        if incoming is not None:
                            incoming.close()
                        if getattr(receiver.ui, 'studio', None) is not None:
                            receiver.ui.studio.close()
                            receiver.ui.studio.store.close()
                    finally:
                        receiver.close()
            if not failing:
                assert clean, 'The separate SDK did not exit normally'


def test_generic_json_crosses_actual_connected_daemons_and_receipt_never_resends(connected, monkeypatch):
    flow, outgoing = connected
    _app, _cookie, incoming, driver, offer, _source = flow
    assert outgoing.service.store.indexed('subscription', outgoing.context['scope']) == []
    sub = start(flow)
    sends, original = [], incoming.local._send

    def counted(*args):
        sends.append(args[0]['event_id'])
        return original(*args)

    monkeypatch.setattr(incoming.local, '_send', counted)
    record = {'question': 'What is covered?', 'retrieved_sources': [{'text': 'Local test policy.'}], 'number': 17}
    accepted = driver.call('publish', offer['id'], 'connected-record', record)
    assert accepted['state'] == 'done'
    assert driver.call('receipt', offer['id'], 'connected-record')['receipt'] == accepted['receipt']
    page = wait(lambda: (rows if rows and rows[0]['state'] == 'done' else None) if (rows := events(flow, sub)) else None)
    assert len(page) == 1 and page[0]['result']['outputs'] == [{'node': 'record', 'port': 'record', 'value': record}]
    assert sends == ['connected-record']
    assert outgoing.service.store.indexed('subscription', outgoing.context['scope']) == []
