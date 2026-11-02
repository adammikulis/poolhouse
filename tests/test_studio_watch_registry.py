"""The native picker bridge never activates execution or adopts a new revoked generation."""
import copy
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse.fleet.studio_watch_registry import create_native_picks
from poolhouse.studio import stream_sources


class Store:
    def __init__(self):
        self.lock = threading.RLock()
        self.rows = {}
        self.graph = SimpleNamespace(get_doc=lambda key: copy.deepcopy(self.rows.get(key)))

    def put(self, kind, row):
        self.rows['studio.' + kind + ':' + row['id']] = copy.deepcopy(row)

    def get(self, kind, ident, scope):
        row = self.rows['studio.' + kind + ':' + ident]
        if row['scope'] != scope:
            raise KeyError(ident)
        return copy.deepcopy(row)


@pytest.fixture
def bridge(tmp_path):
    store = Store()
    principal = {'id': 'interactive', 'scope': 'pool-device-root', 'owner': True}
    anchor = {'generation': None, 'active': False, 'live': True, 'sources': {}}
    def epoch(owner, *, create):
        assert owner == principal
        if anchor['generation'] is None:
            if not create:
                raise KeyError('No selection state')
            anchor['generation'] = 'disabled-selection-generation'
        return anchor['generation']
    def interactive(owner):
        return owner == principal and anchor['live']
    calls = []
    def create(owner, binding, request_id, expected):
        assert expected == anchor['generation'] and interactive(owner)
        assert not anchor['active']
        row = {'id': 'source:' + request_id, 'title': Path(binding['path']).name,
               'binding': copy.deepcopy(binding), 'epoch': expected}
        prior = anchor['sources'].setdefault(request_id, row)
        assert prior == row
        calls.append((owner, binding, request_id, expected))
        return prior
    callbacks = {'selection_epoch': epoch, 'interactive_current': interactive, 'create_source': create}
    picks = create_native_picks(store, **callbacks)
    path = tmp_path / 'events.jsonl'
    path.write_text('{"n":1}\n')
    return picks, store, principal, anchor, calls, callbacks, path


def selected(bridge):
    picks, _, principal, *_ = bridge
    request = picks.request(principal, 'file', 'a' * 32)
    first = picks.begin(request['request_id'], request['nonce'])
    return request['request_id'], request['nonce'], first['claim']


def test_first_selection_creates_disabled_state_and_only_a_resource_grant(bridge):
    picks, _, principal, anchor, calls, _, path = bridge
    args = selected(bridge)
    assert anchor['generation'] == 'disabled-selection-generation' and anchor['active'] is False
    response = picks.complete(*args, str(path))
    assert response == picks.complete(*args, str(path))
    assert set(response['source']) == {'id', 'title'}
    assert str(path) not in str(response)
    assert len(calls) == 1 and calls[0][0] == principal
    binding = calls[0][1]
    assert binding['path'] == str(path) and binding['mode'] == 'file'
    assert anchor['active'] is False


def test_original_interactive_session_is_required_before_tombstone_creation(bridge):
    picks, _, principal, anchor, calls, *_ = bridge
    anchor['live'] = False
    with pytest.raises(PermissionError):
        picks.request(principal, 'file', 'a' * 32)
    assert anchor['generation'] is None and not calls


@pytest.mark.parametrize('change', ['session', 'generation'])
def test_original_selection_epoch_is_not_replaced_after_revoke(bridge, change):
    picks, _, _, anchor, calls, _, path = bridge
    args = selected(bridge)
    if change == 'session':
        anchor['live'] = False
    else:
        anchor['generation'] = 'revoked-generation'
    with pytest.raises(PermissionError):
        picks.complete(*args, str(path))
    assert not calls


def test_revocation_after_descriptor_bind_prevents_source_creation(bridge, monkeypatch):
    picks, _, _, anchor, calls, _, path = bridge
    args = selected(bridge)
    original = stream_sources.bind
    def revoke(*args, **kwargs):
        binding = original(*args, **kwargs)
        anchor['generation'] = 'revoked-during-bind'
        return binding
    monkeypatch.setattr(stream_sources, 'bind', revoke)
    with pytest.raises(PermissionError):
        picks.complete(*args, str(path))
    assert not calls


def test_create_source_receives_original_epoch_for_atomic_compare(bridge):
    _, store, principal, anchor, calls, callbacks, path = bridge
    supplied = []
    def revoke_at_commit(owner, binding, request_id, expected):
        supplied.append(expected)
        anchor['generation'] = 'new-generation'
        if expected != anchor['generation']:
            raise PermissionError('Original generation was revoked before source commit')
        pytest.fail('A new-generation source must never be minted')
    callbacks['create_source'] = revoke_at_commit
    picks = create_native_picks(store, **callbacks)
    request = picks.request(principal, 'file', 'a' * 32)
    first = picks.begin(request['request_id'], request['nonce'])
    with pytest.raises(PermissionError):
        picks.complete(request['request_id'], request['nonce'], first['claim'], str(path))
    assert supplied == ['disabled-selection-generation'] and not calls


def test_symlink_selection_cannot_bypass_descriptor_binding(bridge):
    picks, _, _, _, calls, _, path = bridge
    chosen = path.with_name('linked.jsonl')
    chosen.symlink_to(path)
    with pytest.raises(OSError):
        picks.complete(*selected(bridge), str(chosen))
    assert not calls


def test_lost_create_ack_reuses_exact_binding_identity(bridge):
    _, store, principal, anchor, calls, callbacks, path = bridge
    create = callbacks['create_source']
    first = {'value': True}
    def lost_ack(*args):
        result = create(*args)
        if first['value']:
            first['value'] = False
            raise OSError('The source commit acknowledgement was lost')
        return result
    callbacks['create_source'] = lost_ack
    picks = create_native_picks(store, **callbacks)
    request = picks.request(principal, 'file', 'a' * 32)
    start = picks.begin(request['request_id'], request['nonce'])
    args = request['request_id'], request['nonce'], start['claim'], str(path)
    with pytest.raises(OSError):
        picks.complete(*args)
    response = picks.complete(*args)
    assert response['status'] == 'complete'
    assert len(anchor['sources']) == 1 and len(calls) == 2
    assert calls[0][1] == calls[1][1]
