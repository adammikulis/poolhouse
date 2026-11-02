"""Explicit outbound value ports retain group/sweep meaning and exclude other data."""
import pytest

from poolhouse.studio.contracts import resolve_output_ports, validate_graph
from poolhouse.studio.streams import Streams


def graph():
    return {'nodes': [{'id': 'record', 'type': 'stream.record'},
                      {'id': 'model', 'type': 'model', 'bindings': {'model': {'kind': 'model', 'id': 'installed'}}},
                      {'id': 'guard', 'type': 'guard.injection', 'parameters': {'trusted_task': 'Summarize data'}}],
            'edges': [{'source': {'node': node, 'port': node}, 'target': {'node': 'guard', 'port': node}} for node in ('record', 'model')]}


def expanded(raw):
    result = validate_graph(raw)
    assert result['valid'], result['errors']
    return result['graph']


def test_only_explicit_guard_signal_is_delivered_without_original_record():
    raw = graph()
    ports = resolve_output_ports(raw, expanded(raw), [{'node': 'guard', 'port': 'signal'}])
    run = {'nodes': {'guard': {'state': 'done', 'outputs': {'signal': {'outcome': 'review'}, 'record': {'private': 'data'}}}}}
    body = Streams._delivery_body(run, {'id': 'event', 'sequence': 1}, ports)
    assert body['outputs'] == [{'node': 'guard', 'port': 'signal', 'value': {'outcome': 'review'}}]
    assert 'private' not in str(body)


def test_group_interface_and_sweep_resolve_exact_declared_ports():
    inner = graph()
    raw = {'nodes': [{'id': 'bundle', 'type': 'group', 'group': 'check'}], 'edges': [],
           'groups': {'check': {**inner, 'outputs': {'risk': {'node': 'guard', 'port': 'signal'}}}}}
    assert resolve_output_ports(raw, expanded(raw), [{'node': 'bundle', 'port': 'risk'}]) == [{'node': 'bundle/guard', 'port': 'signal'}]
    raw = graph()
    raw['nodes'][2]['sweep'] = {'min_margin': [0.1, 0.2]}
    ports = resolve_output_ports(raw, expanded(raw), [{'node': 'guard', 'port': 'signal'}])
    assert ports == [{'node': 'guard/sweep/0', 'port': 'signal'}, {'node': 'guard/sweep/1', 'port': 'signal'}]
    assert resolve_output_ports(raw, expanded(raw), ports) == ports


@pytest.mark.parametrize('ports', [[], [{'node': 'guard', 'port': 'missing'}], [{'node': 'missing', 'port': 'signal'}],
                                  [{'node': 'model', 'port': 'model'}], [{'node': 'guard', 'port': 'signal', 'path': 'private'}],
                                  [{'node': 'guard', 'port': 'signal'}, {'node': 'guard', 'port': 'signal'}]])
def test_missing_resource_unknown_or_duplicate_ports_refuse(ports):
    raw = graph()
    with pytest.raises(ValueError):
        resolve_output_ports(raw, expanded(raw), ports)


def test_conditional_skip_never_builds_partial_or_guessed_sink_values():
    run = {'nodes': {'guard': {'state': 'done', 'outputs': {'signal': {'private': 'not sent'}}},
                     'downstream': {'state': 'skipped', 'outputs': {}}}}
    ports = [{'node': 'guard', 'port': 'signal'}, {'node': 'downstream', 'port': 'response'}]
    body = Streams._delivery_body(run, {'id': 'event', 'sequence': 1}, ports)
    assert body['outcome'] == 'skipped'
    assert body['outputs'] == [{'node': 'guard', 'port': 'signal', 'state': 'done'},
                               {'node': 'downstream', 'port': 'response', 'state': 'skipped'}]
    assert 'private' not in str(body) and 'value' not in str(body)
