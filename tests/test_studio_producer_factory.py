"""The actual Start route preflights software availability outside durable admission."""
import socket
import tempfile
import time
from pathlib import Path

import pytest
from test_fleet_ui import Serving
from test_studio_stream_http import create, served as _served

from poolhouse import home
from poolhouse.fleet import producer_bootstrap, tls
from poolhouse.fleet.discovery import mint_pool
from poolhouse.fleet.membership import Roster
from poolhouse.fleet.pool_roster import Pool
from poolhouse.fleet.producer_client import ProducerClient
from poolhouse.fleet.producer_control import receive_frame, send_frame
from poolhouse.fleet.producer_runtime import Runtime
from poolhouse.fleet.remote import Member

served = _served


def test_start_availability_runs_without_store_lock_and_changed_source_prevents_prepare(served, monkeypatch):
    app, cookie, _ = served
    sub = create(app, cookie)
    service = app.ui.studio_streams
    scope = service.authority._context()['scope']
    source = service.store.create('source', {'id': 'fixture-owner', 'scope': scope},
        {'kind': 'webhook', 'provider': 'software-producer', 'generation': 'a' * 32}, 'source')
    actual = service.store.get('subscription', sub['id'], scope)
    actual['source_id'] = source['id']
    service.store.put_subscription(actual)
    checks = []

    def changed(selected):
        assert not service.store.lock._is_owned()
        checks.append(selected['id'])
        service.store.store.put('source', {**selected, 'generation': 'b' * 32})
        return True

    def forbidden(*args):
        raise AssertionError('A changed source must fail before dependency or model preparation.')

    monkeypatch.setattr(service.webhook_setup, 'preflight', changed)
    monkeypatch.setattr(app.ui.studio, 'prepare', forbidden)
    status, body, _ = app.call('/ui/studio/subscriptions/' + sub['id'] + '/start', method='POST', cookie=cookie,
        body={'revision': 1, 'idempotency_key': 'start-after-source-change'})
    assert status == 409, body
    assert checks == [source['id']]
    assert not service.store.get('subscription', sub['id'], scope).get('grant')



@pytest.fixture
def runtime_app(monkeypatch):
    # A short private test root keeps real Unix socket paths within the kernel limit.
    with tempfile.TemporaryDirectory(prefix='prod-', dir='/tmp') as temporary:
        root = Path(temporary).resolve()
        app = Serving(root)
        app.ui.root = root / 'traind'
        app.ui.settings.setup_done = True
        identity = tls.identity(root / 'tls', 'studio')
        app.ui.studio_identity = lambda: identity
        app.ui.studio_endpoint = lambda: f'https://127.0.0.1:{app.port}'
        member = mint_pool('producer-tests', app.keyfile)
        Roster(member.key).enrol(identity.beacon, 'Actual test device', 'test')
        app.httpd.tls = tls.member_context(identity, Pool(app.keyfile))
        machine = home.state('machine-id')
        machine.parent.mkdir(parents=True, exist_ok=True)
        machine.write_text('a' * 16)
        secret = app.ui.root / 'app-identity'
        secret.write_text('ab' * 32)
        secret.chmod(0o600)
        monkeypatch.setattr(Member, 'discover', lambda **kwargs: [])  # Observational LAN discovery only.
        session = app.ui.sessions.open('owner-session', 'launch-ticket')
        cookie = 'poolhouse_ui=' + session.sid
        try:
            status, body, _ = app.call('/ui/studio/sources', cookie=cookie)
            assert status == 200, body
            facade = app.ui.studio_webhook_setup
            facade.factory = lambda: Runtime(app.ui, app.ui.studio_streams, facade.local)
            yield app, cookie, facade._runtime(create=True)
        finally:
            if getattr(app.ui, 'studio', None):
                app.ui.studio.close()
                app.ui.studio.store.close()
            app.close()


def test_runtime_retains_actual_control_and_sdk_offer_without_grant_or_child(runtime_app):
    app, _, runtime = runtime_app
    nonce = '1' * 64
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.connect(runtime.server.bootstrap_path)
        deadline = time.monotonic() + 5
        send_frame(channel, {'nonce': nonce}, deadline=deadline)
        anchor = producer_bootstrap.authenticate(app.ui.root, nonce,
            receive_frame(channel, deadline=deadline), channel)
    client = ProducerClient.__new__(ProducerClient)
    client._initialize(anchor)
    receivers = client.receivers()
    assert len(receivers) == 1
    offer = client.offer(receivers[0]['id'], 'Actual JSON process')
    assert client.status(offer['id']) == {'offer': offer, 'channel': None}
    provider = runtime.providers()
    assert provider['offers'][0]['id'] == offer['id']
    assert runtime.host.rows == {}
    assert app.ui.studio_streams.store.indexed('subscription', runtime.context['scope']) == []
    locator = app.ui.root / 'producer-control.json'
    assert locator.exists()
    app.ui.studio_webhook_setup.close()
    assert not locator.exists()
