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
    monkeypatch.setattr(Adapters, 'validate_resources', lambda self, graph: [])
    monkeypatch.setattr(Adapters, 'dependency_fingerprint', lambda self, node: '')
    previous = Adapters.immediate
    monkeypatch.setattr(Adapters, 'immediate', lambda self, node, inputs, context: {'model': {'kind': 'model', 'id': 'test-decision-model'}} if node['type'] == 'model' else previous(self, node, inputs, context))
    monkeypatch.setattr(Adapters, 'supports_authorized', lambda self, node: True)
    calls = []
    def execute(self, node, inputs, context):
        calls.append(node['id'])
        if node['type'] == 'stream.record':
            return {'record': parse_record(node['parameters']['sample'])}
        if node['type'] == 'guard.injection':
            return assess(node['type'], node['parameters'], inputs['record'], lambda *args: {'scores': {'yes': 0.98, 'no': 0.02}}, model_digest='test-model')
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
    assert not errors
