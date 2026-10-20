"""Private setup metadata never turns readiness or synthetic samples into real grants."""
import copy
import io
import json
import threading
from types import SimpleNamespace

import pytest
from test_studio_stream_http import served  # noqa: F401

from poolhouse.fleet.producer_setup import Setup
from poolhouse.studio.store import StudioStore
from poolhouse.studio.stream_store import StreamStore

PRODUCER = 'software-producer'
LOCAL = 'local-synthetic'
OWNER = {'id': 'owner', 'scope': 'scope'}


@pytest.fixture
def setup(tmp_path):
    base = StudioStore(tmp_path / 'studio.ladybug')
    store = StreamStore(base)
    source = store.create('source', OWNER, {'kind': 'webhook', 'provider': PRODUCER,
                          'generation': 'a' * 32, 'label': 'Software source'}, 'software')
    local_source = store.create('source', OWNER, {'kind': 'webhook', 'provider': LOCAL,
                                'generation': 'b' * 32, 'label': 'Synthetic source'}, 'local')
    sub = store.create('subscription', OWNER, {'source_id': source['id']}, 'sub')
    local_sub = store.create('subscription', OWNER, {'source_id': local_source['id']}, 'local-sub')
    state = {'context': {'scope': 'scope', 'pool': 'c' * 64, 'device': 'd' * 64, 'active': True},
             'owner': True, 'current': True, 'available': True, 'factory': 0, 'failure': False,
             'closed': 0, 'calls': [], 'during': None}
    def owner(principal):
        if not state['owner'] or principal != OWNER:
            raise PermissionError('Current actual owner required')
        return state['context']
    service = SimpleNamespace(store=store, authority=SimpleNamespace(_owner=owner,
                              _context=lambda: copy.deepcopy(state['context'])))
    class Local:
        def providers(self):
            return [{'id': LOCAL, 'label': 'Synthetic test sender', 'available': True, 'requirements': []}]
        def describe(self, row):
            state['calls'].append(('local-describe', row['id']))
            return {**row, 'test_supported': True}
        def ready(self, ident, generation):
            state['calls'].append(('local-ready', ident))
            return True
        current = ready
        def register(self, principal, body):
            state['calls'].append(('local-register', copy.deepcopy(body)))
            return local_source
        def pending(self, actual):
            state['calls'].append(('local-pending', actual['id']))
            return {'sample_id': 'synthetic', 'idempotency_key': 'command'}
        def send(self, principal, actual, body):
            state['calls'].append(('local-send', actual['id']))
            return {'id': 'e' * 64, 'status': 'queued'}
    class Runtime:
        def __init__(self):
            self.connections = SimpleNamespace(register=self.register)
        def providers(self):
            assert not store.lock._is_owned()
            if state['during']:
                state['during']()
            return {'id': PRODUCER, 'label': 'Connected software producer', 'available': state['available'],
                    'requirements': [], 'offers': []}
        def describe(self, row):
            state['calls'].append(('describe', row['id']))
            return {**row, 'available': state['available'], 'test_supported': True, 'requirements': [],
                    'connection': {'state': 'ready', 'can_revoke': True}}
        def register(self, principal, body):
            state['calls'].append(('register', copy.deepcopy(body)))
            return source
        def available(self, row):
            assert not store.lock._is_owned()
            state['calls'].append(('available', row['id']))
            return state['available']
        def ready(self, ident, generation):
            state['calls'].append(('ready', ident))
            return state['current']
        def current(self, ident, generation):
            state['calls'].append(('current', ident))
            return state['current']
        def action(self, principal, row, action, body):
            state['calls'].append(('action', row['id'], action, copy.deepcopy(body)))
            return row
        def close(self):
            state['closed'] += 1
    runtime = Runtime()
    def factory():
        assert not store.lock._is_owned()
        state['factory'] += 1
        if state['failure']:
            raise OSError('private failure details must not be exported')
        return runtime
    facade = Setup(SimpleNamespace(), service, Local(), factory)
    yield SimpleNamespace(facade=facade, state=state, service=service, source=source,
                          local_source=local_source, sub=sub, local_sub=local_sub, runtime=runtime)
    facade.close()
    base.close()


def handler(method='GET', body=None):
    raw = json.dumps(body or {}).encode()
    result = []
    return SimpleNamespace(method=method, header=lambda *_: str(len(raw)),
                           handler=SimpleNamespace(rfile=io.BytesIO(raw)),
                           send=lambda status, value: result.append((status, value)), result=result)


def test_ready_current_and_software_pending_never_construct_runtime_under_store_lock(setup):
    with setup.service.store.lock:
        assert setup.facade.ready(setup.source['id'], setup.source['generation']) is False
        assert setup.facade.current(setup.source['id'], setup.source['generation']) is False
        assert setup.facade.pending(setup.sub) is None
    assert setup.state['factory'] == 0 and not setup.state['calls']
    assert setup.facade.providers()[-1]['id'] == PRODUCER
    assert setup.state['factory'] == 1
    with setup.service.store.lock:
        assert setup.facade.ready(setup.source['id'], setup.source['generation']) is True
        assert setup.facade.current(setup.source['id'], setup.source['generation']) is True
    assert setup.state['factory'] == 1


def test_definite_factory_failure_is_truthful_and_later_metadata_can_retry(setup):
    setup.state['failure'] = True
    result = setup.facade.providers()[-1]
    assert result['available'] is False and result['offers'] == []
    assert 'private failure' not in str(result)
    described = setup.facade.describe(setup.source)
    assert described['available'] is False and described['test_supported'] is False
    assert not any(described['connection'][name] for name in ('can_connect', 'can_rotate', 'can_revoke', 'can_check'))
    count = setup.state['factory']
    assert setup.facade.ready(setup.source['id'], setup.source['generation']) is False
    assert setup.state['factory'] == count
    setup.state['failure'] = False
    assert setup.facade.providers()[-1]['available'] is True
    retained = setup.state['factory']
    assert setup.facade.providers()[-1]['available'] is True
    assert setup.state['factory'] == retained


def test_metadata_factory_and_preflight_cannot_run_inside_store_lock(setup):
    with setup.service.store.lock:
        assert setup.facade.providers()[-1]['available'] is False
        assert setup.facade.describe(setup.source)['available'] is False
        with pytest.raises(PermissionError):
            setup.facade.preflight(setup.source)
    assert setup.state['factory'] == 0


def test_source_provider_generation_and_subscription_selection_use_persisted_rows(setup):
    altered = {**setup.source, 'provider': LOCAL}
    assert setup.facade.describe(altered)['test_supported'] is False
    assert setup.facade.ready(setup.source['id'], '0' * 32) is False
    assert setup.facade.current(setup.source['id'], '0' * 32) is False
    assert setup.facade.pending({**setup.sub, 'source_id': setup.local_source['id']}) is None
    with pytest.raises(PermissionError):
        setup.facade.send(OWNER, {**setup.sub, 'source_id': setup.local_source['id']}, {})
    assert not any(name.startswith('local-') for name, *_ in setup.state['calls'])


def test_synthetic_provider_still_uses_only_existing_local_sender(setup):
    assert setup.facade.describe(setup.local_source)['test_supported'] is True
    assert setup.facade.ready(setup.local_source['id'], setup.local_source['generation']) is True
    assert setup.facade.pending(setup.local_sub)['sample_id'] == 'synthetic'
    assert setup.facade.send(OWNER, setup.local_sub, {})['status'] == 'queued'
    assert setup.facade.register(OWNER, {'kind': 'webhook', 'provider': LOCAL}) == setup.local_source
    assert setup.state['factory'] == 0


@pytest.mark.parametrize('allowed', [False, 1, 'yes'])
def test_preflight_and_readiness_require_strict_actual_true(setup, allowed):
    setup.state['available'] = allowed
    with pytest.raises(PermissionError):
        setup.facade.preflight(setup.source)
    setup.state['current'] = allowed
    assert setup.facade.ready(setup.source['id'], setup.source['generation']) is False
    assert setup.facade.current(setup.source['id'], setup.source['generation']) is False


def test_actual_owner_required_before_registration_or_action_lazy_factory(setup):
    setup.state['owner'] = False
    with pytest.raises(PermissionError):
        setup.facade.register(OWNER, {'kind': 'webhook', 'provider': PRODUCER})
    with pytest.raises(PermissionError):
        setup.facade.route(handler('POST'), setup.service, OWNER, [setup.source['id'], 'connect'])
    with pytest.raises(PermissionError):
        setup.facade.send(OWNER, setup.local_sub, {})
    assert setup.state['factory'] == 0


def test_unknown_provider_and_arbitrary_route_actions_do_not_create_runtime(setup):
    with pytest.raises(ValueError):
        setup.facade.register(OWNER, {'kind': 'webhook', 'provider': 'arbitrary', 'url': 'https://invalid.invalid'})
    assert setup.facade.route(handler('POST'), setup.service, OWNER, [setup.source['id'], 'execute']) is False
    assert setup.facade.route(handler('POST'), setup.service, OWNER, [setup.source['id'], 'connect', 'extra']) is False
    assert setup.state['factory'] == 0


@pytest.mark.parametrize('action', ['connect', 'rotate', 'revoke', 'connection-status'])
def test_fixed_routes_owner_check_actual_source_and_project_private_state(setup, action):
    body = {'revision': 1, 'idempotency_key': 'action'}
    request = handler('POST', body)
    assert setup.facade.route(request, setup.service, OWNER, [setup.source['id'], action])
    assert ('action', setup.source['id'], action, body) in setup.state['calls']
    public = request.result[0][1]['source']
    assert public['id'] == setup.source['id'] and public['test_supported'] is False
    assert not {'scope', 'owner', 'generation', 'selection_epoch', 'binding'} & set(public)
    assert setup.state['factory'] == 1


def test_route_scope_service_and_nonsoftware_action_never_fall_back(setup):
    with pytest.raises(PermissionError):
        setup.facade.route(handler(), SimpleNamespace(), OWNER, [setup.source['id']])
    with pytest.raises(ValueError):
        setup.facade.route(handler('POST'), setup.service, OWNER, [setup.local_source['id'], 'connect'])
    with pytest.raises(PermissionError):
        setup.facade.describe({**setup.source, 'scope': 'different'})
    assert setup.state['factory'] == 0


def test_retained_runtime_context_change_never_replaces_or_reports_stale_offers(setup):
    assert setup.facade.providers()[-1]['available'] is True
    setup.state['context']['pool'] = '0' * 64
    assert setup.facade.providers()[-1]['available'] is False
    assert setup.facade.describe(setup.source)['available'] is False
    assert setup.facade.ready(setup.source['id'], setup.source['generation']) is False
    assert setup.state['factory'] == 1


def test_context_change_during_provider_callback_cannot_report_available(setup):
    setup.state['during'] = lambda: setup.state['context'].update(pool='0' * 64)
    assert setup.facade.providers()[-1]['available'] is False
    assert setup.state['factory'] == 1


def test_close_is_once_terminal_and_never_constructs_or_adopts(setup):
    setup.facade.providers()
    setup.facade.close()
    setup.facade.close()
    assert setup.state['closed'] == 1
    assert setup.facade.ready(setup.source['id'], setup.source['generation']) is False
    with pytest.raises(PermissionError):
        setup.facade.providers()
    assert setup.state['factory'] == 1


def test_real_weak_origin_is_denied_before_metadata_factory(request):
    app, cookie, _ = request.getfixturevalue('served')
    assert app.call('/ui/studio/sources', cookie=cookie)[0] == 200
    service = app.ui.studio_streams
    attempts = []
    def forbidden():
        attempts.append(True)
        pytest.fail('Weak session reached runtime construction')
    app.ui.studio_webhook_setup = Setup(app.ui, service, app.ui.studio_webhook_setup, forbidden)
    weak = app.ui.sessions.open('weak-origin', 'token')
    assert app.call('/ui/studio/sources', cookie='poolhouse_ui=' + weak.sid)[0] == 403
    assert attempts == []


def test_concurrent_metadata_initializes_one_retained_runtime(setup):
    barrier = threading.Barrier(2)
    results = []
    def metadata():
        barrier.wait()
        results.append(setup.facade.providers()[-1]['available'])
    threads = [threading.Thread(target=metadata) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [True, True] and setup.state['factory'] == 1


@pytest.mark.parametrize('nested', [False, True])
def test_provider_metadata_never_exports_unknown_private_fields(setup, nested):
    value = {'id': PRODUCER, 'label': 'Software', 'available': True, 'requirements': [], 'offers': []}
    if nested:
        value['offers'] = [{'id': 'a' * 32, 'revision': 1, 'label': 'Software', 'device_label': 'Device',
                            'software_label': 'Software', 'available': True, 'reason': '', 'secret': 'private'}]
    else:
        value['secret'] = 'private'
    setup.runtime.providers = lambda: value
    public = setup.facade.providers()[-1]
    assert public['available'] is False and 'private' not in str(public)


def test_describe_cannot_substitute_a_different_persisted_source(setup):
    setup.runtime.describe = lambda row: {**row, 'id': 'f' * 32, 'available': True}
    actual = setup.facade.describe(setup.source)
    assert actual['id'] == setup.source['id'] and actual['available'] is False


def test_route_uses_shared_nested_public_projection_for_runtime_description(setup):
    def describe(row):
        return {**row, 'available': True, 'binding': {'secret': 'private-source-capability'},
                'connection': {'state': 'ready', 'reason': '', 'can_revoke': True, 'secret': 'private',
                               'producer': {'id': 'a' * 32, 'label': 'Software', 'secret': 'private'},
                               'pending': {'idempotency_key': 'command', 'secret': 'private'}}}
    setup.runtime.describe = describe
    request = handler()
    assert setup.facade.route(request, setup.service, OWNER, [setup.source['id']])
    source = request.result[0][1]['source']
    assert source['revision'] == 1 and source['connection']['can_revoke'] is True
    assert source['connection']['pending'] == {'idempotency_key': 'command'}
    assert 'private' not in str(source)


@pytest.mark.parametrize('availability', [1, 'yes', None])
def test_description_cannot_promote_truthy_unproved_availability(setup, availability):
    setup.runtime.describe = lambda row: {**row, 'available': availability}
    assert setup.facade.describe(setup.source)['available'] is False


def test_preflight_unavailable_factory_never_exports_private_error_details(setup):
    setup.state['failure'] = True
    with pytest.raises(PermissionError, match='Connect the current software producer') as error:
        setup.facade.preflight(setup.source)
    assert 'private' not in str(error.value)
    assert setup.facade.current(setup.source['id'], setup.source['generation']) is False
