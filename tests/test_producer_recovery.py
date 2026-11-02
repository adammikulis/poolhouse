"""An explicit configuration barrier resolves only the original durable publication."""
import pytest
from test_producer_publications import receive, setup as _publication_setup

from poolhouse.fleet.producer_connections import Connections
from poolhouse.fleet.producer_recovery import OwnerBarrier
from poolhouse.fleet.studio_authority import StudioAuthority

publication_setup = _publication_setup


@pytest.fixture
def setup(publication_setup, tmp_path):
    value = publication_setup
    context = {'scope': value.binding['scope'], 'pool': value.binding['pool'], 'device': '8' * 64, 'active': True}
    value.owner = {'id': 'actual-owner', 'scope': context['scope']}
    value.owner_state = {'owner': True}
    authority = StudioAuthority(tmp_path / 'authority', current=lambda: context,
        owner=lambda principal: 'launch-ticket' if principal == value.owner and value.owner_state['owner'] else '',
        resources=lambda *args: True)
    value.publications.service.authority = authority
    source = value.store.get('source', value.binding['source'], context['scope'])
    grant = authority.mint(value.owner, value.sub['id'],
        {'source': source, 'workflow': {'graph': {'nodes': []}}, 'resources': [], 'destinations': []}, 'actual-start')
    value.sub.update(grant=grant['id'], service_principal=authority.principal(grant))
    value.store.put_subscription(value.sub)
    value.store.graph.put_doc('studio.webhook-subscription:' + source['id'], {'subscription': value.sub['id'],
        'generation': source['generation'], 'scope': context['scope'], 'grant': grant['id']})
    yield value
    authority.close()


def barrier(setup):
    service = setup.publications.service
    context = service.authority._context()
    old = setup.store.get('source', setup.binding['source'], context['scope'])
    intent = {**setup.store.graph.get_doc(Connections._key(old['id'])), 'context': context}
    new = {**old, 'generation': '9' * 32}
    return OwnerBarrier(service, setup.publications), old, intent, new, setup.owner_state


def settle(setup, values):
    recovery, old, intent, new, _ = values
    with setup.store.lock, setup.store.graph.transaction():
        setup.store.store.put('source', new)
        return recovery.settle(setup.owner, old, intent, new)


def test_accepted_lost_ack_wins_over_revocation_barrier(setup):
    setup.state['mode'] = 'lost'
    assert receive(setup)['state'] == 'interrupted'
    result = settle(setup, barrier(setup))
    assert result['state'] == 'done'
    assert setup.state['calls'] == 1
    assert setup.store.graph.get_doc('studio.producer-pending:' + setup.binding['source']) is None


def test_unknown_without_inbox_becomes_definitively_rejected_only_after_generation_disabled(setup):
    setup.state['mode'] = 'before'
    assert receive(setup)['state'] == 'interrupted'
    values = barrier(setup)
    result = settle(setup, values)
    assert result['state'] == 'rejected'
    row = setup.publications.log.get({**setup.binding, 'subscription': setup.sub['id'], 'grant': setup.sub['grant']}, 'order-1')
    assert row['not_sent'] is False and row['attempt']
    with pytest.raises((PermissionError, ValueError)):
        receive(setup)
    assert setup.state['calls'] == 1


def test_unattempted_command_is_discarded_with_event_tombstone(setup):
    publications = setup.publications
    binding = {**setup.binding, 'subscription': setup.sub['id'], 'grant': setup.sub['grant']}
    publications.log.stage(binding, 'order-1', b'{"amount":17}')
    result = settle(setup, barrier(setup))
    assert result['state'] == 'discarded'
    assert setup.state['calls'] == 0


def test_malformed_pending_rolls_back_generation_replacement(setup):
    setup.state['mode'] = 'before'
    receive(setup)
    values = barrier(setup)
    pointer = 'studio.producer-pending:' + setup.binding['source']
    setup.store.graph.put_doc(pointer, {'key': 'missing', 'binding': {}})
    with pytest.raises(PermissionError):
        settle(setup, values)
    assert setup.store.get('source', setup.binding['source'], setup.binding['scope']) == values[1]


def test_owner_revocation_during_receipt_rolls_back_barrier(setup, monkeypatch):
    setup.state['mode'] = 'before'
    receive(setup)
    values = barrier(setup)
    original = values[0].admission._inbox

    def revoked(row):
        result = original(row)
        values[-1]['owner'] = False
        return result

    monkeypatch.setattr(values[0].admission, '_inbox', revoked)
    with pytest.raises(PermissionError):
        settle(setup, values)
    assert setup.store.get('source', setup.binding['source'], setup.binding['scope']) == values[1]
    assert setup.publications.log.get({**setup.binding, 'subscription': setup.sub['id'], 'grant': setup.sub['grant']}, 'order-1')['state'] == 'interrupted'


def test_workflow_database_copy_cannot_replace_actual_authority_registry_grant(setup):
    setup.state['mode'] = 'before'
    receive(setup)
    values = barrier(setup)
    authority = setup.publications.service.authority
    key = 'studio.authority.grant:' + setup.sub['grant']
    with authority.lock, authority.graph.transaction():
        actual = authority.graph.get_doc(key)
        authority.graph.delete_doc(key)
    setup.store.graph.put_doc(key, actual)
    with pytest.raises(PermissionError):
        settle(setup, values)
    assert setup.store.get('source', setup.binding['source'], setup.binding['scope']) == values[1]
