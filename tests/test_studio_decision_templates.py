"""Portable synthetic metadata and model-backed finite template contracts."""
import json

from poolhouse.studio.contracts import validate_graph
from poolhouse.studio.decision_templates import example_record, sample_sets, templates
from poolhouse.studio.templates import templates as all_templates


def test_original_templates_remain_and_examples_have_explicit_models():
    graphs = all_templates()
    assert len(graphs) == 11
    for graph in templates():
        assert len([node for node in graph['nodes'] if node['type'] == 'model']) == 1
        record = next(node for node in graph['nodes'] if node['type'] == 'stream.record')
        assert isinstance(json.loads(record['parameters']['sample']), dict)
        assert len(graph['controls']) == 3
        assert {error['code'] for error in validate_graph(graph)['errors']} == {'binding'}
        assert all(not node['bindings'] for node in graph['nodes'])
        assert all(node['type'] not in {'tools.call', 'export'} for node in graph['nodes'])


def test_sample_selection_returns_independent_registered_records():
    sources = sample_sets()
    record = example_record(sources[0]['id'], 'sample')
    record['private'] = 'edited'
    sources[0]['samples'][0]['record']['private'] = 'edited'
    assert 'private' not in example_record(sources[0]['id'], 'sample')
