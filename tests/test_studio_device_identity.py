"""Device selection remains bound to its certificate and exact current pool.

The pool, its nodes and their compute service bindings are real (``service_kit``). The devices a dispatcher lists are the node's
authorized services as members, without the health request a live listing makes to each: no daemon listens at these endpoints.
"""

from threading import RLock
from types import SimpleNamespace

import pytest
from service_kit import node_binary, two_nodes  # noqa: F401  (fixtures)

from poolhouse.fleet import studio_dispatch, tls
from poolhouse.fleet.remote import Member
from poolhouse.fleet.service_pool import Pool
from poolhouse.fleet.studio_dispatch import DeviceDispatch


def authorized_members():
    own = tls.local().fingerprint
    return [Member.from_binding(row) for row in Pool().services() if row.fingerprint != own]


def test_same_claimed_machine_with_another_certificate_cannot_replace_bound_target(two_nodes, tmp_path, monkeypatch):  # noqa: F811
    two_nodes.serve(12344, 12345)
    monkeypatch.setattr(studio_dispatch, 'members', authorized_members)
    dispatcher = DeviceDispatch(SimpleNamespace(), None, lambda *args: True)
    try:
        selected = {'execution': {'machine': two_nodes.idb.fingerprint}}
        snapshot = dispatcher.identity(selected)
        assert dispatcher.member(snapshot).fingerprint == two_nodes.idb.fingerprint
        with pytest.raises(PermissionError):
            dispatcher.member({**snapshot, 'pool': '0' * 64})
        replacement = tls.identity(tmp_path / 'replacement-tls', 'device-replacement')
        two_nodes.bind(two_nodes.b, replacement, 'https://127.0.0.1:12345')
        two_nodes.a.call('sync_now', token=two_nodes.a.token)
        with pytest.raises(PermissionError):
            dispatcher.member(snapshot)
    finally:
        dispatcher.close()


def test_origin_callback_rechecks_current_predecessor_resource_authority(two_nodes, monkeypatch):  # noqa: F811
    two_nodes.serve(12344, 12345)
    monkeypatch.setattr(studio_dispatch, 'members', authorized_members)
    certificate = two_nodes.idb.fingerprint
    calls, allowed = [], [True]

    def authorize(principal, node, inputs):
        calls.append(inputs)
        return allowed[0]

    dispatcher = DeviceDispatch(SimpleNamespace(), None, authorize)
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
