"""Actual sealed TLS producer routes derive the caller, never request identity fields."""
import hashlib
from types import SimpleNamespace

import pytest
from test_studio_device_http import devices  # noqa: F401

from poolhouse.fleet import producer_member, tls
from poolhouse.fleet.studio_transport import MAX_REPLY
from poolhouse.graph.guard import MAX_BODY


def provider(opened_devices):
    opened, _ = opened_devices
    client, service, _, _, _ = opened[0]
    calls = []
    class Provider:
        def member(self, actual, action, request):
            calls.append((actual, action, request))
            return {'accepted': True, 'bytes': len(request.get('raw') or '')}
    service.ui.studio_producers = Provider()
    return client, service, calls


def test_real_sealed_tls_uses_actual_current_caller_not_record_fields(request):
    client, service, calls = provider(request.getfixturevalue('devices'))
    assert client.request('producer-offers', {}) == {'accepted': True, 'bytes': 0}
    from poolhouse.fleet.discovery import load_pool_key
    assert calls == [({'certificate': tls.local().beacon,
                       'pool': hashlib.sha256(load_pool_key(service.ui.pool_key_path)).hexdigest()}, 'producer-offers', {})]
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


def test_missing_service_and_mismatched_tls_identity_fail_closed(request, monkeypatch):
    opened, _ = request.getfixturevalue('devices')
    client = opened[0][0]
    with pytest.raises(ValueError):
        client.request('producer-offers', {})
    _, _, calls = provider(request.getfixturevalue('devices'))
    monkeypatch.setattr(producer_member, 'fingerprint_of', lambda _: 'wrong-device')
    with pytest.raises(ValueError):
        client.request('producer-offers', {})
    assert calls == []


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


def test_no_tls_channel_or_current_membership_cannot_dispatch():
    handler = SimpleNamespace(command='POST', connection=object())
    with pytest.raises(PermissionError):
        producer_member.answer(handler, SimpleNamespace(), SimpleNamespace(key=b'pool'), 'producer-offers', b'{}')


def test_only_fixed_action_has_large_request_budget(request):
    client, _, calls = provider(request.getfixturevalue('devices'))
    with pytest.raises(ValueError):
        client.request('producer-receipt', {'raw': 'x' * MAX_REPLY})
    assert calls == []
    with pytest.raises(ValueError):
        client.request('producer-arbitrary-command', {'argv': ['untrusted']})
    assert calls == []
