"""Real HTTP wizard with a scripted native CPU server; this does not prove model ability."""
import tempfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_fleet_bootstrap import browser, no_release_lookup
from test_fleet_ui import Serving

from poolhouse import home, node_launch
from poolhouse.fleet import automatic_pools, setup_preparation, startup_models
from poolhouse.fleet.conversations import Conversations
from poolhouse.fleet.models import Models
from poolhouse.fleet.projects import ProjectRegistry
from poolhouse.fleet.serving import Served
from poolhouse.fleet.themes import default_appearance
from poolhouse.home import machine_id
from poolhouse.hub.probe import GIB, MachineMemory

__all__ = ['browser', 'no_release_lookup']
pytest_plugins = ['serve_node', 'native_server_kit', 'test_fleet_setup_probe']
pytestmark = [pytest.mark.slow, pytest.mark.redteam]


@pytest.fixture
def ready_device(tmp_path, monkeypatch, native_probe, lease_node):
    monkeypatch.setattr(automatic_pools, 'offers', lambda port=None: [])
    node = lease_node.resolve()
    monkeypatch.setattr(node_launch, 'default_state', lambda: node)
    assert node_launch.default_state() == lease_node.resolve() and not node.is_symlink()
    with ExitStack() as resources:
        account = resources.enter_context(tempfile.TemporaryDirectory(prefix='poolhouse-setup-account-'))
        device = Serving(tmp_path / 'device', secure=False)
        resources.callback(device.close)
        private_home = Path(account).resolve()
        monkeypatch.setattr(home, 'user_home', lambda: private_home)
        monkeypatch.setattr(setup_preparation, 'machine_memory',
                            lambda: MachineMemory(total_ram=16 * GIB, available_ram=12 * GIB))
        root = private_home / '.poolhouse' / 'traind'
        root.mkdir(parents=True, mode=0o700)
        ui = device.ui
        ui.root = root
        ui.projects = ProjectRegistry(root, machine_id())
        ui.conversations = Conversations(root / 'chats')
        ui.models = Models(store=tmp_path / 'managed', roots=[native_probe.model.parent])
        info = native_probe.model.stat()
        row = {'path':str(native_probe.model), 'name':'Scripted CPU protocol fixture', 'servable':True,
               'capabilities':{'chat':True}, 'size_bytes':info.st_size, 'mtime':info.st_mtime,
               'context_length':2048, 'files':[]}
        # Catalogue and advertised capability metadata are fixtures; startup receipts and SDK calls are real.
        monkeypatch.setattr(ui.models, 'library', lambda: [row])
        monkeypatch.setattr(setup_preparation, 'SUGGESTED', [])
        monkeypatch.setattr(startup_models, 'SUGGESTED', [])
        served = Served(port=native_probe.info.port, models=[native_probe.model.name],
                        aliases=[str(native_probe.model)], context=2048, capabilities={'chat':True})
        other = Served(port=served.port, models=['another-fixture.gguf'],
                       aliases=['/unverified/another-fixture.gguf'], context=2048, capabilities={'chat':True})
        ui.serving = SimpleNamespace(live=lambda **_: [other, served],
                                     public=lambda: [other.public(), served.public()])
        ui.members = lambda **_: []
        ui.start_serving = lambda *a, **k: pytest.fail('Verified resident must not start another server')
        monkeypatch.setattr(setup_preparation.llama, 'ensure_server',
                            lambda *a, **k: pytest.fail('Verified resident must not reinstall its runtime'))
        try:
            yield SimpleNamespace(device=device, native=native_probe, root=root)
        finally:
            with ui.sessions._lock:
                ui.sessions._sessions.clear()
            jobs = ui.setup_jobs
            worker = jobs._worker if jobs is not None else None
            if worker is not None:
                worker.join(timeout=5)
                assert not worker.is_alive()


def capture(page, directory, name):
    path = directory / (name + '.png')
    page.screenshot(path=str(path), full_page=True)


def assert_preparation_receipts(case, submissions):
    jobs = case.device.ui.setup_jobs.all()
    assert len(jobs) == 1 and jobs[0]['state'] == 'done'
    assert set(jobs[0]['probes']) == {'chat', 'tool-call', 'tool-result'}
    assert {proof['status'] for proof in jobs[0]['probes'].values()} == {'accepted'}
    selected = case.device.ui.projects.coding_project(current=lambda: True)
    assert selected is not None and not selected.shared
    prepares = [body for url, body in submissions if url.endswith('/ui/setup/prepare')]
    assert len(prepares) == 1 and prepares[0]['download'] is False
    assert prepares[0]['project_id'] == selected.id
    assert case.native.info.log_path.read_text().splitlines().count('POST') == 3


def assert_selected_chat(page, model):
    page.wait_for_function('(model) => document.querySelector("chat-view #model")?.value === model', arg=str(model))
    assert page.locator('chat-view #model option:checked').inner_text() == model.name
    page.wait_for_function("""() => !document.querySelector('#conversation-project').textContent.includes('Loading') &&
        !document.querySelector('#conversation-channels').textContent.includes('Connecting to workspace')""")


@pytest.mark.parametrize('theme,width', [('light', 1440), ('dark', 390)])
def test_first_run_uses_actual_preparation_http_then_explicitly_opens_selected_chat(ready_device, browser, tmp_path, theme, width):
    case = ready_device
    case.device.ui.settings.appearance = default_appearance()
    case.device.ui.settings.appearance['active_theme'] = theme
    case.device.ui.settings.appearance_initialized = True
    page = browser.new_page(viewport={'width':width, 'height':900}, color_scheme=theme)
    page.set_default_timeout(7000)
    held, submissions, errors = [], [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('**/ui/setup/prepare', lambda request: held.append(request))
    page.on('request', lambda request: submissions.append((request.url, request.post_data_json))
            if request.method == 'POST' and '/ui/setup/' in request.url else None)
    try:
        page.goto(f'http://127.0.0.1:{case.device.port}/ui/')
        page.get_by_role('heading', name='Welcome to Poolhouse', exact=True).wait_for()
        page.wait_for_function('(theme) => document.documentElement.dataset.theme === theme', arg=theme)
        capture(page, tmp_path, '01-device')
        page.get_by_role('button', name='Continue', exact=True).click()
        page.locator('#startup-model-choice').wait_for()
        capture(page, tmp_path, '02-installed-model')
        assert page.locator('#startup-model-choice option').count() == 1
        page.get_by_text('Coding folder and availability', exact=True).click()
        capture(page, tmp_path, '03-folder-availability')
        page.get_by_role('button', name='Prepare and continue', exact=True).click()
        page.get_by_role('heading', name='Preparing your device', exact=True).wait_for()
        capture(page, tmp_path, '04-submitting')
        assert len(held) == 1 and case.device.ui.setup_jobs is None
        held[0].continue_()
        page.get_by_role('button', name='Start chat', exact=True).wait_for()
        capture(page, tmp_path, '05-verified-ready')
        assert_preparation_receipts(case, submissions)
        assert not case.device.ui.settings.setup_done
        page.get_by_role('button', name='Start chat', exact=True).click()
        page.wait_for_url('**/#chat')
        assert_selected_chat(page, case.native.model)
        capture(page, tmp_path, '06-chat')
        assert case.device.ui.settings.setup_done
        assert not page.get_by_text('no chat store on this daemon', exact=True).count()
        assert len([body for url, body in submissions if url.endswith('/ui/setup/done')]) == 1
        assert not errors
    except Exception as error:
        capture(page, tmp_path, '99-failure')
        raise AssertionError((page.locator('#first-run-card').inner_text(), errors,
                              [url for url, _ in submissions])) from error
    finally:
        page.close()
