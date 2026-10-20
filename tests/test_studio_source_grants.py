"""Actual service grants pin source configuration independently of transient sender liveness."""
import pytest
from test_studio_stream_http import served  # noqa: F401
from test_studio_webhook_setup import configured, create  # noqa: F401


@pytest.mark.parametrize('changed', [{'generation': 'e' * 32}, {'revision': 2}, {'label': 'Reconfigured source'}])
def test_current_node_authorization_rejects_changed_source_before_resource_reads(request, changed):
    app, cookie, _ = request.getfixturevalue('configured')
    source, sub = create(app, cookie)
    service = app.ui.studio_streams
    actual = service.store.get('subscription', sub['id'], app.ui.studio_authority._context()['scope'])
    node = actual['resolved_workflow']['graph']['nodes'][0]
    principal = actual['service_principal']
    assert app.ui.studio.authorize(principal, node, {}) is True
    reads = []
    app.ui.studio_authorize = lambda *args: reads.append(args) is None
    with service.store.lock, service.store.graph.transaction():
        original = service.store.get('source', source['id'], actual['scope'])
        service.store.store.put('source', {**original, **changed})
    assert app.ui.studio.authorize(principal, node, {}) is False
    assert reads == []


def test_sender_exit_does_not_revoke_authority_for_already_admitted_work(request, monkeypatch):
    app, cookie, _ = request.getfixturevalue('configured')
    source, sub = create(app, cookie)
    service = app.ui.studio_streams
    actual = service.store.get('subscription', sub['id'], app.ui.studio_authority._context()['scope'])
    node = actual['resolved_workflow']['graph']['nodes'][0]
    snapshot = service.store.get('source', source['id'], actual['scope'])
    monkeypatch.setattr(app.ui.studio_webhook_setup, 'ready', lambda *args: False)
    assert app.ui.studio.authorize(actual['service_principal'], node, {}) is True
    assert service.store.get('source', source['id'], actual['scope']) == snapshot
