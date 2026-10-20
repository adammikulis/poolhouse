"""Late startup inventory cannot replace an explicitly chosen workflow draft."""
import copy

import pytest
from browser_expect import expect
from test_fleet_ui import Serving
from test_studio_canvas_browser import SPECS, studio_browser  # noqa: F401

pytestmark = pytest.mark.slow


def response(call, state, tmp_path):
    path = call.request.url.split('/ui/studio', 1)[1]
    if path == '/nodes':
        result = {'nodes': SPECS}
    elif path == '/templates':
        result = {'templates': []}
    elif path == '/devices':
        result = {'devices': []}
    elif path == '/workflows' and call.request.method == 'GET':
        result = {'workflows': copy.deepcopy(list(state['documents'].values()))}
    elif path.startswith('/workflows'):
        if state.get('save_error'):
            call.fulfill(status=503, json={'error': 'Controlled save failure'})
            return
        data = call.request.post_data_json
        identifier = 'new-workflow'
        saved = {**data, 'id': identifier, 'revision': len(state['saved']) + 1}
        state['saved'].append(saved)
        state['documents'][identifier] = copy.deepcopy(saved)
        result = {'workflow': saved}
        if state.get('hold_save'):
            state.setdefault('pending_saves', []).append((call, result))
            return
    elif path == '/resources':
        result = {}
    elif path == '/runs':
        result = {'runs': []}
    else:
        result = {}
    return result


@pytest.fixture
def delayed_page(tmp_path, studio_browser, request):  # noqa: F811
    app = Serving(tmp_path)
    app.ui.settings.setup_done = True
    old = {'id': 'older-workflow', 'revision': 4, 'title': 'Earlier experiment', 'graph': {
        'nodes': [{'id': 'old-prompt', 'type': 'prompt', 'version': 1, 'parameters': {'template': 'Existing text'}}],
        'edges': [], 'controls': [], 'groups': {}, 'layout': {'nodes': {}, 'viewport': {'x': 0, 'y': 0, 'zoom': 1}}}}
    state = {'held': [], 'saved': [], 'documents': {old['id']: copy.deepcopy(old)}}
    delayed = request.param
    with studio_browser() as page:
        def route(call):
            path = call.request.url.split('/ui/studio', 1)[1]
            result = response(call, state, tmp_path)
            if result is None:
                return
            if path == delayed and call.request.method == 'GET' and not state.get('released'):
                state['held'].append((call, result))
                page.evaluate('window.studioInitialInventoryHeld = true')
            else:
                call.fulfill(json=result)
        page.route('**/ui/studio/**', route)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(f'http://127.0.0.1:{app.port}/ui/#studio')
            page.wait_for_function('() => window.studioInitialInventoryHeld === true')
            expect(page.get_by_role('button', name='New', exact=True)).to_be_visible()
            yield page, state, old, errors
        finally:
            for call, result in state['held']:
                if not state.get('released'):
                    call.fulfill(json=result)
            for call, result in state.get('pending_saves', []):
                call.fulfill(json=result)
            app.close()


def release(page, state):
    state['released'] = True
    for call, result in state['held']:
        call.fulfill(json=result)
    page.wait_for_function('() => document.querySelector("studio-view").loaded && !document.querySelector("studio-view").loading')


@pytest.mark.parametrize('delayed_page', ['/workflows', '/resources'], indirect=True)
@pytest.mark.parametrize('intent', ['new', 'new_save', 'title'])
def test_explicit_intent_survives_delayed_initial_selection(delayed_page, intent):
    page, state, old, errors = delayed_page
    if intent != 'title':
        page.get_by_role('button', name='New', exact=True).click()
    title = page.get_by_label('Workflow title', exact=True)
    if intent in {'new_save', 'title'}:
        title.fill('Current experiment')
    if intent == 'new_save':
        page.get_by_role('button', name='Save', exact=True).click()
        expect(page.locator('.studio-status')).to_contain_text('Saved')
        assert state['documents']['new-workflow']['graph']['nodes'] == []
    release(page, state)
    expect(title).to_have_value('Current experiment' if intent != 'new' else 'Untitled experiment')
    assert page.locator('studio-view').evaluate('editor => editor.graph.nodes.length') == 0
    assert state['documents'][old['id']] == old
    if intent == 'new_save':
        expect(page.get_by_label('Saved workflows', exact=True)).to_have_value('new-workflow')
        expect(page.get_by_label('Saved workflows', exact=True).locator('option[value="new-workflow"]')).to_have_count(1)
    assert not errors


@pytest.mark.parametrize('delayed_page', ['/workflows', '/resources'], indirect=True)
def test_untouched_startup_still_opens_first_saved_workflow(delayed_page):
    page, state, old, errors = delayed_page
    release(page, state)
    expect(page.get_by_label('Workflow title', exact=True)).to_have_value(old['title'])
    assert page.locator('studio-view').evaluate('editor => editor.workflowId') == old['id']
    assert state['saved'] == []
    assert not errors


@pytest.mark.parametrize('delayed_page', ['/resources'], indirect=True)
@pytest.mark.parametrize('automatic', [False, True])
def test_initial_response_cannot_replace_inflight_save(delayed_page, automatic):
    page, state, old, errors = delayed_page
    state['hold_save'] = True
    page.get_by_role('button', name='New', exact=True).click()
    title = page.get_by_label('Workflow title', exact=True)
    title.fill('Pending experiment')
    if automatic:
        title.press('Tab')
    else:
        page.get_by_role('button', name='Save', exact=True).click()
    page.wait_for_function('() => !!document.querySelector("studio-view").saving')
    release(page, state)
    expect(title).to_have_value('Pending experiment')
    assert page.locator('studio-view').evaluate('editor => editor.graph.nodes.length') == 0
    assert state['pending_saves']
    pending = state.pop('pending_saves')
    state['hold_save'] = False
    for call, result in pending:
        call.fulfill(json=result)
    expect(page.locator('.studio-status')).to_contain_text('Saved')
    expect(page.get_by_label('Saved workflows', exact=True)).to_have_value('new-workflow')
    assert state['documents'][old['id']] == old
    assert not errors


@pytest.mark.parametrize('delayed_page', ['/resources'], indirect=True)
@pytest.mark.parametrize('failure', [False, True])
def test_explicit_selection_and_failed_selection_survive_initial_response(delayed_page, failure):
    page, state, old, errors = delayed_page
    page.get_by_role('button', name='New', exact=True).click()
    title = page.get_by_label('Workflow title', exact=True)
    title.fill('Selected experiment')
    page.get_by_role('button', name='Save', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('Saved')
    if failure:
        state['save_error'] = True
        title.fill('Keep failed draft')
        title.press('Tab')
    page.get_by_label('Saved workflows', exact=True).select_option('')
    if failure:
        expect(page.locator('.studio-status')).to_contain_text('Controlled save failure')
    else:
        expect(title).to_have_value('Untitled experiment')
    release(page, state)
    expect(title).to_have_value('Keep failed draft' if failure else 'Untitled experiment')
    assert page.locator('studio-view').evaluate('editor => editor.graph.nodes.length') == 0
    assert state['documents'][old['id']] == old
    assert not errors


@pytest.mark.parametrize('delayed_page', ['/resources'], indirect=True)
def test_failed_startup_then_retry_keeps_preexisting_explicit_intent(delayed_page):
    page, state, old, errors = delayed_page
    page.get_by_role('button', name='New', exact=True).click()
    title = page.get_by_label('Workflow title', exact=True)
    title.fill('Retained after retry')
    page.get_by_role('button', name='Save', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('Saved')
    state['released'] = True
    for call, _result in state['held']:
        call.fulfill(status=503, json={'error': 'Controlled initial load failure'})
    expect(page.locator('.studio-status')).to_contain_text('Controlled initial load failure')
    page.locator('studio-view').evaluate('editor => editor.load()')
    expect(title).to_have_value('Retained after retry')
    expect(page.get_by_label('Saved workflows', exact=True)).to_have_value('new-workflow')
    assert state['documents'][old['id']] == old
    assert not errors


@pytest.mark.parametrize('delayed_page', ['/resources'], indirect=True)
def test_user_graph_edit_survives_late_resources(delayed_page):
    page, state, old, errors = delayed_page
    page.get_by_role('button', name='New', exact=True).click()
    control = page.get_by_role('button', name='Add Prompt template', exact=True)
    control.focus()
    control.press('Enter')
    before = page.locator('studio-view').evaluate('editor => window.studioModel.clone(editor.graph)')
    release(page, state)
    assert page.locator('studio-view').evaluate('editor => editor.graph') == before
    assert state['documents'][old['id']] == old
    assert not errors


@pytest.mark.parametrize('delayed_page', ['/resources'], indirect=True)
@pytest.mark.parametrize('intent', ['new', 'title', 'node', 'saved', 'untouched'])
def test_route_restart_preserves_intent_but_untouched_editor_opens_default(delayed_page, intent):
    page, state, old, errors = delayed_page
    title = page.get_by_label('Workflow title', exact=True)
    if intent in {'new', 'node', 'saved'}:
        page.get_by_role('button', name='New', exact=True).click()
    if intent in {'title', 'saved'}:
        title.fill('Retained through navigation')
    if intent == 'node':
        control = page.get_by_role('button', name='Add Prompt template', exact=True)
        control.focus()
        control.press('Enter')
    if intent == 'saved':
        page.get_by_role('button', name='Save', exact=True).click()
        expect(page.locator('.studio-status')).to_contain_text('Saved')
    page.evaluate('() => {fleetModel.go("pool");fleetModel.go("studio");}')
    release(page, state)
    expected = old['title'] if intent == 'untouched' else 'Retained through navigation' if intent in {'title', 'saved'} else 'Untitled experiment'
    expect(title).to_have_value(expected)
    nodes = page.locator('studio-view').evaluate('editor => editor.graph.nodes')
    assert len(nodes) == (1 if intent in {'untouched', 'node'} else 0)
    if intent == 'saved':
        expect(page.get_by_label('Saved workflows', exact=True)).to_have_value('new-workflow')
    assert state['documents'][old['id']] == old
    assert not errors


@pytest.mark.parametrize('delayed_page', ['/resources'], indirect=True)
@pytest.mark.parametrize('failure', [False, True])
def test_direct_save_of_untouched_editor_prevents_late_default_selection(delayed_page, failure):
    page, state, old, errors = delayed_page
    state['save_error'] = failure
    page.locator('studio-view').evaluate('editor => editor.perform(() => editor.save())')
    release(page, state)
    assert page.locator('studio-view').evaluate('editor => editor.graph.nodes') == []
    expect(page.get_by_label('Workflow title', exact=True)).to_have_value('Untitled experiment')
    if not failure:
        expect(page.get_by_label('Saved workflows', exact=True)).to_have_value('new-workflow')
    assert state['documents'][old['id']] == old
    assert not errors


@pytest.mark.parametrize('delayed_page', ['/runs'], indirect=True)
def test_old_run_refresh_cannot_publish_ready_after_route_change(delayed_page):
    page, state, _old, errors = delayed_page
    page.locator('studio-view').evaluate('editor => {editor.liveBox.hidden=false;editor.loadLive=async () => {editor.liveLoadCalls=(editor.liveLoadCalls||0)+1;};}')
    page.evaluate('() => fleetModel.go("pool")')
    state['released'] = True
    for call, result in state['held']:
        call.fulfill(json=result)
    page.wait_for_function('() => !document.querySelector("studio-view").loading')
    assert page.locator('studio-view').evaluate('editor => editor.liveLoadCalls || 0') == 0
    assert page.locator('#studio-live').is_disabled()
    assert not errors
