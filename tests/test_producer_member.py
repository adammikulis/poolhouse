"""Actual sealed TLS producer routes derive the caller, never request identity fields."""
import hashlib
import socket
import ssl
import threading
from types import SimpleNamespace

import pytest
from service_kit import node_binary, three_nodes  # noqa: F401  (fixtures the daemons below need)
from test_studio_device_http import devices  # noqa: F401  (a fixture)

from poolhouse.fleet import producer_member, studio_pool, tls
from poolhouse.fleet.studio_transport import MAX_REPLY
from poolhouse.graph.guard import MAX_BODY


def provider(opened_devices):
    opened, _ = opened_devices
    client, service = opened[0].transport, opened[0].service
    calls = []
    class Provider:
        def member(self, actual, action, request):
            calls.append((actual, action, request))
            return {'accepted': True, 'bytes': len(request.get('raw') or '')}
    service.ui.studio_producers = Provider()
    return client, service, calls


def test_real_sealed_tls_uses_actual_current_caller_not_record_fields(request):
    client, _service, calls = provider(request.getfixturevalue('devices'))
    assert client.request('producer-offers', {}) == {'accepted': True, 'bytes': 0}
    assert calls == [({'certificate': tls.local().beacon, 'pool': studio_pool.current()}, 'producer-offers', {})]
    with pytest.raises(ValueError):
        client.request('producer-offers', {'certificate': 'other-device', 'owner': True})
    assert len(calls) == 1


@pytest.mark.parametrize('raw', [b'{"revision":1,"revision":1,"offer_id":"x"}',
                               b'{"revision":NaN,"offer_id":"x"}', b'{"revision":1e999,"offer_id":"x"}'])
def test_duplicate_and_nonfinite_fields_never_dispatch_over_actual_tls(request, raw):
    client, _, calls = provider(request.getfixturevalue('devices'))
    with pytest.raises(ValueError):
        client._wire('POST', '/studio/v1/executions/producer-inspect', raw, MAX_REPLY)
    assert calls == []


def test_missing_service_fails_closed(request):
    opened, _ = request.getfixturevalue('devices')
    client = opened[0].transport
    with pytest.raises(ValueError):
        client.request('producer-offers', {})


def test_tls_caller_that_is_not_the_sealed_member_cannot_dispatch(tmp_path):
    """A real TLS 1.3 connection whose client certificate is not the member the daemon authorized."""
    server_id = tls.identity(tmp_path / 'server', 'server')
    client_id = tls.identity(tmp_path / 'client', 'client')
    serving = tls.server_context(server_id)
    serving.verify_mode = ssl.CERT_OPTIONAL
    serving.load_verify_locations(cadata=client_id.certfile.read_text())
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    accepted = {}

    def accept():
        raw, _ = listener.accept()
        accepted['channel'] = serving.wrap_socket(raw, server_side=True)

    thread = threading.Thread(target=accept)
    thread.start()
    connecting = tls.pinned_context(server_id.beacon, who=client_id)
    connected = connecting.wrap_socket(socket.create_connection(listener.getsockname()), server_hostname='127.0.0.1')
    thread.join(10)
    called = []
    ui = SimpleNamespace(studio_producers=SimpleNamespace(member=lambda *args: called.append(args) or {}))
    sent = []
    handler = SimpleNamespace(command='POST', connection=accepted['channel'], _member='0' * 64, _send=lambda *args: sent.append(args))
    with pytest.raises(PermissionError, match='do not match'):
        producer_member.answer(handler, ui, studio_pool.scope_of('a' * 32), 'producer-offers', b'{}')
    handler._member = hashlib.sha256(client_id.der).hexdigest()
    assert producer_member.answer(handler, ui, studio_pool.scope_of('a' * 32), 'producer-offers', b'{}') is True
    assert called and called[0][0]['certificate'] == client_id.beacon
    connected.close()
    accepted['channel'].close()
    listener.close()


def test_only_publisher_action_can_carry_maximum_record_over_sealed_tls(request):
    client, _, calls = provider(request.getfixturevalue('devices'))
    raw = '{"record":"' + 'x' * (MAX_BODY - 13) + '"}'
    assert len(raw.encode()) == MAX_BODY
    request = {'version': 2, 'action': 'bridge-publish', 'binding': {}, 'event_id': 'record-1',
               'raw': raw, 'wire_digest': hashlib.sha256(raw.encode()).hexdigest(), 'nonce': 'a' * 64, 'retry_key': None}
    assert client.request('producer-publish', request) == {'accepted': True, 'bytes': MAX_BODY}
    assert calls[0][2] == request
    with pytest.raises(ValueError):
        client.request('producer-receipt', request)
    assert len(calls) == 1


def test_metadata_budget_is_fixed_before_provider_dispatch(request):
    client, _, calls = provider(request.getfixturevalue('devices'))
    request = {'version': 2, 'action': 'bridge-receipt', 'binding': {'text': 'x' * 70000},
               'event_id': 'record-1', 'raw': None, 'wire_digest': None, 'nonce': 'a' * 64, 'retry_key': None}
    with pytest.raises(ValueError):
        client.request('producer-receipt', request)
    assert calls == []


def test_no_tls_channel_or_current_pool_cannot_dispatch():
    handler = SimpleNamespace(command='POST', connection=object())
    with pytest.raises(PermissionError):
        producer_member.answer(handler, SimpleNamespace(), studio_pool.scope_of('a' * 32), 'producer-offers', b'{}')
    with pytest.raises(PermissionError):
        producer_member.answer(handler, SimpleNamespace(), '', 'producer-offers', b'{}')


def test_only_fixed_action_has_large_request_budget(request):
    client, _, calls = provider(request.getfixturevalue('devices'))
    with pytest.raises(ValueError):
        client.request('producer-receipt', {'raw': 'x' * MAX_REPLY})
    assert calls == []
    with pytest.raises(ValueError):
        client.request('producer-arbitrary-command', {'argv': ['untrusted']})
    assert calls == []
