"""Native selection proofs never replace the current source registry's authorization."""
import copy
import io
import json
import threading
from types import SimpleNamespace

import pytest

from poolhouse.fleet.native_picks import NativePicks
from poolhouse.fleet.studio_native_picks import NATIVE, NativePickRoutes
from poolhouse.studio.store import Conflict


class Store:
    def __init__(self):
        self.purpose = "studio-source"
        self.lock = threading.RLock()
        self.rows = {}

    def read(self, ident):
        return copy.deepcopy(self.rows.get(ident))

    def write(self, row):
        self.rows[row['id']] = copy.deepcopy(row)
        return copy.deepcopy(row)


@pytest.fixture
def picker():
    store = Store()
    principal = {'id': 'interactive', 'scope': 'pool-device-root', 'owner': True}
    authority = {'epoch': 'current', 'live': True, 'now': 100.0}
    created = {}
    calls = []
    def create(owner, path, mode, request_id):
        calls.append((owner, path, mode, request_id))
        created.setdefault(request_id, {'id': 'watch:' + request_id, 'title': 'events.csv'})
        return created[request_id]
    callbacks = {'epoch': lambda owner: authority['epoch'],
                 'current': lambda owner, epoch: owner == principal and authority['live'] and epoch == authority['epoch'],
                 'create_resource': create, 'clock': lambda: authority['now']}
    return NativePicks(store, **callbacks), store, principal, authority, calls, callbacks


def selected(picker):
    picks, _, principal, *_ = picker
    request = picks.request(principal, 'file', 'a' * 32)
    first = picks.begin(request['request_id'], request['nonce'])
    return request, first


def test_request_begin_complete_are_idempotent_and_keep_private_data_private(picker):
    picks, store, principal, _, calls, _ = picker
    request, first = selected(picker)
    assert picks.request(principal, 'file', 'a' * 32) == request
    assert picks.begin(request['request_id'], request['nonce']) == {'status': 'choosing'}
    response = picks.complete(request['request_id'], request['nonce'], first['claim'], '/private/original/events.csv')
    assert picks.complete(request['request_id'], request['nonce'], first['claim'], '/private/original/events.csv') == response
    assert picks.begin(request['request_id'], request['nonce']) == response
    assert len(calls) == 1
    assert set(response) == {'status', 'source'} and set(response['source']) == {'id', 'title'}
    assert '/private' not in str(response)
    assert request['nonce'] not in str(store.rows) and first['claim'] not in str(store.rows)
    assert '/private' not in str(store.rows)


@pytest.mark.parametrize('change', ['session', 'epoch', 'expiry'])
def test_revoke_or_expire_between_choosing_and_completion(picker, change):
    picks, _, _, authority, calls, _ = picker
    request, first = selected(picker)
    if change == 'session':
        authority['live'] = False
    elif change == 'epoch':
        authority['epoch'] = 'revoked'
    else:
        authority['now'] += 12 * 3600
    with pytest.raises(PermissionError):
        picks.complete(request['request_id'], request['nonce'], first['claim'], '/private/events.csv')
    assert not calls


def test_cancellation_never_mints_a_source_or_reopens(picker):
    picks, _, _, _, calls, _ = picker
    request, first = selected(picker)
    args = request['request_id'], request['nonce'], first['claim']
    assert picks.cancel(*args) == picks.cancel(*args) == {'status': 'cancelled'}
    assert picks.begin(*args[:2]) == {'status': 'cancelled'}
    with pytest.raises(PermissionError):
        picks.complete(*args, '/private/events.csv')
    assert not calls


@pytest.mark.parametrize('field', ['nonce', 'claim', 'request_id'])
def test_forged_native_selection_proofs_are_refused(picker, field):
    picks, _, _, _, calls, _ = picker
    request, first = selected(picker)
    values = {**request, 'claim': first['claim'], field: 'b' * 32}
    with pytest.raises(PermissionError):
        picks.complete(values['request_id'], values['nonce'], values['claim'], '/private/events.csv')
    assert not calls


def test_changed_path_or_mode_is_not_an_idempotent_replay(picker):
    picks, _, principal, _, calls, _ = picker
    request, first = selected(picker)
    args = request['request_id'], request['nonce'], first['claim']
    picks.complete(*args, '/private/events.csv')
    with pytest.raises(Conflict):
        picks.complete(*args, '/private/other.csv')
    with pytest.raises(Conflict):
        picks.request(principal, 'folder', 'a' * 32)
    assert len(calls) == 1


def test_restart_never_resurrects_nonce_or_reopens_chooser(picker):
    _picks, store, principal, _, calls, callbacks = picker
    request, _ = selected(picker)
    restarted = NativePicks(store, **callbacks)
    with pytest.raises(PermissionError):
        restarted.begin(request['request_id'], request['nonce'])
    with pytest.raises(PermissionError):
        restarted.request(principal, 'file', 'a' * 32)
    assert restarted.request(principal, 'file', 'b' * 32)['request_id'] != request['request_id']
    assert not calls


def test_lost_registry_ack_only_reconciles_exact_selection(picker):
    picks, _, _, _, calls, callbacks = picker
    request, first = selected(picker)
    original = callbacks['create_resource']
    def uncertain(*args):
        original(*args)
        raise OSError('lost acknowledgement')
    picks.create_resource = uncertain
    args = request['request_id'], request['nonce'], first['claim']
    with pytest.raises(OSError):
        picks.complete(*args, '/private/events.csv')
    assert picks.begin(*args[:2]) == {'status': 'choosing'}
    with pytest.raises(Conflict):
        picks.complete(*args, '/private/different.csv')
    with pytest.raises(Conflict):
        picks.cancel(*args)
    picks.create_resource = original
    assert picks.complete(*args, '/private/events.csv')['status'] == 'complete'
    assert len({call[-1] for call in calls}) == 1


@pytest.mark.parametrize('path', ['relative/events.csv', '/private/../secret', '', '\x00'])
def test_no_path_fallback_or_traversal(picker, path):
    picks, _, _, _, calls, _ = picker
    request, first = selected(picker)
    with pytest.raises(ValueError):
        picks.complete(request['request_id'], request['nonce'], first['claim'], path)
    assert not calls


def test_registry_owns_descriptor_safety_without_a_permissive_fallback(picker):
    picks, _, _, _, calls, _ = picker
    request, first = selected(picker)
    def refuse(*args):
        raise PermissionError('symlink or revoked resource')
    picks.create_resource = refuse
    with pytest.raises(PermissionError):
        picks.complete(request['request_id'], request['nonce'], first['claim'], '/private/linked.csv')
    assert not calls


def handler(picker, *, secret='native-only', path=None, request=None, headers=None):
    picks, _, principal, *_ = picker
    body = json.dumps(request or {}).encode()
    replies = []
    fields = {'Content-Length': str(len(body)), 'X-Poolhouse-Launch': secret, **(headers or {})}
    context = SimpleNamespace(path=path or NATIVE + 'a' * 32 + '/begin', method='POST', client_ip='127.0.0.1',
                              host_header='127.0.0.1:8770', header=lambda name, default='': fields.get(name, default),
                              handler=SimpleNamespace(rfile=io.BytesIO(body)),
                              ui=SimpleNamespace(launch=SimpleNamespace(matches=lambda value: value == 'native-only'),
                                                 host_ok=lambda host: host == '127.0.0.1:8770', studio_native_picks=picks),
                              _service=lambda: (SimpleNamespace(authorize=lambda *args: True), principal),
                              send=lambda status, value, *args: replies.append((status, value)))
    return context, replies


@pytest.mark.parametrize('headers', [{'Origin': 'http://127.0.0.1:8770'}, {'Sec-Fetch-Site': 'same-origin'}, {'X-Poolhouse-Launch': 'forged'}])
def test_native_endpoint_never_accepts_browser_headers_or_forged_secret(picker, headers):
    context, replies = handler(picker, headers=headers)
    assert NativePickRoutes.public_route(context)
    assert replies[0][0] == 403


def test_authenticated_picker_request_and_private_callback_route(picker):
    context, replies = handler(picker, path='/ui/studio/sources/pick', request={'mode': 'folder', 'request_key': 'a' * 32})
    assert NativePickRoutes.route(context)
    request = replies[0][1]
    context, replies = handler(picker, path=NATIVE + request['request_id'] + '/begin', request={'nonce': request['nonce']})
    assert NativePickRoutes.public_route(context)
    assert replies[0][0] == 200 and replies[0][1]['mode'] == 'folder'


@pytest.mark.parametrize('extra', [{'mode': 'folder'}, {'path': '/private'}, {'url': 'https://invalid.example'}])
def test_native_endpoint_rejects_injected_selection_fields(picker, extra):
    request, _ = selected(picker)
    context, replies = handler(picker, path=NATIVE + request['request_id'] + '/begin', request={'nonce': request['nonce'], **extra})
    assert NativePickRoutes.public_route(context)
    assert replies[0][0] == 409


def test_missing_registry_keeps_native_and_browser_routes_inert(picker):
    context, replies = handler(picker)
    del context.ui.studio_native_picks
    assert NativePickRoutes.public_route(context)
    assert replies[0][0] == 409
    context.path = '/ui/studio/sources/pick'
    assert NativePickRoutes.route(context)
    assert replies[1][0] == 409


def test_concurrent_begin_only_returns_one_native_claim(picker):
    from concurrent.futures import ThreadPoolExecutor
    picks, _, principal, *_ = picker
    request = picks.request(principal, 'file', 'a' * 32)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: picks.begin(request['request_id'], request['nonce']), range(2)))
    assert sum('claim' in result for result in results) == 1


def test_duplicate_json_callback_fields_never_consume_the_request(picker):
    request, _ = selected(picker)
    raw = json.dumps({'nonce': request['nonce']})[:-1] + ',"nonce":"' + 'b' * 32 + '"}'
    context, replies = handler(picker, path=NATIVE + request['request_id'] + '/begin',
                               headers={'Content-Length': str(len(raw.encode()))})
    context.handler.rfile = io.BytesIO(raw.encode())
    assert NativePickRoutes.public_route(context)
    assert replies[0][0] == 409


def test_foreign_purpose_row_cannot_be_completed(picker):
    picks, store, *_ = picker
    request, claim = selected(picker)
    store.rows[request['request_id']]['purpose'] = 'coding-project'
    with pytest.raises(PermissionError):
        picks.complete(request['request_id'], request['nonce'], claim['claim'], '/private/events.csv')


def test_coding_folder_and_studio_requests_have_separate_identity(picker):
    studio, store, principal, _, _, callbacks = picker
    records = SimpleNamespace(purpose='coding-project', lock=store.lock, read=store.read, write=store.write)
    coding = NativePicks(records, **callbacks)
    key = 'b' * 32
    studio_request = studio.request(principal, 'folder', key)
    coding_request = coding.request(principal, 'folder', key)
    assert studio_request['request_id'] != coding_request['request_id']
    with pytest.raises(PermissionError):
        coding.begin(studio_request['request_id'], studio_request['nonce'])
    with pytest.raises(ValueError):
        coding.request(principal, 'file', 'c' * 32)
