"""Device selection remains bound to its certificate and exact current pool."""

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
