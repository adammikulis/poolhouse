"""n8n imports retain topology without importing execution authority."""
import json

import pytest
from test_studio_engine import PRINCIPAL, setup  # noqa: F401

from poolhouse.studio.contracts import validate_graph
from poolhouse.studio.n8n import preview


def node(name='Start', kind='n8n-nodes-base.manualTrigger', **fields):
    return {'name': name, 'type': kind, 'typeVersion': 1, 'position': [100, 200], 'parameters': {}, **fields}


def workflow(nodes=None, connections=None, **fields):
    return {'name': 'Imported example', 'nodes': [node()] if nodes is None else nodes,
            'connections': connections or {}, **fields}


def translate(value):
    return preview(json.dumps(value))


def test_manual_trigger_is_finite_empty_record_and_each_draft_has_fresh_ids():
    first, second = translate(workflow()), translate(workflow())
    converted = first['graph']['nodes'][0]
    assert converted['type'] == 'stream.record' and converted['parameters'] == {'sample': '{}'}
    assert 'no live subscription' in first['nodes'][0]['message']
    assert converted['id'] != second['graph']['nodes'][0]['id']
    assert validate_graph(first['graph'])['valid']
    assert first['graph']['layout']['nodes'][converted['id']] == {'x': 20, 'y': 20}


def test_credentials_code_urls_history_and_source_ids_never_enter_translation():
    value = workflow([node(id='discard-source-id'), node('Remote', 'n8n-nodes-base.httpRequest',
        parameters={'url': 'https://secret.invalid/private', 'headers': {'Authorization': 'omit-header-secret'},
                    'jsCode': 'omit-code-secret'}, credentials={'secret': {'name': 'omit-credential-secret'}})],
        {'Start': {'main': [[{'node': 'Remote', 'type': 'main', 'index': 0}]]}},
        pinData={'secret': 'omit-pinned-secret'}, staticData={'secret': 'omit-static-secret'}, active=True)
    translated = translate(value)
    encoded = json.dumps(translated)
    assert not any(secret in encoded for secret in ('secret.invalid', 'omit-', 'discard-source-id', 'Authorization', 'jsCode'))
    assert [node['status'] for node in translated['nodes']] == ['translated', 'blocked']
    assert len(translated['graph']['edges']) == 1
    assert translated['graph']['edges'][0]['source']['port'] == 'record'
    assert translated['graph']['edges'][0]['target']['port'] == 'main_0'
    assert not validate_graph(translated['graph'])['valid']


def test_ollama_resource_requires_current_poolhouse_binding_and_keeps_ai_topology():
    value = workflow([node('Local model', '@n8n/n8n-nodes-langchain.lmChatOllama',
        parameters={'model': 'unimported-model-name', 'options': {}}, credentials={'private': 'discard'}),
        node('Chain', '@n8n/n8n-nodes-langchain.chainLlm', typeVersion=1.7)],
        {'Local model': {'ai_languageModel': [[{'node': 'Chain', 'type': 'ai_languageModel', 'index': 0}]]}})
    result = translate(value)
    assert result['graph']['nodes'][0]['type'] == 'model'
    assert result['graph']['nodes'][0]['bindings'] == {}
    assert 'unimported-model-name' not in json.dumps(result)
    assert result['graph']['edges'][0]['source']['port'] == 'model'
    assert result['graph']['edges'][0]['target']['port'] == 'ai_languageModel_0'
    assert not validate_graph(result['graph'])['valid']


@pytest.mark.parametrize('fields', [
    {'typeVersion': 2}, {'parameters': {'expression': '={{ $json.secret }}'}},
    {'disabled': True}, {'executeOnce': True}, {'retryOnFail': True},
    {'continueOnFail': True}, {'disabled': 'stopWorkflow'}, {'alwaysOutputData': True}, {'onError': 'continueRegularOutput'},
])
def test_unsupported_options_and_versions_are_blocked(fields):
    result = translate(workflow([node(**fields)]))
    assert result['nodes'][0]['status'] == 'blocked'
    assert not validate_graph(result['graph'])['valid']


@pytest.mark.parametrize('parameters', [{'model': '=environment'}, {'model': '{{ secret }}'},
                                       {'model': 'example', 'options': {'temperature': 0.2}},
                                       *({'model': 'example', 'options': value} for value in [False, 0, [], ''])])
def test_provider_expressions_and_options_are_not_silently_dropped(parameters):
    result = translate(workflow([node(kind='@n8n/n8n-nodes-langchain.lmChatOllama', parameters=parameters)]))
    assert result['nodes'][0]['status'] == 'blocked'


@pytest.mark.parametrize('value', [
    '[]', '{"nodes":[],"connections":{}}', '{"nodes":[],"nodes":[],"connections":{}}',
    '{"nodes":[],"connections":{},"__proto__":{}}',
    '{"nodes":[],"connections":{},"nested":{"constructor":{}}}',
    '{"nodes":[],"connections":{},"value":NaN}',
])
def test_malformed_duplicate_and_hostile_json_is_rejected(value):
    with pytest.raises(ValueError):
        preview(value)


@pytest.mark.parametrize('change', ['duplicate', 'dangling', 'cycle', 'index', 'coordinate', 'depth', 'oversize'])
def test_ambiguous_or_unbounded_graphs_are_rejected(change):
    value = workflow([node(), node('Other')], {'Start': {'main': [[{'node': 'Other', 'type': 'main', 'index': 0}]]}})
    if change == 'duplicate':
        value['nodes'][1]['name'] = 'Start'
    if change == 'dangling':
        value['connections']['Start']['main'][0][0]['node'] = 'Missing'
    if change == 'cycle':
        value['connections']['Other'] = {'main': [[{'node': 'Start', 'type': 'main', 'index': 0}]]}
    if change == 'index':
        value['connections']['Start']['main'][0][0]['index'] = True
    if change == 'coordinate':
        value['nodes'][0]['position'][0] = 10**20
    if change == 'depth':
        nested = {}
        value['ignored'] = nested
        for _ in range(20):
            nested['child'] = {}
            nested = nested['child']
    if change == 'oversize':
        value['ignored'] = 'x' * 2_000_000
    with pytest.raises(ValueError):
        translate(value)


def test_unsupported_disconnected_node_blocks_selected_output_before_dispatch(setup):  # noqa: F811
    store, engine, runner = setup
    result = translate(workflow([node(), node('Code', 'n8n-nodes-base.code')]))
    saved = store.save_workflow(PRINCIPAL['scope'], result['title'], result['graph'])
    with pytest.raises(ValueError, match='Unknown node version'):
        engine.submit(PRINCIPAL, saved, [result['graph']['nodes'][0]['id']], 'explicit-selected')
    assert store.records('run', PRINCIPAL['scope']) == []
    assert runner.jobs == {}


def test_nonstring_target_and_excessive_distinct_ports_are_rejected():
    value = workflow([node(), node('Target')], {'Start': {'main': [[{'node': [], 'type': 'main', 'index': 0}]]}})
    with pytest.raises(ValueError, match='target'):
        translate(value)
    value['connections']['Start'] = {f'channel{i}': [[{'node': 'Target', 'type': 'main', 'index': 0}]] for i in range(129)}
    with pytest.raises(ValueError, match='ports'):
        translate(value)
