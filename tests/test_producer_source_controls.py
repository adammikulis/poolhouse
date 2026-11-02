"""Explicit source lifecycle changes disable the old configuration before remote cleanup."""
from types import SimpleNamespace

import pytest
from test_producer_connections import BODY, OWNER, intent, setup as _connection_setup

from poolhouse.fleet.producer_source_controls import SourceControls
from poolhouse.studio.store import Conflict

setup = _connection_setup


def controls(setup):
    connections, _, state = setup
    state['cleanups'] = []

    def revoke(old):
        assert not connections.store.lock._is_owned()
        state['cleanups'].append(old)
        return {'revoked': True}

    runtime = SimpleNamespace(service=connections.service, connections=connections,
        publications=SimpleNamespace(admission=None), identity=connections.identity,
        devices=SimpleNamespace(revoke=revoke))
    return SourceControls(runtime)


@pytest.mark.parametrize('action', ['rotate', 'revoke'])
def test_owner_action_replaces_generation_before_one_exact_cleanup_and_duplicate_attaches(setup, action):
    connections, _, state = setup
    source = connections.register(OWNER, BODY)
    control = controls(setup)
    body = {'revision': source['revision'], 'idempotency_key': action + '-source'}
    updated = control.action(OWNER, source, action, body)
    assert updated['generation'] != source['generation']
    assert updated['revision'] == source['revision'] + 1
    assert connections.configured(source['id'], source['generation']) is False
    assert intent(connections, updated)['phase'] == 'revoked'
    assert control.action(OWNER, source, action, body) == updated
    assert len(state['cleanups']) == 1
    with pytest.raises(Conflict):
        control.action(OWNER, updated, action, {**body, 'revision': updated['revision']})


def test_weak_owner_cannot_disable_or_cleanup_source(setup):
    connections, _, state = setup
    source = connections.register(OWNER, BODY)
    control = controls(setup)
    state['origin'] = 'invite'
    with pytest.raises(PermissionError):
        control.action(OWNER, source, 'revoke', {'revision': 1, 'idempotency_key': 'revoke'})
    assert connections.store.get('source', source['id'], source['scope']) == source
    assert state['cleanups'] == []


def test_remote_cleanup_uncertainty_never_restores_old_generation(setup):
    connections, _, _state = setup
    source = connections.register(OWNER, BODY)
    control = controls(setup)

    def uncertain(old):
        raise OSError('lost cleanup receipt')

    control.runtime.devices.revoke = uncertain
    updated = control.action(OWNER, source, 'revoke', {'revision': 1, 'idempotency_key': 'revoke'})
    assert updated['generation'] != source['generation']
    assert connections.configured(source['id'], source['generation']) is False
    assert 'unconfirmed' in intent(connections, updated)['reason']


def test_duplicate_that_reaches_inner_transaction_attaches_original_command(setup):
    connections, _, state = setup
    source = connections.register(OWNER, BODY)
    control = controls(setup)
    body = {'revision': 1, 'idempotency_key': 'same-revoke'}
    context, actual, key, repeated = control._command(OWNER, source, 'revoke', body)
    assert repeated is False
    epoch = connections.authority.selection_epoch(OWNER, create=True)
    selected = (context, control.runtime.identity(), epoch, None, key)
    first, old, command = control._replace(OWNER, actual, 'revoke', body, selected)
    second, duplicate_old, duplicate_command = control._replace(OWNER, actual, 'revoke', body, selected)
    assert second == first and old and command
    assert duplicate_old is duplicate_command is None
    assert state['cleanups'] == []
