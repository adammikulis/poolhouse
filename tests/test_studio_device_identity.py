"""Device selection remains bound to its certificate and exact current pool."""

from threading import RLock
from types import SimpleNamespace

import pytest

from poolhouse.fleet import studio_dispatch, tls
from poolhouse.fleet.discovery import Beacon, derive_token, mint_pool
from poolhouse.fleet.membership import fingerprint_of
from poolhouse.fleet.remote import Member
from poolhouse.fleet.studio_dispatch import DeviceDispatch


def test_same_claimed_machine_with_another_certificate_cannot_replace_bound_target(tmp_path, monkeypatch):
    keyfile = tmp_path / 'pool.key'
    membership = mint_pool('experiments', keyfile)
    original = tls.identity(tmp_path / 'original-tls', 'device-original')
    replacement = tls.identity(tmp_path / 'replacement-tls', 'device-replacement')
    def device(identity):
        beacon = Beacon('device', host='127.0.0.1', port=12345, machine='same-claimed-machine', cert=identity.beacon)
        return Member(beacon.base_url, derive_token(membership.key), beacon=beacon)
    available = [device(original)]
    monkeypatch.setattr(studio_dispatch, 'members', lambda ui: available)
    dispatcher = DeviceDispatch(SimpleNamespace(pool_key_path=keyfile), None, lambda *args: True)
    try:
        selected = {'execution': {'machine': fingerprint_of(original.beacon)}}
        snapshot = dispatcher.identity(selected)
        assert dispatcher.member(snapshot) is available[0]
        available[:] = [device(replacement)]
        with pytest.raises(PermissionError):
            dispatcher.member(snapshot)
        available[:] = [device(original)]
        with pytest.raises(PermissionError):
            dispatcher.member({**snapshot, 'pool': '0' * 64})
    finally:
        dispatcher.close()


def test_origin_callback_rechecks_current_predecessor_resource_authority(tmp_path, monkeypatch):
    keyfile = tmp_path / 'pool.key'
    membership = mint_pool('experiments', keyfile)
    identity = tls.identity(tmp_path / 'tls', 'device-target')
    beacon = Beacon('device', host='127.0.0.1', port=12345, machine='target-machine', cert=identity.beacon)
    member = Member(beacon.base_url, derive_token(membership.key), beacon=beacon)
    monkeypatch.setattr(studio_dispatch, 'members', lambda ui: [member])
    certificate = fingerprint_of(identity.beacon)
    calls, allowed = [], [True]
    def authorize(principal, node, inputs):
        calls.append(inputs)
        return allowed[0]
    dispatcher = DeviceDispatch(SimpleNamespace(pool_key_path=keyfile), None, authorize)
    try:
        snapshot = dispatcher.identity({'execution': {'machine': certificate}})
        output = {'kind': 'model', 'path': '/server-owned/base'}
        execution = {**snapshot, 'id': 'e' * 64, 'digest': 'd' * 64}
        node = {'id': 'generation'}
        run = {'state': 'running', 'principal': {'id': 'session'}, 'graph': {'nodes': [node], 'edges': [
            {'source': {'node': 'base', 'port': 'model'}, 'target': {'node': 'generation', 'port': 'model'}}]},
            'nodes': {'base': {'outputs': {'model': output}}, 'generation': {'execution': execution}}}
        dispatcher.store = SimpleNamespace(lock=RLock(), graph=SimpleNamespace(
            query=lambda query: [{'id': 'run'}], get_doc=lambda identifier: run))
        source = certificate + ':' + snapshot['pool'] + ':origin'
        request = {'dispatch_id': 'e' * 64, 'spec_digest': 'd' * 64}
        assert dispatcher.authorize_execution(source, request)
        assert calls[-1] == {'model': output}
        allowed[0] = False
        assert not dispatcher.authorize_execution(source, request)
    finally:
        dispatcher.close()
