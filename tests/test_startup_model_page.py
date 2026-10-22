"""Drive the three-step setup UI without downloads, models, native dialogs or public network."""
import json

import pytest
from web_assets import serve_page_asset

from poolhouse.fleet.page import render
from poolhouse.fleet.themes import PRESETS

pytestmark = pytest.mark.slow


class SetupPage:
    def __init__(self, *, installed=False, state='done', theme='light', needs_theme=False):
        self.posted, self.gets = [], []
        self.theme, self.needs_theme = theme, needs_theme
        self.default_selected = True
        self.installed, self.state = installed, state
        self.pool_mode, self.name = 'dev', 'test-device'
        self.create_error = False
        self.job = {'id': 'c' * 32, 'state': state, 'can_cancel':state in {'queued','installing'}, 'note': 'Checking the model', 'error': '',
                    'result': {'chat_ready': state == 'done', 'tools_ready': state == 'done',
                               'model': '/models/decoder.gguf'}}

    def route(self, request):
        path = request.request.url.split('http://127.0.0.1:8765', 1)[-1]
        if path == '/':
            return request.fulfill(body=render(), content_type='text/html')
        if serve_page_asset(request, path):
            return
        method = request.request.method
        if method == 'POST':
            self.posted.append((path, request.request.post_data_json))
        else:
            self.gets.append(path)
        body = {'ok': True}
        if path == '/ui/setup':
            body.update(needs_setup=True, name=self.name, pool_mode=self.pool_mode, experimental_notice_accepted=True, needs_setup_theme=self.needs_theme)
        elif path == '/ui/settings':
            body.update(settings={}, resolved_theme=PRESETS["dark" if self.needs_theme else self.theme],
                        theme_registry=list(PRESETS.values()))
        elif path == '/ui/session':
            body.update(signed_in=True, credentialed=True)
        elif path == '/ui/setup/initial':
            self.pool_mode = request.request.post_data_json['pool_mode']
            self.name = request.request.post_data_json['name']
            body.update(needs_setup=True, name=self.name, pool_mode=self.pool_mode, in_pool=True, group='local-pool')
        elif path == '/ui/models/startup':
            body.update(models=[{'model_id': 'curated-decoder', 'name': 'Recommended decoder', 'file': 'decoder.gguf',
                                'memory_gb': 4, 'download_bytes': 4300000000, 'installed': self.installed, 'recommended': True,
                                'fits_now': True, 'what': 'General chat', 'ref': 'hf:fixture/decoder.gguf'}],
                        memory={'pool': 'RAM', 'capacity_gb': 16, 'free_gb': 12}, context=8192)
        elif path == '/ui/setup/projects/pick':
            body.update(request_id='d' * 32, nonce='x' * 43)
        elif path == '/ui/setup/projects/create':
            body = {'error':'Fixture project creation failed'} if self.create_error else {
                'ok':True, 'purpose':'coding-project', 'status':'complete', 'selected':True,
                'project':{'id':'e' * 32, 'title':'Selected project'}}
        elif path == '/ui/setup/projects/default':
            body.update(project={'id': 'b' * 32, 'title': 'Private workspace'}, selected=self.default_selected)
        elif path == '/ui/setup/prepare' or path == '/ui/setup/prepare/' + self.job['id']:
            body = self.job
        elif path.endswith('/cancel'):
            self.job = {**self.job, 'state': 'cancelled', 'note': 'Preparation stopped', 'result': {}}
            body = self.job
        elif path == '/ui/setup/done':
            body['needs_setup'] = False
        elif path == '/ui/room':
            body = {'error':'Hardware tuning is unavailable in this fixture.'}
        elif path == '/ui/chat':
            body.update(models=[{'model':'/models/decoder.gguf', 'local':True, 'capabilities':{'chat':True}}], conversations=[])
        request.fulfill(body=json.dumps(body), content_type='application/json')


def screenshot(page, directory, name):
    """Keep actual rendered transition evidence beside the isolated journey."""
    directory.mkdir(exist_ok=True)
    page.screenshot(path=str(directory / f'{name}.png'), full_page=True)


def native_folder(page):
    page.add_init_script("""window.folderReply={status:'needs-project',purpose:'coding-project',project:{id:'dddddddddddddddddddddddddddddddd',title:'Chosen folder'}};
      window.__TAURI__={core:{invoke:async(command,args)=>{
        if(command!=='choose_coding_project')throw Error('Unexpected native command');
        if(args.requestId!=='dddddddddddddddddddddddddddddddd'||args.nonce!=='x'.repeat(43))throw Error('Missing exact challenge');
        return window.folderReply;}}};""")


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('width', [390, 1440])
def test_setup_prepares_once_then_explicitly_opens_ready_chat(theme, width, tmp_path):
    pw = pytest.importorskip('playwright.sync_api')
    setup = SetupPage(theme=theme, needs_theme=True)
    with pw.sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': width, 'height': 900}, color_scheme=theme)
        page.set_default_timeout(5000)
        page.route('http://127.0.0.1:8765/**', setup.route)
        page.goto('http://127.0.0.1:8765/')
        page.get_by_role('heading', name='Welcome to Poolhouse', exact=True).wait_for()
        page.wait_for_function('(theme) => document.documentElement.dataset.theme === theme', arg=theme)
        screenshot(page, tmp_path, '01-device')
        assert page.locator('#setup-device-name').input_value() == 'test-device'
        assert page.get_by_text('Train', exact=True).count() == 0
        page.get_by_role('button', name='Continue', exact=True).click()
        page.locator('#startup-model-choice').wait_for()
        screenshot(page, tmp_path, '02-model-choice')
        page.get_by_text('Coding folder and availability', exact=True).click()
        screenshot(page, tmp_path, '03-folder-availability')
        assert page.locator('#startup-model-choice').input_value() == 'curated-decoder'
        assert page.locator('#startup-model-download').count() == 0
        assert not any(path in {'/ui/setup/prepare', '/ui/setup/projects/default'} for path, _ in setup.posted)
        assert page.locator('#first-run-card').evaluate('e => e.scrollWidth <= e.clientWidth + 1')
        page.get_by_role('button', name='Prepare and continue', exact=True).click()
        try:
            page.get_by_role('button', name='Start chat', exact=True).wait_for()
        except Exception as error:
            raise AssertionError((page.locator('#first-run-card').inner_text(), setup.posted)) from error
        screenshot(page, tmp_path, '04-ready')
        assert page.get_by_text('Chat and tool use are ready.', exact=True).is_visible()
        prepares = [body for path, body in setup.posted if path == '/ui/setup/prepare']
        assert len(prepares) == 1
        assert prepares[0]['model_id'] == 'curated-decoder' and prepares[0]['download'] is True
        assert next(body for path, body in setup.posted if path == '/ui/setup/prefs')['system_theme'] == theme
        assert prepares[0]['project_id'] == 'b' * 32 and len(prepares[0]['idempotency_key']) == 32
        assert len([1 for path, _ in setup.posted if path == '/ui/setup/projects/default']) == 1
        assert not any(path in {'/ui/models', '/ui/serving', '/ui/serving/install'} for path, _ in setup.posted)
        page.get_by_role('button', name='Start chat', exact=True).click()
        page.wait_for_url('**/#chat')
        page.wait_for_function("() => document.querySelector('chat-view #model')?.value === '/models/decoder.gguf'")
        screenshot(page, tmp_path, '05-chat')
        assert [body for path, body in setup.posted if path == '/ui/setup/done'] == [{'system_theme':theme}]
        browser.close()


@pytest.mark.parametrize('action', ['installed', 'defer', 'cancel', 'interrupted', 'reopen'])
def test_setup_installed_deferred_cancelled_and_interrupted_states_are_truthful(action, tmp_path):
    pw = pytest.importorskip('playwright.sync_api')
    setup = SetupPage(installed=action == 'installed', state='installing' if action in {'cancel', 'reopen'} else 'interrupted' if action == 'interrupted' else 'done')
    with pw.sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(5000)
        page.route('http://127.0.0.1:8765/**', setup.route)
        page.goto('http://127.0.0.1:8765/')
        page.get_by_role('button', name='Continue', exact=True).click()
        page.locator('#startup-model-choice').wait_for()
        screenshot(page, tmp_path, '01-model-' + action)
        if action == 'defer':
            page.get_by_role('button', name='Set up models later', exact=True).click()
            page.wait_for_url('**/#chat')
            assert not any(path in {'/ui/setup/prepare', '/ui/setup/projects/default'} for path, _ in setup.posted)
        else:
            page.get_by_role('button', name='Prepare and continue', exact=True).click()
            if action == 'installed':
                page.get_by_role('button', name='Start chat', exact=True).wait_for()
                assert next(body for path, body in setup.posted if path == '/ui/setup/prepare')['download'] is False
            elif action == 'cancel':
                page.get_by_role('button', name='Stop preparing', exact=True).click()
                page.get_by_role('heading', name='Preparation stopped', exact=True).wait_for()
                assert page.get_by_role('button', name='Start chat', exact=True).count() == 0
                assert len([1 for path, _ in setup.posted if path.endswith('/cancel')]) == 1
            elif action == 'interrupted':
                page.get_by_role('button', name='Check status', exact=True).click()
                assert page.get_by_role('button', name='Start chat', exact=True).count() == 0
                assert len([1 for path, _ in setup.posted if path == '/ui/setup/prepare']) == 1
            else:
                page.get_by_role('button', name='Stop preparing', exact=True).wait_for()
                screenshot(page, tmp_path, '02-preparing-before-reopen')
                page.reload()
                page.get_by_role('button', name='Stop preparing', exact=True).wait_for()
                assert len([1 for path, _ in setup.posted if path == '/ui/setup/prepare']) == 1
        screenshot(page, tmp_path, '03-result-' + action)
        browser.close()


def test_back_invalidates_held_preparation_and_old_finally_cannot_unlock_new_request(tmp_path):
    pw = pytest.importorskip('playwright.sync_api')
    setup = SetupPage()
    held_prefs, held_initial = [], []
    initial_count = [0]
    def route(request):
        path = request.request.url.split('http://127.0.0.1:8765', 1)[-1]
        if path == '/ui/setup/prefs':
            setup.posted.append((path, request.request.post_data_json))
            held_prefs.append(request)
            return
        if path == '/ui/setup/initial':
            initial_count[0] += 1
            if initial_count[0] == 2:
                setup.posted.append((path, request.request.post_data_json))
                held_initial.append(request)
                return
        setup.route(request)
    with pw.sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(5000)
        page.route('http://127.0.0.1:8765/**', route)
        page.goto('http://127.0.0.1:8765/')
        page.get_by_role('button', name='Continue', exact=True).click()
        page.locator('#startup-model-choice').wait_for()
        page.get_by_role('button', name='Prepare and continue', exact=True).click()
        page.get_by_role('button', name='Back', exact=True).click()
        page.get_by_role('button', name='Continue', exact=True).click()
        screenshot(page, tmp_path, '01-new-request-pending')
        assert held_prefs and held_initial
        held_prefs[0].fulfill(body=json.dumps({'ok': True}), content_type='application/json')
        page.wait_for_function('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        assert page.get_by_role('button', name='Continue', exact=True).is_disabled()
        assert not any(path in {'/ui/setup/projects/default', '/ui/setup/prepare'} for path, _ in setup.posted)
        held_initial[0].fulfill(body=json.dumps({'ok': True, 'name':'test-device', 'pool_mode':'dev', 'in_pool':True}), content_type='application/json')
        page.locator('#startup-model-choice').wait_for()
        assert page.get_by_role('button', name='Prepare and continue', exact=True).is_enabled()
        screenshot(page, tmp_path, '02-return-model')
        browser.close()


def test_new_session_reentry_never_paints_old_ready_receipt_before_authorized_status(tmp_path):
    pw = pytest.importorskip('playwright.sync_api')
    setup = SetupPage()
    held = []
    hold_status = [False]
    def route(request):
        path = request.request.url.split('http://127.0.0.1:8765', 1)[-1]
        if hold_status[0] and path == '/ui/setup/prepare/' + setup.job['id']:
            held.append(request)
            return
        setup.route(request)
    with pw.sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(5000)
        page.route('http://127.0.0.1:8765/**', route)
        page.goto('http://127.0.0.1:8765/')
        page.get_by_role('button', name='Continue', exact=True).click()
        page.locator('#startup-model-choice').wait_for()
        page.get_by_role('button', name='Prepare and continue', exact=True).click()
        page.get_by_role('button', name='Start chat', exact=True).wait_for()
        hold_status[0] = True
        page.evaluate('async () => { await fleetModel.signOut(); fleetModel.go("first-run"); }')
        page.get_by_role('heading', name='Preparing your device', exact=True).wait_for()
        screenshot(page, tmp_path, '01-current-session-status-pending')
        assert held
        assert page.get_by_role('button', name='Start chat', exact=True).count() == 0
        held[0].fulfill(body=json.dumps({'error':'This preparation belongs to an ended session.'}), content_type='application/json', status=403)
        page.get_by_text('This preparation belongs to an ended session.', exact=True).wait_for()
        screenshot(page, tmp_path, '02-ended-session')
        assert page.get_by_role('button', name='Start chat', exact=True).count() == 0
        browser.close()


def test_lost_submission_ack_attaches_same_intent_and_shows_actual_transfer_bytes(tmp_path):
    pw = pytest.importorskip('playwright.sync_api')
    setup = SetupPage(state='installing')
    setup.job.update(stage='transfer', progress={'done':25, 'total':100})
    first = [True]
    def route(request):
        path = request.request.url.split('http://127.0.0.1:8765', 1)[-1]
        if path == '/ui/setup/prepare' and first[0]:
            first[0] = False
            setup.posted.append((path, request.request.post_data_json))
            request.fulfill(status=502, body=json.dumps({'error':'The preparation reply was interrupted.'}), content_type='application/json')
            return
        setup.route(request)
    with pw.sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(5000)
        page.route('http://127.0.0.1:8765/**', route)
        page.goto('http://127.0.0.1:8765/')
        page.get_by_role('button', name='Continue', exact=True).click()
        page.locator('#startup-model-choice').wait_for()
        page.get_by_role('button', name='Prepare and continue', exact=True).click()
        page.get_by_text('The preparation reply was interrupted.', exact=True).wait_for()
        screenshot(page, tmp_path, '01-unknown-receipt')
        page.get_by_role('button', name='Check status', exact=True).click()
        progress = page.locator('#first-run poolhouse-progress')
        progress.wait_for()
        assert progress.evaluate('e => [e.done,e.total]') == [25,100]
        screenshot(page, tmp_path, '02-attached-transfer-progress')
        starts = [body for path, body in setup.posted if path == '/ui/setup/prepare']
        assert len(starts) == 2 and starts[0] == starts[1]
        assert len([1 for path, _ in setup.posted if path == '/ui/setup/projects/default']) == 1
        assert page.get_by_role('button', name='Start chat', exact=True).count() == 0
        browser.close()


def test_new_session_discards_old_connection_policy_and_device_name_draft(tmp_path):
    pw = pytest.importorskip('playwright.sync_api')
    setup = SetupPage()
    with pw.sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(5000)
        page.route('http://127.0.0.1:8765/**', setup.route)
        page.goto('http://127.0.0.1:8765/')
        page.get_by_role('heading', name='Welcome to Poolhouse', exact=True).wait_for()
        page.locator('#setup-device-name').fill('old-device-draft')
        setup.pool_mode, setup.name = 'prod', 'current-device'
        page.evaluate('async () => { await fleetModel.signOut(); fleetModel.go("first-run"); }')
        page.get_by_role('heading', name='Welcome to Poolhouse', exact=True).wait_for()
        assert page.locator('#setup-device-name').input_value() == 'current-device'
        page.get_by_text('Connection options', exact=True).click()
        assert page.locator('#setup-invitation-only').is_checked()
        screenshot(page, tmp_path, '01-current-connection-policy')
        page.get_by_role('button', name='Continue', exact=True).click()
        page.get_by_role('heading', name='Make this device ready', exact=True).wait_for()
        body = next(body for path, body in setup.posted if path == '/ui/setup/initial')
        assert body['pool_mode'] == 'prod' and body['name'] == 'current-device'
        browser.close()


@pytest.fixture
def native_setup_page():
    pw = pytest.importorskip('playwright.sync_api')
    setup = SetupPage()
    with pw.sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(5000)
        native_folder(page)
        page.route('http://127.0.0.1:8765/**', setup.route)
        page.goto('http://127.0.0.1:8765/')
        yield setup, page
        browser.close()


@pytest.mark.parametrize('action', ['create', 'private', 'cancel', 'retry', 'back'])
def test_unresolved_native_folder_never_silently_falls_back_to_private_workspace(action, tmp_path, native_setup_page):
    setup, page = native_setup_page
    page.get_by_role('button', name='Continue', exact=True).click()
    page.locator('#startup-model-choice').wait_for()
    page.get_by_text('Coding folder and availability', exact=True).click()
    page.get_by_role('button', name='Choose a coding folder', exact=True).click()
    page.get_by_role('button', name='Create project here', exact=True).wait_for()
    screenshot(page, tmp_path, '01-pending-folder')
    prepare = page.get_by_role('button', name='Prepare and continue', exact=True)
    assert prepare.is_disabled()
    assert not any(path in {'/ui/setup/prepare', '/ui/setup/projects/default'} for path, _ in setup.posted)
    if action in {'create', 'retry'}:
        if action == 'retry':
            setup.create_error = True
            page.get_by_role('button', name='Create project here', exact=True).click()
            page.get_by_text('Fixture project creation failed', exact=True).wait_for()
            screenshot(page, tmp_path, '02-create-error')
            assert prepare.is_disabled()
            setup.create_error = False
        page.get_by_role('button', name='Create project here', exact=True).click()
        page.get_by_text('Coding folder: Selected project', exact=True).wait_for()
        assert prepare.is_enabled()
        prepare.click()
        page.get_by_role('button', name='Start chat', exact=True).wait_for()
        body = next(body for path, body in setup.posted if path == '/ui/setup/prepare')
        assert body['project_id'] == 'e' * 32
        assert not any(path == '/ui/setup/projects/default' for path, _ in setup.posted)
    elif action == 'private':
        page.get_by_role('button', name='Use private workspace', exact=True).click()
        assert prepare.is_enabled()
        prepare.click()
        page.get_by_role('button', name='Start chat', exact=True).wait_for()
        assert len([1 for path, _ in setup.posted if path == '/ui/setup/projects/default']) == 1
    elif action == 'cancel':
        page.evaluate("window.folderReply={status:'cancelled',purpose:'coding-project'}")
        page.get_by_role('button', name='Choose a coding folder', exact=True).click()
        page.get_by_text('Folder selection was cancelled.', exact=True).wait_for()
        assert prepare.is_disabled()
        assert page.get_by_role('button', name='Create project here', exact=True).is_visible()
    else:
        page.get_by_role('button', name='Back', exact=True).click()
        page.get_by_role('button', name='Continue', exact=True).click()
        page.locator('#startup-model-choice').wait_for()
        page.get_by_text('Coding folder and availability', exact=True).click()
        assert prepare.is_disabled()
        assert page.get_by_role('button', name='Create project here', exact=True).is_visible()
    screenshot(page, tmp_path, '03-result-' + action)


@pytest.mark.parametrize('old_error', [False, True])
def test_optional_theme_reply_cannot_change_a_new_session_or_its_theme(old_error, tmp_path):
    pw = pytest.importorskip('playwright.sync_api')
    setup = SetupPage(theme='dark')
    held = []
    def route(request):
        if request.request.url.endswith('/ui/settings') and not held:
            held.append(request)
            return
        setup.route(request)
    with pw.sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(5000)
        page.route('http://127.0.0.1:8765/**', route)
        page.goto('http://127.0.0.1:8765/')
        page.get_by_role('heading', name='Welcome to Poolhouse', exact=True).wait_for()
        assert held
        page.evaluate('async () => { await fleetModel.signOut(); fleetModel.go("first-run"); }')
        page.wait_for_function('document.documentElement.dataset.theme === "dark"')
        epoch = page.evaluate('fleetModel.sessionContext()')
        screenshot(page, tmp_path, '01-current-dark-session')
        body = {'error':'Old unauthorized theme read'} if old_error else {
            'ok':True, 'settings':{}, 'resolved_theme':PRESETS['light']}
        held[0].fulfill(body=json.dumps(body), content_type='application/json', status=403 if old_error else 200)
        page.wait_for_function('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        assert page.evaluate('fleetModel.sessionContext()') == epoch
        assert page.locator('html').get_attribute('data-theme') == 'dark'
        assert page.get_by_role('heading', name='Welcome to Poolhouse', exact=True).is_visible()
        screenshot(page, tmp_path, '02-stale-theme-ignored')
        browser.close()


def test_historical_default_response_never_prepares_or_silently_selects_it(native_setup_page, tmp_path):
    setup, page = native_setup_page
    setup.default_selected = False
    page.get_by_role('button', name='Continue', exact=True).click()
    page.locator('#startup-model-choice').wait_for()
    page.get_by_role('button', name='Prepare and continue', exact=True).click()
    page.get_by_text('The coding folder choice changed. Prepare again to use the private workspace, or choose a folder.', exact=True).wait_for()
    screenshot(page, tmp_path, '01-historical-choice-inert')
    assert not any(path == '/ui/setup/prepare' for path, _ in setup.posted)
    assert page.get_by_role('button', name='Start chat', exact=True).count() == 0
    setup.default_selected = True
    page.get_by_role('button', name='Prepare and continue', exact=True).click()
    page.get_by_role('button', name='Start chat', exact=True).wait_for()
    requests = [body for path, body in setup.posted if path == '/ui/setup/projects/default']
    assert len(requests) == 2 and requests[0]['operation_id'] != requests[1]['operation_id']
    screenshot(page, tmp_path, '02-explicit-new-default-choice')


def test_lost_default_reply_reuses_exact_operation_after_reload(tmp_path):
    pw = pytest.importorskip('playwright.sync_api')
    setup, failed = SetupPage(), [False]
    def route(request):
        if request.request.url.endswith('/ui/setup/projects/default') and not failed[0]:
            failed[0] = True
            setup.posted.append(('/ui/setup/projects/default', request.request.post_data_json))
            request.fulfill(status=502, json={'error':'The folder reply was interrupted.'})
            return
        setup.route(request)
    with pw.sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(5000)
        page.route('http://127.0.0.1:8765/**', route)
        for attempt in range(2):
            page.goto('http://127.0.0.1:8765/')
            page.get_by_role('button', name='Continue', exact=True).click()
            page.locator('#startup-model-choice').wait_for()
            page.get_by_role('button', name='Prepare and continue', exact=True).click()
            if attempt == 0:
                page.get_by_text('The folder reply was interrupted.', exact=True).wait_for()
            else:
                page.get_by_role('button', name='Start chat', exact=True).wait_for()
            screenshot(page, tmp_path, f'0{attempt + 1}-default-receipt')
        requests = [body for path, body in setup.posted if path == '/ui/setup/projects/default']
        assert len(requests) == 2 and requests[0] == requests[1]
        assert len(requests[0]['operation_id']) == 32
        assert len([1 for path, _ in setup.posted if path == '/ui/setup/prepare']) == 1
        browser.close()


def test_missing_theme_registry_keeps_setup_usable_without_an_extra_write(tmp_path):
    pw = pytest.importorskip('playwright.sync_api')
    setup = SetupPage(theme='dark', needs_theme=True)
    errors = []
    def route(request):
        if request.request.url.endswith('/ui/settings'):
            request.fulfill(json={'ok':True, 'settings':{}, 'resolved_theme':PRESETS['dark']})
            return
        setup.route(request)
    with pw.sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        page = browser.new_page(color_scheme='light')
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.set_default_timeout(5000)
        page.route('http://127.0.0.1:8765/**', route)
        page.goto('http://127.0.0.1:8765/')
        page.get_by_role('heading', name='Welcome to Poolhouse', exact=True).wait_for()
        screenshot(page, tmp_path, '01-no-registry')
        page.get_by_role('button', name='Continue', exact=True).click()
        page.locator('#startup-model-choice').wait_for()
        page.get_by_role('button', name='Set up models later', exact=True).click()
        page.wait_for_url('**/#chat')
        assert [body for path, body in setup.posted if path == '/ui/setup/done'] == [{'system_theme':'light'}]
        assert not any(path in {'/ui/settings', '/ui/setup/prefs', '/ui/setup/prepare'} for path, _ in setup.posted)
        assert not errors
        screenshot(page, tmp_path, '02-explicit-deferred')
        browser.close()
