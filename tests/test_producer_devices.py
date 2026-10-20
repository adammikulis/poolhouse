"""Selected-pool discovery is observational; current roster checks own every request."""
import copy
import hashlib
import os
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_studio_device_http import devices as enrolled_servers  # noqa: F401

from poolhouse.fleet import producer_devices as devices, tls
from poolhouse.fleet.discovery import Beacon, Membership, derive_token
from poolhouse.fleet.membership import roster
from poolhouse.fleet.remote import Member
from poolhouse.studio.store import Conflict, digest


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    root.chmod(0o700)
    monkeypatch.setattr(devices.home, 'state', lambda *parts: root.joinpath(*parts))
    machine = root / 'machine-id'
    machine.write_text('f' * 16)
    identity = tls.identity(root / 'local-tls', 'local-device')
    remote_identity = tls.identity(root / 'remote-tls', 'remote-device')
    selected = Membership('experiments', b'YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXowMTIzNDU')
    listed = roster(selected.key)
    listed.enrol(identity.beacon, 'Local device', 'test')
    listed.enrol(remote_identity.beacon, 'Remote device', 'test')
    context = {'pool': hashlib.sha256(selected.key).hexdigest(), 'device': identity.fingerprint,
               'scope': 'scope', 'active': True}
    remote = Member('https://127.0.0.1:12346', derive_token(selected.key),
                    beacon=Beacon('Remote device', host='127.0.0.1', port=12346,
                                  machine='e' * 16, cert=remote_identity.beacon))
    state = {'memberships': [selected], 'context': context, 'identity': identity, 'found': [remote],
             'discoveries': [], 'calls': [], 'replies': {}, 'during': None, 'endpoint': 'https://127.0.0.1:12345'}
    ui = SimpleNamespace(pool_key_path=root / 'pool.key', discovery_port=0, name='Local device',
                         studio_identity=lambda: state['identity'], studio_endpoint=lambda: state['endpoint'])
    service = SimpleNamespace(authority=SimpleNamespace(_context=lambda: copy.deepcopy(state['context'])),
                              store=SimpleNamespace(lock=threading.RLock()))
    control = {'certificate': identity.beacon, 'pool': context['pool'], 'runtime': '1' * 64,
               'control_path': '/tmp/control.sock', 'daemon': {'pid': os.getpid(), 'started': 100.0, 'instance': '2' * 64}}
    monkeypatch.setattr(devices, 'memberships', lambda _: list(state['memberships']))
    def discover(*, discovery, timeout):
        assert not service.store.lock._is_owned()
        state['discoveries'].append(discovery)
        assert discovery.key == selected.key and timeout == 5
        return list(state['found'])
    monkeypatch.setattr(Member, 'discover', discover)
    class Transport:
        def __init__(self, member, authorize, *, identity):
            assert identity is state['identity']
            self.member, self.authorize = member, authorize
            assert authorize() is True
        def request(self, action, payload):
            assert self.authorize() is True
            assert not service.store.lock._is_owned()
            state['calls'].append((self.member.beacon.cert, action, copy.deepcopy(payload)))
            result = copy.deepcopy(state['replies'].get((self.member.beacon.cert, action), {'offers': []}))
            if state['during']:
                state['during']()
            assert self.authorize() is True
            return result
    monkeypatch.setattr(devices, 'MemberExecution', Transport)
    monkeypatch.setattr(tls, 'local', lambda: pytest.fail('Created a device identity'))
    monkeypatch.setattr(tls, 'identity', lambda *_: pytest.fail('Loaded or created another identity'))
    adapter = devices.Devices(ui, service, lambda: copy.deepcopy(control))
    return SimpleNamespace(adapter=adapter, state=state, root=root, ui=ui, service=service,
                           identity=identity, remote=remote, listed=listed, selected=selected, control=control)


def target(setup):
    return {'certificate': setup.remote.beacon.cert, 'pool': setup.state['context']['pool']}


def public_offer():
    return {'id': 'a' * 32, 'revision': 1, 'label': 'Record sender', 'device_label': 'Remote device',
            'software_label': 'Record sender', 'available': True, 'reason': ''}


def offer(setup):
    value = public_offer()
    receiver = next(row for row in setup.adapter.receivers() if row['certificate'] == setup.identity.beacon)
    return {'id': value['id'], 'revision': 1, 'state': 'offered', 'process': {'pid': 17, 'started': 100.0},
            'receiver': receiver, 'context': {'pool': receiver['pool'], 'runtime': '3' * 64,
            'daemon_instance': '4' * 64, 'device_certificate': setup.remote.beacon.cert},
            'implementation': '5' * 64, 'label': value['label'], 'device_label': value['device_label']}


def discover_offer(setup):
    setup.state['replies'][setup.remote.beacon.cert, 'producer-offers'] = {'offers': [public_offer()]}
    assert setup.adapter.discover() == [public_offer()]
    private = offer(setup)
    setup.state['replies'][setup.remote.beacon.cert, 'producer-inspect'] = {'offer': private}
    return private


def test_existing_self_and_cached_receivers_need_no_network_even_under_store_lock(setup):
    assert not setup.state['discoveries']
    own = setup.adapter.receivers()
    assert len(own) == 1 and own[0]['certificate'] == setup.identity.beacon
    rows = setup.adapter.refresh()
    assert len(rows) == 2 and setup.state['discoveries'][0].group == 'experiments'
    assert all(row['id'] == digest({'pool': row['pool'], 'certificate': row['certificate']})[:32] for row in rows)
    with setup.service.store.lock:
        assert setup.adapter.receivers() == rows
        with pytest.raises(RuntimeError):
            setup.adapter.refresh()
        with pytest.raises(RuntimeError):
            setup.adapter.request(target(setup), 'producer-offers', {})
    assert len(setup.state['discoveries']) == 1


def test_explicit_refresh_preserves_identical_member_object_for_inflight_guards(setup):
    setup.adapter.refresh()
    first = setup.adapter._members[setup.remote.beacon.cert]
    setup.state['found'] = [copy.deepcopy(setup.remote)]
    setup.adapter.refresh()
    assert setup.adapter._members[setup.remote.beacon.cert][0] is first[0]
    assert setup.adapter._allowed(*first) is True


def test_fixed_requests_inspect_and_intent_actions_use_only_authenticated_mapping(setup):
    private = discover_offer(setup)
    assert setup.adapter.inspect(private['id'], 1) == private
    intent = {'source': 'b' * 32, 'command_id': 'c' * 64, 'offer': private}
    for method in ('prepare', 'activate', 'status', 'revoke'):
        getattr(setup.adapter, method)(intent)
    assert [item[1] for item in setup.state['calls'][-4:]] == [
        'producer-connect', 'producer-activate', 'producer-status', 'producer-revoke']
    assert setup.state['calls'][-4][2] == {'source': 'b' * 32, 'command_id': 'c' * 64,
                                         'offer_id': 'a' * 32, 'revision': 1}
    assert all(item[2] == {'source': 'b' * 32, 'command_id': 'c' * 64} for item in setup.state['calls'][-3:])
    assert setup.adapter.control() == setup.control


@pytest.mark.parametrize('change', ['pool', 'key', 'identity', 'machine', 'endpoint', 'member-address', 'roster'])
def test_cached_addresses_never_override_current_pool_identity_or_membership(setup, change):
    setup.adapter.refresh()
    if change == 'pool':
        setup.state['context']['pool'] = '0' * 64
    elif change == 'key':
        setup.state['memberships'] = []
    elif change == 'identity':
        setup.state['identity'] = replace(setup.identity)
    elif change == 'machine':
        (setup.root / 'machine-id').write_text('0' * 16)
    elif change == 'endpoint':
        setup.state['endpoint'] = 'http://127.0.0.1:12345'
        with pytest.raises(PermissionError):
            setup.adapter.request({'certificate': setup.identity.beacon, 'pool': setup.adapter.anchor['pool']}, 'producer-offers', {})
        return
    elif change == 'member-address':
        setup.remote.base_url = 'https://127.0.0.1:12347'
    else:
        setup.listed.revoke(devices.fingerprint_of(setup.remote.beacon.cert), 'test')
    with pytest.raises(PermissionError):
        setup.adapter.request({'certificate': setup.remote.beacon.cert, 'pool': setup.adapter.anchor['pool']}, 'producer-offers', {})
    assert not setup.state['calls']


def test_removed_member_during_request_discards_response(setup):
    setup.adapter.refresh()
    setup.state['during'] = lambda: setup.listed.revoke(devices.fingerprint_of(setup.remote.beacon.cert), 'test')
    with pytest.raises(PermissionError):
        setup.adapter.request(target(setup), 'producer-offers', {})
    assert len(setup.state['calls']) == 1


def test_discovery_never_accepts_ambiguous_same_certificate_or_unknown_target(setup):
    duplicate = copy.deepcopy(setup.remote)
    duplicate.base_url = 'https://127.0.0.1:12347'
    duplicate.beacon.port = 12347
    setup.state['found'].append(duplicate)
    with pytest.raises(Conflict):
        setup.adapter.refresh()
    with pytest.raises(PermissionError):
        setup.adapter.request(target(setup), 'producer-offers', {})
    assert not setup.state['calls']


def test_offer_collision_or_changed_address_never_remaps_original_offer(setup):
    discover_offer(setup)
    setup.state['replies'][setup.identity.beacon, 'producer-offers'] = {'offers': [public_offer()]}
    with pytest.raises(Conflict):
        setup.adapter.discover()
    setup.state['replies'][setup.identity.beacon, 'producer-offers'] = {'offers': []}
    altered = copy.deepcopy(setup.remote)
    altered.base_url = 'https://127.0.0.1:12347'
    altered.beacon.port = 12347
    setup.state['found'] = [altered]
    with pytest.raises(Conflict):
        setup.adapter.discover()
    with pytest.raises(PermissionError):
        setup.adapter.inspect('a' * 32, 1)


@pytest.mark.parametrize('change', ['producer', 'receiver', 'pool', 'revision', 'process', 'implementation', 'extra'])
def test_private_offer_must_match_authenticated_producer_and_intended_receiver(setup, change):
    private = discover_offer(setup)
    if change == 'producer':
        private['context']['device_certificate'] = setup.identity.beacon
    elif change == 'receiver':
        private['receiver']['certificate'] = setup.remote.beacon.cert
    elif change == 'pool':
        private['context']['pool'] = '0' * 64
    elif change == 'revision':
        private['revision'] = True
    elif change == 'process':
        private['process']['started'] = float('nan')
    elif change == 'implementation':
        private['implementation'] = 'not-a-digest'
    else:
        private['owner'] = True
    setup.state['replies'][setup.remote.beacon.cert, 'producer-inspect'] = {'offer': private}
    with pytest.raises((PermissionError, ValueError)):
        setup.adapter.inspect('a' * 32, 1)


@pytest.mark.parametrize('action,payload', [('submit', {}), ('producer-offers', {'url': 'https://invalid.invalid'}),
                                          ('producer-connect', {'source': 'b' * 32})])
def test_no_generic_compute_url_or_schema_fallback(setup, action, payload):
    setup.adapter.refresh()
    with pytest.raises(ValueError):
        setup.adapter.request(target(setup), action, payload)
    assert not setup.state['calls']


@pytest.mark.parametrize('change', ['missing', 'symlink', 'hardlink'])
def test_machine_identity_is_read_only_and_never_created_or_followed(setup, change):
    path = setup.root / 'machine-id'
    if change == 'missing':
        path.unlink()
    elif change == 'symlink':
        path.rename(setup.root / 'other')
        path.symlink_to(setup.root / 'other')
    else:
        os.link(path, setup.root / 'other')
    with pytest.raises((PermissionError, OSError)):
        devices.Devices(setup.ui, setup.service, lambda: setup.control)
    assert (path.is_symlink() if change == 'symlink' else not path.exists() if change == 'missing' else path.stat().st_nlink == 2)


@pytest.mark.parametrize('change', ['pool', 'certificate', 'runtime', 'process', 'path'])
def test_private_control_callback_cannot_adopt_another_context(setup, change):
    if change in ('pool', 'certificate'):
        setup.control[change] = 'wrong'
    elif change == 'runtime':
        setup.control['runtime'] = None
    elif change == 'process':
        setup.control['daemon']['pid'] = True
    else:
        setup.control['control_path'] = '/tmp/../other'
    with pytest.raises((PermissionError, ValueError)):
        setup.adapter.control()


def test_actual_self_adapter_uses_existing_identity_over_sealed_tls(request, monkeypatch):
    from poolhouse.fleet.discovery import memberships
    opened, _ = request.getfixturevalue('enrolled_servers')
    client, remote_service, _runner, server, _pool = opened[0]
    identity = tls.identity(remote_service.ui.root / 'tls', 'device-a')
    selected = memberships(remote_service.ui.pool_key_path)[0]
    path = devices.home.state('machine-id')
    if not path.exists():
        path.write_text('f' * 16)
    context = {'pool': hashlib.sha256(selected.key).hexdigest(), 'device': identity.fingerprint,
               'scope': 'scope', 'active': True}
    ui = SimpleNamespace(pool_key_path=remote_service.ui.pool_key_path, discovery_port=0, name='Local device',
                         studio_identity=lambda: identity, studio_endpoint=lambda: f'https://127.0.0.1:{server.server_port}')
    service = SimpleNamespace(authority=SimpleNamespace(_context=lambda: context),
                              store=SimpleNamespace(lock=threading.RLock()))
    calls = []
    class Provider:
        def member(self, caller, action, request):
            calls.append((caller, action, request))
            return {'offers': []}
    remote_service.ui.studio_producers = Provider()
    monkeypatch.setattr(Member, 'discover', lambda **_: [])
    monkeypatch.setattr(tls, 'local', lambda: pytest.fail('Looked up an implicit client identity'))
    monkeypatch.setattr(tls, 'identity', lambda *_: pytest.fail('Created a transport identity'))
    adapter = devices.Devices(ui, service, lambda: None)
    assert adapter.discover() == []
    assert calls == [({'certificate': identity.beacon, 'pool': context['pool']}, 'producer-offers', {})]
    assert adapter.receivers()[0]['certificate'] == client.certificate


@pytest.mark.parametrize('change', ['runtime', 'instance', 'started', 'path'])
def test_original_control_descriptor_is_never_silently_replaced(setup, change):
    initial = setup.adapter.control()
    if change == 'runtime':
        setup.control['runtime'] = '9' * 64
    elif change == 'instance':
        setup.control['daemon']['instance'] = '9' * 64
    elif change == 'started':
        setup.control['daemon']['started'] += 1
    else:
        setup.control['control_path'] = '/tmp/new-control.sock'
    with pytest.raises(PermissionError):
        setup.adapter.control()
    assert setup.adapter._control_anchor == initial


def test_self_listener_unavailable_during_refresh_denies_discovery_cleanly(setup, monkeypatch):
    def discovery(**_):
        setup.state['endpoint'] = None
        return [setup.remote]
    monkeypatch.setattr(Member, 'discover', discovery)
    with pytest.raises(PermissionError, match='receiver is unavailable'):
        setup.adapter.discover()
    assert not setup.state['calls']
