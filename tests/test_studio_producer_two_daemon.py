"""Two real TLS daemons on one Mac, not physical cross-device or holder proof."""
import socket
import sys
import tempfile
import time
from pathlib import Path

import pytest
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
from poolhouse.fleet.discovery import adopt, load_pool_key, memberships
from poolhouse.fleet.membership import Roster
from poolhouse.fleet.pool_roster import Pool
from poolhouse.fleet.producer_runtime import Runtime
from poolhouse.fleet.remote import Member

runtime_app = _runtime_app


def configure(receiver, root, producer):
    receiver.ui.root = root / 'traind'
    receiver.ui.settings.setup_done = True
    identity = tls.identity(root / 'tls', 'Receiving daemon')
    receiver.ui.studio_identity = lambda: identity
    receiver.ui.studio_endpoint = lambda: f'https://127.0.0.1:{receiver.port}'
    adopt(memberships(producer.keyfile)[0], receiver.keyfile)
    Roster(load_pool_key(receiver.keyfile)).enrol(identity.beacon, 'Receiving daemon', 'test')
    receiver.httpd.tls = tls.member_context(identity, Pool(receiver.keyfile))
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
def connected(runtime_app, monkeypatch):
    producer, _producer_cookie, outgoing = runtime_app
    with tempfile.TemporaryDirectory(prefix='recv-', dir='/tmp') as temporary:
        root = Path(temporary).resolve()
        receiver = Serving(root, name='Receiving daemon')
        incoming, driver = None, None
        try:
            cookie, incoming = configure(receiver, root, producer)
            members = [item.devices._self(item.devices._current()) for item in (outgoing, incoming)]
            # Only observational discovery is supplied; all requests cross actual TLS listeners.
            monkeypatch.setattr(Member, 'discover', lambda **kwargs: members)
            monkeypatch.setattr(producer_process, '_command', lambda fd: [sys.executable, '-I', '-B', '-c',
                f'import sys;sys.path.insert(0,{SOURCE!r});from poolhouse.fleet.producer_process import child;child({fd})'])
            driver = launch_sdk(producer, outgoing)
            receiver_id = incoming.devices._receiver(members[1])['id']
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
