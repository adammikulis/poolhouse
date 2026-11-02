"""Fixed record nodes retain typed branch integrity through prepare, cache and dispatch."""
from types import SimpleNamespace

import pytest

from poolhouse.studio import engine as scheduler
from poolhouse.studio.adapters import Adapters
from poolhouse.studio.catalog import node_spec
from poolhouse.studio.contracts import validate_graph
from poolhouse.studio.store import StudioStore, digest
from poolhouse.studio.worker import execute

PRINCIPAL = {'id': 'record-session', 'scope': 'record-scope', 'owner': True}


def graph():
    return {'nodes': [
        {'id': 'input', 'type': 'stream.record', 'parameters': {'sample': '{"value":2,"private":"omit"}'}},
        {'id': 'map', 'type': 'record.map', 'parameters': {'controls': {'keep_other': False, 'assignments': [
            {'target': 'score', 'source': {'field': 'value'}}]}}},
        {'id': 'condition', 'type': 'record.if', 'parameters': {'controls': {'field': 'score', 'operator': 'gt', 'value': 1}}},
        {'id': 'yes', 'type': 'prompt', 'parameters': {'template': 'selected'}},
        {'id': 'no', 'type': 'prompt', 'parameters': {'template': 'unselected'}}],
        'edges': [{'source': {'node': 'input', 'port': 'record'}, 'target': {'node': 'map', 'port': 'record'}},
                  {'source': {'node': 'map', 'port': 'record'}, 'target': {'node': 'condition', 'port': 'record'}}],
        'controls': [{'source': {'node': 'condition', 'port': 'true'}, 'target': {'node': 'yes'}},
                     {'source': {'node': 'condition', 'port': 'false'}, 'target': {'node': 'no'}}]}


@pytest.fixture
def setup(tmp_path):
    store = StudioStore(tmp_path / 'records.ladybug')
    runner = SimpleNamespace(files_root=tmp_path / 'files', environment=None, jobs={})
    engine = scheduler.WorkflowEngine(store, runner, Adapters(runner), lambda *args: True)
    engine.close()
    try:
        yield store, engine, runner
    finally:
        store.close()


def run(setup, key='first'):
    store, engine, _ = setup
    workflow = store.save_workflow(PRINCIPAL['scope'], 'Typed records', graph())
    return engine.advance(engine.submit(PRINCIPAL, workflow, [], key))


def test_actual_nodes_map_then_choose_one_branch_without_compute_and_cache(setup):
    first = run(setup)
    assert first['state'] == 'done'
    assert first['nodes']['map']['outputs'] == {'record': {'score': 2}}
    assert first['nodes']['condition']['outputs']['record'] == {'score': 2}
    assert first['nodes']['yes']['state'] == 'done'
    assert first['nodes']['no']['state'] == 'skipped'
    second = run(setup, 'second')
    assert second['nodes']['condition']['reused_from'] == first['id']
    assert second['nodes']['no']['state'] == 'skipped'
    assert setup[2].jobs == {}


def test_defaults_remain_typed_and_isolated():
    value = {'nodes': [{'id': 'record', 'type': 'stream.record'}, {'id': 'map', 'type': 'record.map'},
                       {'id': 'if', 'type': 'record.if'}], 'edges': [
        {'source': {'node': 'record', 'port': 'record'}, 'target': {'node': 'map', 'port': 'record'}},
        {'source': {'node': 'map', 'port': 'record'}, 'target': {'node': 'if', 'port': 'record'}}]}
    first, second = validate_graph(value), validate_graph(value)
    assert first['valid'] and second['valid']
    first['graph']['nodes'][1]['parameters']['controls']['assignments'].append({'changed': True})
    assert second['graph']['nodes'][1]['parameters']['controls']['assignments'] == []
    assert second['graph']['nodes'][2]['parameters']['controls'] == {'field': 'value', 'operator': 'exists'}
    assert node_spec('record.map')['parameters']['properties']['controls']['default']['assignments'] == []


@pytest.mark.parametrize('controls', [[], '[]', {'keep_other': 1, 'assignments': []},
    {'keep_other': True, 'assignments': [], 'owner': True}])
def test_malformed_controls_refused_even_outside_selected_output_before_resources(setup, controls):
    store, engine, runner = setup
    value = graph()
    value['nodes'][1]['parameters']['controls'] = controls
    value['nodes'].append({'id': 'alone', 'type': 'prompt', 'parameters': {'template': 'independent'}})
    workflow = store.save_workflow(PRINCIPAL['scope'], 'Invalid controls', value)
    def forbidden(*args):
        raise AssertionError('Malformed controls must fail before resource preparation')
    engine.adapters.validate_resources = forbidden
    with pytest.raises(ValueError):
        engine.submit(PRINCIPAL, workflow, ['alone'], 'bad-controls')
    assert engine._all_runs() == []
    assert runner.jobs == {}


@pytest.mark.parametrize('kind,controls', [
    ('record.map', {'keep_other': False, 'assignments': [{'target': 'x', 'source': {'field': 'score'}}]}),
    ('record.if', {'field': 'score', 'operator': 'exists'})])
def test_fixed_worker_matches_immediate_semantics(setup, tmp_path, kind, controls):
    node = {'id': 'node', 'type': kind, 'parameters': {'controls': controls}}
    inputs = {'record': {'score': 2}}
    assert execute(node, inputs, tmp_path) == setup[1].adapters.immediate(node, inputs, {})


@pytest.mark.parametrize('mutation', ['both', 'neither', 'wrong_reason', 'nonbool', 'extra'])
def test_cached_invalid_gate_sets_never_release_a_branch(setup, mutation):
    store, _engine, _ = setup
    first = run(setup)
    key = 'studio.cache:' + digest({'principal': PRINCIPAL, 'key': first['nodes']['condition']['fingerprint']})
    row = store.graph.get_doc(key)
    values = row['outputs']
    if mutation == 'both':
        values['false']['allow'] = True
    elif mutation == 'neither':
        values['true']['allow'] = False
    elif mutation == 'wrong_reason':
        values['true']['reason'] = values['false']['reason'] = 'allow'
    elif mutation == 'nonbool':
        values['true']['allow'] = 1
    else:
        values['true']['owner'] = True
    store.graph.put_doc(key, row)
    result = run(setup, 'after-cache-change')
    assert result['nodes']['condition']['state'] == 'failed'
    assert result['nodes']['yes']['state'] == 'blocked'
    assert result['nodes']['no']['state'] == 'blocked'


def test_if_cannot_reinterpret_decision_gate_names():
    output = {'true': {'allow': True, 'reason': 'true'}, 'false': {'allow': False, 'reason': 'true'}}
    assert scheduler._valid_gates({'type': 'record.if'}, output)
    forged = {name: {'allow': name == 'allow', 'reason': 'true'} for name in ('allow', 'review', 'block')}
    assert not scheduler._valid_gates({'type': 'decision.route'}, forged)


def test_resource_revocation_after_if_blocks_controlled_nodes(setup):
    _, engine, _ = setup
    original = engine.adapters.immediate
    def revoke(node, inputs, context):
        output = original(node, inputs, context)
        if node['type'] == 'record.if':
            engine.authorize = lambda principal, selected, inputs: selected is None or selected['id'] != 'input'
        return output
    engine.adapters.immediate = revoke
    result = run(setup)
    assert result['nodes']['condition']['state'] == 'done'
    assert result['nodes']['yes']['state'] == 'blocked'
    assert result['nodes']['no']['state'] == 'blocked'


def test_persisted_completed_gate_tamper_blocks_even_the_open_branch(setup):
    _, engine, _ = setup
    result = run(setup)
    result['nodes']['condition']['outputs']['false']['allow'] = True
    state = {'state': 'pending'}
    assert engine._controls(result, 'yes', state) is False
    assert state['state'] == 'blocked'


def test_multiple_if_controls_remain_conjunctive(setup):
    store, engine, _ = setup
    value = graph()
    value['nodes'].append({'id': 'second', 'type': 'record.if', 'parameters': {
        'controls': {'field': 'score', 'operator': 'lt', 'value': 3}}})
    value['edges'].append({'source': {'node': 'map', 'port': 'record'},
                          'target': {'node': 'second', 'port': 'record'}})
    value['controls'].append({'source': {'node': 'second', 'port': 'false'}, 'target': {'node': 'yes'}})
    workflow = store.save_workflow(PRINCIPAL['scope'], 'Conjunctive conditions', value)
    result = engine.advance(engine.submit(PRINCIPAL, workflow, [], 'conjunction'))
    # The second controller follows its target in stored order; the next scheduler tick settles it.
    result = engine.advance(result)
    assert result['state'] == 'done'
    assert result['nodes']['condition']['outputs']['true']['allow'] is True
    assert result['nodes']['second']['outputs']['true']['allow'] is True
    assert result['nodes']['yes']['state'] == 'skipped'
    assert result['nodes']['no']['state'] == 'skipped'
