"""Typed record fields through actual owner UI routes; no response or execution stubs."""
from __future__ import annotations

import json
import re

import pytest
from browser_expect import expect
from studio_commands import studio_command, workflow_picker
from test_fleet_ui import Serving
from test_studio_canvas_browser import studio_browser  # noqa: F401
from test_studio_record_nodes import graph

from poolhouse.fleet import tls
from poolhouse.fleet.discovery import mint_pool
from poolhouse.fleet.membership import Roster

pytestmark = pytest.mark.slow


@pytest.fixture
def record_page(tmp_path, studio_browser):  # noqa: F811
    app = Serving(tmp_path)
    app.httpd.daemon_threads = False
    app.ui.root = tmp_path / 'traind'
    app.ui.settings.setup_done = True
    identity = tls.identity(tmp_path / 'tls', 'records')
    app.ui.studio_identity = lambda: identity
    member = mint_pool('record-fields', app.keyfile)
    Roster(member.key).enrol(identity.beacon, 'Record test device', 'test')
    session = app.ui.sessions.open('record-owner-session', 'launch-ticket')
    cookie = 'poolhouse_ui=' + session.sid
    value = graph()
    for node in value['nodes']:
        node['version'] = 1
    value['nodes'][1]['parameters']['controls'] = {'keep_other': True, 'assignments': []}
    value['nodes'][2]['parameters']['controls'] = {'field': 'score', 'operator': 'exists'}
    status, body, _ = app.call('/ui/studio/workflows', method='POST', cookie=cookie,
        body={'title': 'Record fields', 'graph': value})
    assert status == 201, body
    workflow = body['workflow']
    try:
        with studio_browser() as page:
            page.context.add_cookies([{'name': 'poolhouse_ui', 'value': session.sid,
                                      'url': f'http://127.0.0.1:{app.port}/ui/'}])
            errors, posts = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: posts.append(request.url)
                    if request.method == 'POST' and '/ui/studio/' in request.url else None)
            page.goto(f'http://127.0.0.1:{app.port}/ui/#studio')
            page.wait_for_function('(id) => document.querySelector("studio-view")?.workflows?.some(w => w.id === id)', arg=workflow['id'])
            workflow_picker(page).select_option(workflow['id'])
            page.wait_for_function('(id) => document.querySelector("studio-view")?.workflowId === id', arg=workflow['id'])
            yield page, app, errors, posts
    finally:
        app.close()
        if getattr(app.ui, 'studio_streams', None):
            app.ui.studio_streams.close()
        if getattr(app.ui, 'studio', None):
            app.ui.studio.close()
            app.ui.studio.store.close()


def select_node(page, title):
    if not page.locator('.studio-outline').is_visible():
        studio_command(page, 'Outline').click()
    page.locator('.studio-outline').get_by_role('button', name=re.compile('^' + re.escape(title))).click()
    return page.locator('.studio-inspector')


def enter(input, value):
    input.fill(value)
    input.press('Tab')


def test_record_mapping_and_typed_if_save_reload_and_run_both_branches(record_page):
    page, _, errors, _ = record_page
    inspector = select_node(page, 'Map record fields')
    inspector.get_by_label('Keep other record fields', exact=True).uncheck()
    inspector.get_by_role('button', name='Add field mapping', exact=True).focus()
    page.keyboard.press('Enter')
    expect(inspector.get_by_label('Output field 1', exact=True)).to_be_focused()
    enter(inspector.get_by_label('Output field 1', exact=True), 'score')
    enter(inspector.get_by_label('Original field 1', exact=True), 'value')
    inspector.get_by_role('button', name='Add field mapping', exact=True).click()
    enter(inspector.get_by_label('Output field 2', exact=True), 'metadata')
    inspector.get_by_label('Value source 2', exact=True).select_option('value')
    inspector.get_by_label('Literal value 2 type', exact=True).select_option('json')
    expect(inspector.get_by_label('Literal value 2', exact=True)).to_be_focused()
    literal = {'text': '</textarea><img src=x onerror="window.recordInjected=true">', 'typed': [True, 1, None]}
    enter(inspector.get_by_label('Literal value 2', exact=True), json.dumps(literal))
    inspector = select_node(page, 'If record matches')
    inspector.get_by_label('Comparison', exact=True).select_option('gt')
    expect(inspector.get_by_label('Comparison value', exact=True)).to_be_focused()
    enter(inspector.get_by_label('Comparison value', exact=True), '1')
    page.get_by_role('button', name='Save', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('Saved')
    page.reload()
    page.wait_for_function('() => document.querySelector("studio-view")?.graph.nodes.some(n => n.type === "record.map")')
    inspector = select_node(page, 'Map record fields')
    expect(inspector.get_by_label('Output field 1', exact=True)).to_have_value('score')
    assert json.loads(inspector.get_by_label('Literal value 2', exact=True).input_value()) == literal
    assert page.locator('.studio-inspector img').count() == 0
    assert page.evaluate('window.recordInjected') is None
    page.get_by_role('button', name='Run workflow', exact=True).click()
    page.wait_for_function('() => document.querySelector("studio-view")?.activeRun?.state === "done"')
    first = page.evaluate('document.querySelector("studio-view").activeRun')
    assert first['nodes']['map']['outputs']['record'] == {'score': 2, 'metadata': literal}
    assert first['nodes']['yes']['state'] == 'done' and first['nodes']['no']['state'] == 'skipped'
    inspector = select_node(page, 'If record matches')
    enter(inspector.get_by_label('Comparison value', exact=True), '3')
    page.get_by_role('button', name='Run workflow', exact=True).click()
    page.wait_for_function('(id) => { const run = document.querySelector("studio-view")?.activeRun; return run?.id !== id && run?.state === "done"; }', arg=first['id'])
    second = page.evaluate('document.querySelector("studio-view").activeRun')
    assert second['nodes']['yes']['state'] == 'skipped' and second['nodes']['no']['state'] == 'done'
    assert errors == []


def test_invalid_literal_stays_visible_and_blocks_save_and_run(record_page):
    page, _, errors, posts = record_page
    inspector = select_node(page, 'Map record fields')
    inspector.get_by_role('button', name='Add field mapping', exact=True).click()
    enter(inspector.get_by_label('Output field 1', exact=True), 'literal')
    inspector.get_by_label('Value source 1', exact=True).select_option('value')
    inspector.get_by_label('Literal value 1 type', exact=True).select_option('json')
    enter(inspector.get_by_label('Literal value 1', exact=True), '{"incomplete":')
    expect(inspector.get_by_role('status').filter(has_text='Complete the literal')).to_be_visible()
    page.get_by_role('button', name='Save', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('Complete the record literal')
    page.get_by_role('button', name='Run workflow', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('Complete the record literal')
    assert not any(url.endswith('/runs') for url in posts)
    expect(inspector.get_by_label('Literal value 1', exact=True)).to_have_value('{"incomplete":')
    enter(inspector.get_by_label('Literal value 1', exact=True), '{"complete":true}')
    page.get_by_role('button', name='Save', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('Saved')
    assert errors == []


def test_detached_inspector_and_reinserted_node_cannot_receive_stale_edits(record_page):
    page, _, errors, _ = record_page
    inspector = select_node(page, 'Map record fields')
    inspector.get_by_role('button', name='Add field mapping', exact=True).click()
    enter(inspector.get_by_label('Output field 1', exact=True), 'original')
    old = inspector.get_by_label('Output field 1', exact=True).element_handle()
    select_node(page, 'If record matches')
    page.evaluate('(input) => { input.value = "foreign"; input.dispatchEvent(new Event("change", {bubbles:true})); }', old)
    assert page.evaluate('document.querySelector("studio-view").graph.nodes.find(n => n.id === "map").parameters.controls.assignments[0].target') == 'original'
    inspector = select_node(page, 'Map record fields')
    old = inspector.get_by_label('Output field 1', exact=True).element_handle()
    studio_command(page, 'Delete').click()
    studio_command(page, 'Undo').click()
    page.evaluate('(input) => { input.value = "reinserted"; input.dispatchEvent(new Event("change", {bubbles:true})); }', old)
    assert page.evaluate('document.querySelector("studio-view").graph.nodes.find(n => n.id === "map").parameters.controls.assignments[0].target') == 'original'
    assert errors == []


def test_first_autosave_and_repeated_visible_edits_keep_current_draft(record_page):
    page, _, errors, _ = record_page
    page.set_viewport_size({'width': 900, 'height': 900})
    page.get_by_role('button', name='New', exact=True).click()
    page.get_by_label('Search nodes, templates and groups').fill('Map record fields')
    page.locator('.studio-library').get_by_role('button', name=re.compile('^Add Map record fields')).focus()
    page.keyboard.press('Enter')
    page.get_by_role('button', name='Configure field mapping', exact=True).click()
    inspector = page.locator('.studio-inspector')
    inspector.get_by_role('button', name='Add field mapping', exact=True).click()
    page.wait_for_function('() => !!document.querySelector("studio-view")?.workflowId')
    input = inspector.get_by_label('Output field 1', exact=True)
    enter(input, 'first')
    enter(input, 'second')
    enter(inspector.get_by_label('Original field 1', exact=True), 'value')
    expect(input).to_have_value('second')
    page.get_by_role('button', name='Save', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('Saved')
    assert page.evaluate('document.querySelector("studio-view").graph.nodes[0].parameters.controls.assignments[0].target') == 'second'
    assert errors == []
