"""Finite decision examples through real Studio routes, with no native compute."""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
from browser_expect import expect
from test_fleet_ui import Serving
from test_studio_canvas_browser import studio_browser  # noqa: F401

from poolhouse.studio.adapters import Adapters
from poolhouse.studio.contracts import validate_graph
from poolhouse.studio.decisions import assess, parse_record, route

pytestmark = pytest.mark.slow
TITLES = ['Fraud screening', 'Prompt injection check', 'Retrieved-source check', 'Support triage', 'Document routing']


@pytest.fixture
def decision_page(tmp_path, request):
    open_page = request.getfixturevalue('studio_browser')
    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    served.ui.root = tmp_path / 'traind'
    try:
        with open_page() as page:
            errors, submissions = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda req: submissions.append(req.post_data_json) if req.method == 'POST' and req.url.endswith('/ui/studio/runs') else None)
            page.goto(f'http://127.0.0.1:{served.port}/ui/')
            yield page, errors, submissions
    finally:
        if hasattr(served.ui, 'studio'):
            served.ui.studio.close()
        served.close()


def choose_template(page, title):
    page.locator('.studio-library').get_by_role('button', name=title, exact=False).click()
    expect(page.get_by_label('Workflow title')).to_have_value(title)


def focus_node(page, node_id):
    page.locator(f'.studio-node[data-node-id="{node_id}"]').focus()
    page.keyboard.press('Enter')


def test_five_examples_preserve_lifecycle_and_require_explicit_model(decision_page):
    page, errors, submissions = decision_page
    for title in TITLES:
        choose_template(page, title)
        expect(page.locator('.studio-node[data-node-id="model"]')).to_contain_text('Choose an installed model')
        focus_node(page, 'record')
        record = page.locator('.studio-inspector').get_by_label('Sample record JSON', exact=True)
        assert isinstance(json.loads(record.input_value()), dict)
        expect(page.locator('.studio-connections [data-control]')).to_have_count(3)
    page.get_by_role('button', name='Run workflow', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('model')
    assert submissions == []
    expect(page.locator('.studio-library').get_by_role('button', name=re.compile('fine tuning and evaluation', re.IGNORECASE))).to_be_visible()
    assert not errors


def test_record_draft_is_inert_and_blocks_stale_submission(decision_page):
    page, errors, submissions = decision_page
    choose_template(page, 'Prompt injection check')
    focus_node(page, 'record')
    input_box = page.locator('.studio-inspector').get_by_label('Sample record JSON', exact=True)
    input_box.fill('{"prompt":')
    focus_node(page, 'model')
    page.get_by_role('button', name='Run workflow', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('Correct the sample record JSON')
    input_box = page.locator('.studio-inspector').get_by_label('Sample record JSON', exact=True)
    expect(input_box).to_have_value('{"prompt":')
    input_box.fill('{"prompt":"<img src=x onerror=window.injected=true>"}')
    input_box.press('Tab')
    expect(page.locator('.studio-status')).to_contain_text('Saved')
    assert page.evaluate('window.injected') is None
    assert submissions == []
    assert not errors


@pytest.fixture
def synthetic_model(monkeypatch):
    """Fixed test scores exercise actual gate contracts without loading a model."""
    from poolhouse import hub
    monkeypatch.setattr(hub, 'discover', lambda **kwargs: [] if kwargs else [SimpleNamespace(id='test-decision-model')])
    monkeypatch.setattr(Adapters, 'availability', lambda self, nodes: {})
    monkeypatch.setattr(Adapters, 'validate', lambda self, nodes: [])
    principals = []
    def allow_current_resource(self, graph, context):
        assert set(context) == {'principal'}
        principal = context['principal']
        assert principal['owner'] is True
        assert re.fullmatch('[0-9a-f]{64}', principal['id'])
        assert re.fullmatch('[0-9a-f]{64}', principal['scope'])
        assert all(node['bindings']['model']['id'] == 'test-decision-model' for node in graph['nodes'] if node['type'] == 'model')
        if principals:
            assert principal == principals[-1]
        principals.append(principal)
        return []
    monkeypatch.setattr(Adapters, 'validate_resources', allow_current_resource)
    monkeypatch.setattr(Adapters, 'dependency_fingerprint', lambda self, node: '')
    previous = Adapters.immediate
    monkeypatch.setattr(Adapters, 'immediate', lambda self, node, inputs, context: {'model': {'kind': 'model', 'id': 'test-decision-model'}} if node['type'] == 'model' else previous(self, node, inputs, context))
    monkeypatch.setattr(Adapters, 'supports_authorized', lambda self, node: True)
    calls = []
    def execute(self, node, inputs, context):
        assert context['principal'] == principals[-1]
        calls.append(node['id'])
        if node['type'] == 'stream.record':
            return {'record': parse_record(node['parameters']['sample'])}
        if node['type'] == 'guard.injection':
            return assess(node['type'], node['parameters'], inputs['record'], lambda *args: {'scores': {'yes': 0.98, 'no': 0.02}}, identity={'model_digest': 'test-model', 'calibration_digest': 'unverified'})
        if node['type'] == 'decision.route':
            return route(node['parameters'], inputs['decision'])
        if node['type'] == 'report':
            return {'artifact': {'report': inputs['report']}}
        raise AssertionError(node['type'])
    monkeypatch.setattr(Adapters, 'execute_authorized', execute)
    return calls


def test_guard_blocks_branch_and_partial_copy_export_keeps_gate(synthetic_model, decision_page):
    page, errors, submissions = decision_page
    choose_template(page, 'Prompt injection check')
    focus_node(page, 'model')
    page.get_by_label('Choose model', exact=True).select_option('test-decision-model')
    page.get_by_role('button', name='Run workflow', exact=True).click()
    expect(page.locator('#studio-runs')).to_contain_text('done', timeout=10000)
    expect(page.locator('.studio-node[data-node-id="allow"]')).to_contain_text('skipped')
    expect(page.locator('.studio-node[data-node-id="block"]')).to_contain_text('done')
    assert 'allow' not in synthetic_model and 'review' not in synthetic_model
    assert 'block' in synthetic_model
    assert len(submissions) == 1
    focus_node(page, 'block')
    page.get_by_role('button', name='Copy', exact=True).click()
    page.get_by_role('button', name='Paste', exact=True).click()
    copied = page.locator('studio-view').evaluate('editor => editor.clipboard')
    assert {node['id'] for node in copied['nodes']} == {'record', 'model', 'assess', 'block'}
    assert copied['controls'] == [{'source': {'node': 'assess', 'port': 'block'}, 'target': {'node': 'block'}}]
    focus_node(page, 'block')
    page.get_by_role('button', name='Share', exact=True).click()
    page.get_by_label('Package content').select_option('selection')
    exported = page.locator('studio-view').evaluate('editor => editor.sharingGraph()')
    assert {node['id'] for node in exported['nodes']} == {'record', 'model', 'assess', 'block'}
    assert exported['controls'] == copied['controls']
    with page.expect_download() as downloaded:
        page.get_by_role('button', name='Export file', exact=True).click()
    package = json.loads(downloaded.value.path().read_text())
    assert package['graph']['controls'] == copied['controls']
    assert {node['id'] for node in package['graph']['nodes']} == {'record', 'model', 'assess', 'block'}
    assert all(not node['bindings'] for node in package['graph']['nodes'])
    focus_node(page, 'assess')
    page.get_by_role('button', name='Delete', exact=True).click()
    focus_node(page, 'block')
    page.get_by_role('button', name='Share', exact=True).click()
    page.get_by_label('Package content').select_option('selection')
    unresolved = page.locator('studio-view').evaluate('editor => editor.sharingGraph()')
    assert unresolved['controls'] == copied['controls']
    assert not validate_graph(unresolved)['valid']
    assert not errors


def test_group_required_control_survives_nested_edit_copy_and_export(synthetic_model, decision_page):
    page, errors, _submissions = decision_page
    choose_template(page, 'Prompt injection check')
    focus_node(page, 'block')
    page.get_by_role('button', name='Group selection', exact=True).click()
    group = page.locator('.studio-node').filter(has=page.get_by_role('button', name='Open group'))
    expect(group).to_have_count(1)
    group_id = group.get_attribute('data-node-id')
    definition = page.locator('studio-view').evaluate('editor => editor.graph.groups[editor.graph.nodes.find(n => n.type === "group").group]')
    assert definition['control_inputs'] == {'block': [{'node': 'block'}]}
    focus_node(page, group_id)
    expect(page.get_by_label('Required branch block', exact=True)).to_have_value('{"node":"assess","port":"block"}')
    # A blanket condition cannot replace the required precise condition.
    page.get_by_label('Required branch block', exact=True).select_option('')
    expect(group).to_contain_text('Connect required branch block')
    page.get_by_role('button', name='Unpack group', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('before unpacking this group')
    expect(group).to_have_count(1)
    page.get_by_label('Run whole group only when', exact=True).select_option('{"node":"assess","port":"block"}')
    page.get_by_role('button', name='Run workflow', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('required group control block')
    page.get_by_label('Required branch block', exact=True).select_option('{"node":"assess","port":"block"}')
    page.get_by_role('button', name='Remove branch block', exact=True).first.click()
    group.get_by_role('button', name='Open group').click()
    focus_node(page, 'block')
    page.get_by_role('button', name='Group selection', exact=True).click()
    nested = page.locator('.studio-node')
    nested_id = nested.get_attribute('data-node-id')
    nested_definition = page.locator('studio-view').evaluate('editor => editor.graph.groups[editor.graph.nodes[0].group]')
    assert nested_definition['control_inputs'] == {'block': [{'node': 'block'}]}
    forwarded = page.locator('studio-view').evaluate('editor => editor.graph.control_inputs')
    assert forwarded == {'block': [{'node': nested_id, 'port': 'block'}]}
    nested.get_by_role('button', name='Open group').click()
    focus_node(page, 'block')
    page.get_by_role('button', name='Copy', exact=True).click()
    copied = page.locator('studio-view').evaluate('editor => editor.clipboard')
    assert {node['id'] for node in copied['nodes']} == {'record', 'model', 'assess', group_id}
    assert any(edge['target'].get('port') == 'block' for edge in copied['controls'])
    page.get_by_role('button', name='Share', exact=True).click()
    page.get_by_label('Package content').select_option('selection')
    expect(page.get_by_label('Package content')).to_contain_text('Containing controlled group')
    payload = page.locator('studio-view').evaluate('editor => editor.sharingGraph()')
    checked = validate_graph(payload)
    assert all(error['code'] == 'binding' for error in checked['errors']), checked['errors']
    with page.expect_download() as downloaded:
        page.get_by_role('button', name='Export file', exact=True).click()
    package = json.loads(downloaded.value.path().read_text())
    assert any(edge['target'].get('port') == 'block' for edge in package['graph']['controls'])
    assert any(group['control_inputs'] for group in package['graph']['groups'].values())
    assert not errors


def test_mixed_group_gate_skips_only_intended_target(synthetic_model, decision_page):
    page, errors, _submissions = decision_page
    choose_template(page, 'Prompt injection check')
    focus_node(page, 'model')
    page.get_by_label('Choose model', exact=True).select_option('test-decision-model')
    focus_node(page, 'allow')
    page.locator('.studio-node[data-node-id="assessment"]').focus()
    page.keyboard.press('Shift+Enter')
    page.get_by_role('button', name='Group selection', exact=True).click()
    group = page.locator('.studio-node').filter(has=page.get_by_role('button', name='Open group'))
    group_id = group.get_attribute('data-node-id')
    definition = page.locator('studio-view').evaluate('editor => editor.graph.groups[editor.graph.nodes.find(n => n.type === "group").group]')
    assert definition['control_inputs'] == {'allow': [{'node': 'allow'}]}
    assert {node['id'] for node in definition['nodes']} == {'allow', 'assessment'}
    page.get_by_role('button', name='Run workflow', exact=True).click()
    expect(page.locator('#studio-runs')).to_contain_text('done', timeout=10000)
    states = page.locator('studio-view').evaluate('editor => editor.activeRun.nodes')
    assert states[group_id + '/allow']['state'] == 'skipped'
    assert states[group_id + '/assessment']['state'] == 'done'
    assert group_id + '/allow' not in synthetic_model
    assert group_id + '/assessment' in synthetic_model
    assert not errors
