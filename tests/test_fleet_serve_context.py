"""Per-start contexts follow actual model metadata and the maintained serving lease."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from conftest import write_gguf

from poolhouse.fleet import serving


@pytest.fixture
def model(tmp_path):
    return write_gguf(tmp_path / 'context-model.gguf', {
        'general.architecture': 'llama', 'llama.context_length': 16384})


@pytest.mark.parametrize('context', [True, False, 0, -1, 16385, 10**20, '4096', 4096.0, [], {}])
def test_invalid_context_never_reaches_serving(model, context):
    with pytest.raises(ValueError):
        serving.context_settings(model, context)


def test_absent_context_preserves_default_without_inventing_metadata(tmp_path):
    unknown = write_gguf(tmp_path / 'unknown.gguf', {})
    assert serving.context_settings(unknown, None, 2048) == serving.ServeSettings(context=2048)
    with pytest.raises(ValueError, match='verified context'):
        serving.context_settings(unknown, 2048)


def test_effective_model_supplies_context_limit(model, tmp_path, monkeypatch):
    actual = write_gguf(tmp_path / 'integrated.gguf', {
        'general.architecture': 'llama', 'llama.context_length': 4096})
    monkeypatch.setattr(serving.model_components, 'effective', lambda _path: actual)
    assert serving.context_settings(model, 4096).model_identity[0] == str(actual.resolve())
    with pytest.raises(ValueError, match='4096'):
        serving.context_settings(model, 8192)


def test_model_changed_during_header_read_refuses_context(model, monkeypatch):
    original = serving.read_gguf_header
    def replaced(path):
        metadata = original(path)
        replacement = path.with_suffix('.replacement')
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)
        return metadata
    monkeypatch.setattr(serving, 'read_gguf_header', replaced)
    with pytest.raises(ValueError, match='changed'):
        serving.context_settings(model, 4096)


def test_checked_model_identity_cannot_be_copied_to_another_path(model, tmp_path):
    settings = serving.context_settings(model, 4096)
    other = tmp_path / 'other.gguf'
    other.write_bytes(model.read_bytes())
    with pytest.raises(ValueError, match='changed'):
        serving.start_model(tmp_path, other, settings=settings, manager=SimpleNamespace())


def test_model_replacement_between_validation_and_start_is_denied(model, tmp_path):
    settings = serving.context_settings(model, 4096)
    replacement = model.with_suffix('.replacement')
    replacement.write_bytes(model.read_bytes())
    replacement.replace(model)
    with pytest.raises(ValueError, match='changed'):
        serving.start_model(tmp_path, model, settings=settings, manager=SimpleNamespace())


def test_model_replacement_during_estimate_never_leases(model, tmp_path, monkeypatch):
    settings = serving.context_settings(model, 4096)
    leased = []
    def estimate(*_args, **_kwargs):
        replacement = model.with_suffix('.replacement')
        replacement.write_bytes(model.read_bytes())
        replacement.replace(model)
        return object()
    monkeypatch.setattr(serving, 'estimate', estimate)
    monkeypatch.setattr(serving, 'verdict', lambda *_args: 'green')
    monkeypatch.setattr(serving, 'machine_memory', lambda: None)
    manager = SimpleNamespace(lease=lambda *_args, **_kwargs: leased.append(True))
    with pytest.raises(ValueError, match='changed'):
        serving.start_model(tmp_path, model, settings=settings, manager=manager)
    assert not leased


def test_exact_context_reaches_existing_lease_without_mutating_defaults(model, tmp_path, monkeypatch):
    captured = []
    manager = SimpleNamespace(lease=lambda spec, **_kwargs: captured.append(spec) or SimpleNamespace(port=23456))
    monkeypatch.setattr(serving, 'estimate', lambda *_args, **_kwargs: object())
    monkeypatch.setattr(serving, 'verdict', lambda *_args: 'green')
    monkeypatch.setattr(serving, 'machine_memory', lambda: None)
    settings = replace(serving.context_settings(model, 6144), name='Context test')
    serving.start_model(tmp_path, model, settings=settings, manager=manager)
    assert captured[0].context == 6144 and captured[0].parallel == 1
    assert serving.ServeSettings().context == 8192


@pytest.fixture
def app(tmp_path, model, monkeypatch):
    from test_fleet_ui import Serving as DaemonServing

    from poolhouse.fleet import routes
    from poolhouse.fleet.discovery import mint_pool
    from poolhouse.fleet.models import Models
    instance = DaemonServing(tmp_path)
    mint_pool('context-test-pool', instance.keyfile)
    session = instance.ui.sessions.open('context-owner', 'launch-ticket')
    instance.ui.models = Models([tmp_path], tmp_path)
    instance.ui.serving = serving.Serving(tmp_path / 'serving.json')
    instance.ui.settings.context = 2048
    monkeypatch.setattr(routes, '_can_serve', lambda: True)
    captured = []
    def start(path, settings):
        captured.append((path, settings))
        return serving.Served(port=12345, models=['Context model'], context=8192, slot_context=8192)
    instance.ui.hosting = SimpleNamespace(start=start)
    try:
        yield instance, session, captured
    finally:
        instance.close()


def test_http_context_reaches_hosting_and_returns_observed_server_context(app, model):
    instance, session, captured = app
    status, result, _ = instance.call('/ui/serving', method='POST', cookie='poolhouse_ui=' + session.sid,
                                     body={'path': str(model), 'context': 4096})
    assert status == 201, result
    assert captured[0][1].context == 4096 and captured[0][1].model_identity
    assert result['context'] == 8192  # A shared server can have a larger actual context.
    assert instance.ui.settings.context == 2048


@pytest.mark.parametrize('context', [True, 0, 16385, '4096', None])
def test_http_invalid_explicit_context_does_not_start(app, model, context):
    instance, session, captured = app
    status, _, _ = instance.call('/ui/serving', method='POST', cookie='poolhouse_ui=' + session.sid,
                                body={'path': str(model), 'context': context})
    assert status == 400 and captured == []


def test_http_absent_context_uses_existing_default(app, model):
    instance, session, captured = app
    status, _, _ = instance.call('/ui/serving', method='POST', cookie='poolhouse_ui=' + session.sid,
                                body={'path': str(model)})
    assert status == 201 and captured[0][1].context == 2048


def test_revoked_session_during_model_metadata_read_never_starts(app, model, monkeypatch):
    instance, session, captured = app
    original = serving.read_gguf_header
    def revoked(path):
        metadata = original(path)
        instance.ui.sessions.close(session.sid)
        return metadata
    monkeypatch.setattr(serving, 'read_gguf_header', revoked)
    status, _, _ = instance.call('/ui/serving', method='POST', cookie='poolhouse_ui=' + session.sid,
                                body={'path': str(model), 'context': 4096})
    assert status in {401, 403} and captured == []



def test_revocation_during_estimate_never_leases(model, tmp_path, monkeypatch):
    current = [True]
    settings = replace(serving.context_settings(model, 4096), current=lambda: current[0])
    leased = []
    def estimate(*_args, **_kwargs):
        current[0] = False
        return object()
    monkeypatch.setattr(serving, 'estimate', estimate)
    monkeypatch.setattr(serving, 'verdict', lambda *_args: 'green')
    monkeypatch.setattr(serving, 'machine_memory', lambda: None)
    manager = SimpleNamespace(lease=lambda *_args, **_kwargs: leased.append(True))
    with pytest.raises(PermissionError):
        serving.start_model(tmp_path, model, settings=settings, manager=manager)
    assert not leased


def test_truthy_current_proof_cannot_start(model, tmp_path):
    settings = replace(serving.context_settings(model, 4096), current=lambda: 1)
    with pytest.raises(PermissionError):
        serving.start_model(tmp_path, model, settings=settings, manager=SimpleNamespace())
    assert 'current=' not in repr(settings)



def test_reused_broker_port_is_the_only_registered_endpoint(model, tmp_path, monkeypatch):
    requested = []
    manager = SimpleNamespace(lease=lambda spec, **_kwargs: requested.append(spec.port) or SimpleNamespace(port=23456))
    monkeypatch.setattr(serving, 'free_port', lambda: 34567)
    monkeypatch.setattr(serving, 'estimate', lambda *_args, **_kwargs: object())
    monkeypatch.setattr(serving, 'verdict', lambda *_args: 'green')
    monkeypatch.setattr(serving, 'machine_memory', lambda: None)
    registry = serving.Serving(tmp_path / 'registry.json')
    started = serving.start_model(tmp_path, model, manager=manager, serving=registry)
    assert requested == [34567] and started.port == 23456
    assert [row.port for row in registry.all()] == [23456]


def test_revoked_request_after_lease_releases_only_its_lease(model, tmp_path, monkeypatch):
    current = [True]
    released = []
    lease = SimpleNamespace(port=23456)
    def leased(*_args, **_kwargs):
        current[0] = False
        return lease
    manager = SimpleNamespace(lease=leased, release=released.append)
    monkeypatch.setattr(serving, 'estimate', lambda *_args, **_kwargs: object())
    monkeypatch.setattr(serving, 'verdict', lambda *_args: 'green')
    monkeypatch.setattr(serving, 'machine_memory', lambda: None)
    registry = serving.Serving(tmp_path / 'registry.json')
    settings = replace(serving.context_settings(model, 4096), current=lambda: current[0])
    with pytest.raises(PermissionError):
        serving.start_model(tmp_path, model, settings=settings, manager=manager, serving=registry)
    assert released == [lease] and registry.all() == []


@pytest.mark.parametrize('actual', [2048, 8192])
def test_hosting_reports_actual_context_instead_of_requested(model, tmp_path, monkeypatch, actual):
    observed = serving.Served(port=23456, models=['Observed model'], context=actual, slot_context=actual)
    registry = SimpleNamespace(live=lambda **_kwargs: [observed])
    manager = SimpleNamespace()
    started = serving.Started(port=23456, lease=object(), manager=manager)
    monkeypatch.setattr(serving, 'start_model', lambda *_args, **_kwargs: started)
    hosting = serving.Hosting(tmp_path, registry, manager=manager)
    assert hosting.start(model, serving.ServeSettings(context=4096)).context == actual


@pytest.mark.parametrize('port', [True, 0, 65536, '23456'])
def test_invalid_broker_endpoint_is_not_registered(model, tmp_path, monkeypatch, port):
    released = []
    lease = SimpleNamespace(port=port)
    manager = SimpleNamespace(lease=lambda *_args, **_kwargs: lease, release=released.append)
    monkeypatch.setattr(serving, 'estimate', lambda *_args, **_kwargs: object())
    monkeypatch.setattr(serving, 'verdict', lambda *_args: 'green')
    monkeypatch.setattr(serving, 'machine_memory', lambda: None)
    registry = serving.Serving(tmp_path / 'registry.json')
    with pytest.raises(ValueError, match='endpoint'):
        serving.start_model(tmp_path, model, manager=manager, serving=registry)
    assert registry.all() == [] and released == [lease]


def test_repeated_hosting_start_retains_one_owned_lease(model, tmp_path, monkeypatch):
    old, new = object(), object()
    handles = iter([old, new, new])
    released = []
    manager = SimpleNamespace(release=released.append)
    registry = SimpleNamespace(live=lambda **_kwargs: [], unregister=lambda _port: None)
    def start(*_args, **_kwargs):
        return serving.Started(port=23456, lease=next(handles), manager=manager)
    monkeypatch.setattr(serving, 'start_model', start)
    hosting = serving.Hosting(tmp_path, registry, manager=manager)
    for _ in range(3):
        assert hosting.start(model).port == 23456
    assert released == [old] and hosting.leases[23456] == [new]
    hosting.stop(23456)
    hosting.stop(23456)
    assert released == [old, new] and hosting.leases == {}


def test_displaced_release_failure_keeps_all_owned_handles_tracked(model, tmp_path, monkeypatch):
    old, new = object(), object()
    handles = iter([old, new])
    failing = [True]
    released = []
    def release(lease):
        if failing[0]:
            raise OSError('isolated release failure')
        released.append(lease)
    manager = SimpleNamespace(release=release)
    registry = SimpleNamespace(live=lambda **_kwargs: [], unregister=lambda _port: None)
    monkeypatch.setattr(serving, 'start_model', lambda *_args, **_kwargs:
        serving.Started(port=23456, lease=next(handles), manager=manager))
    hosting = serving.Hosting(tmp_path, registry, manager=manager)
    hosting.start(model)
    with pytest.raises(OSError):
        hosting.start(model)
    assert hosting.leases[23456] == [old, new]
    failing[0] = False
    hosting.stop(23456)
    assert released == [old, new] and hosting.leases == {}


def test_concurrent_reused_port_starts_preserve_only_current_owned_handle(model, tmp_path, monkeypatch):
    import threading
    handles = [object(), object()]
    remaining = iter(handles)
    released = []
    manager = SimpleNamespace(release=released.append)
    registry = SimpleNamespace(live=lambda **_kwargs: [], unregister=lambda _port: None)
    monkeypatch.setattr(serving, 'start_model', lambda *_args, **_kwargs:
        serving.Started(port=23456, lease=next(remaining), manager=manager))
    hosting = serving.Hosting(tmp_path, registry, manager=manager)
    errors = []
    def start():
        try:
            hosting.start(model)
        except (OSError, RuntimeError, ValueError, StopIteration) as error:
            errors.append(error)
    workers = [threading.Thread(target=start) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(2)
    assert not errors and all(not worker.is_alive() for worker in workers)
    assert released == handles[:1] and hosting.leases[23456] == handles[1:]



def test_failed_new_start_preserves_previous_hosting_lease(model, tmp_path, monkeypatch):
    old = object()
    released = []
    manager = SimpleNamespace(release=released.append)
    registry = SimpleNamespace(live=lambda **_kwargs: [], unregister=lambda _port: None)
    first = [True]
    def start(*_args, **_kwargs):
        if not first[0]:
            raise PermissionError('The current request was revoked.')
        first[0] = False
        return serving.Started(port=23456, lease=old, manager=manager)
    monkeypatch.setattr(serving, 'start_model', start)
    hosting = serving.Hosting(tmp_path, registry, manager=manager)
    hosting.start(model)
    with pytest.raises(PermissionError):
        hosting.start(model)
    assert hosting.leases[23456] == [old] and released == []



@pytest.mark.parametrize('selection,expected', [
    ({'name': 'context-model.gguf'}, 400),
    ({'path': 'canonical', 'name': 'context-model.gguf'}, 400),
    ({'path': 'canonical', 'label': 'Context model'}, 400),
    ({'path': 'context-model.gguf'}, 404),
    ({'path': ''}, 400),
    ({'path': True}, 400),
    ({'context': 4096}, 400),
    ([], 400),
])
def test_serving_requires_canonical_path_without_name_alias(app, model, monkeypatch, selection, expected):
    instance, session, captured = app
    def forbidden_find(*_args):
        pytest.fail('Display names must never resolve serving identities')
    monkeypatch.setattr(instance.ui.models, 'find', forbidden_find)
    if isinstance(selection, dict) and selection.get('path') == 'canonical':
        selection = dict(selection, path=str(model))
    status, _, _ = instance.call('/ui/serving', method='POST', cookie='poolhouse_ui=' + session.sid, body=selection)
    assert status == expected and captured == []
