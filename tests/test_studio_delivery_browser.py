"""Outbound controls through real owner routes, with controlled scores and transport."""
from __future__ import annotations

import copy
import json
import re
from types import SimpleNamespace

import pytest
from browser_expect import expect
from test_fleet_ui import Serving
from test_studio_canvas_browser import studio_browser  # noqa: F401
from test_studio_decision_browser import choose_template, focus_node
from test_studio_live_browser import open_live

from poolhouse import hub
from poolhouse.fleet import studio_delivery_service, tls
from poolhouse.fleet.discovery import mint_pool
from poolhouse.fleet.membership import Roster
from poolhouse.studio import adapters
from poolhouse.studio.adapters import Adapters
from poolhouse.studio.decisions import assess

pytestmark = pytest.mark.slow


def install_model_fixture(tmp_path, monkeypatch):
    model = tmp_path / 'verified-test-model'
    model.mkdir()
    (model / 'config.json').write_text('{"model_type":"controlled_test_model"}')
    (model / 'model.safetensors').write_bytes(b'controlled model fixture, never loaded')
    entry = SimpleNamespace(id='test-decision-model', path=model, format='safetensors', kind='decision',
                            is_complete=True, verified=True, repo='')
    monkeypatch.setattr(hub, 'discover', lambda **kwargs: [entry])
    def controlled_source(resource, output):
        assert resource['id'] == 'test-decision-model' and resource['path'] == str(model)
        return None, {'model_digest': 'controlled-model', 'calibration_digest': 'unverified'}
    monkeypatch.setattr(adapters, 'decision_source', controlled_source)


@pytest.fixture
def delivery_page(tmp_path, request, monkeypatch):
    """Model-format parsing, model scores and transport are controlled; resource content and grants stay real."""
    install_model_fixture(tmp_path, monkeypatch)
    state = {'mode': 'done', 'sends': [], 'posts': [], 'model_calls': []}
    def send(destination, envelope, *, authorize):
        assert authorize() is True
        state['sends'].append((copy.deepcopy(destination), copy.deepcopy(envelope)))
        if state['mode'] == 'done':
            return {'state': 'done', 'receipt': {key: envelope[key] for key in ('delivery_id', 'request_digest')}}
        return {'state': 'interrupted', 'reason': 'Receiver receipt unavailable'}
    monkeypatch.setattr(studio_delivery_service, 'deliver', send)
    previous = Adapters.supports_authorized
    monkeypatch.setattr(Adapters, 'supports_authorized', lambda self, node: node['type'] in {'guard.injection', 'report'} or previous(self, node))
    def execute(self, node, inputs, context):
        assert context['principal']['kind'] == 'studio_subscription'
        if node['type'] == 'guard.injection':
            state['model_calls'].append(node['id'])
            return assess(node['type'], node['parameters'], inputs['record'],
                lambda *args: {'scores': {'yes': .98, 'no': .02}},
                identity={'model_digest': 'controlled-model', 'calibration_digest': 'unverified'})
        assert node['type'] == 'report'
        return {'artifact': {'report': inputs['report']}}
    monkeypatch.setattr(Adapters, 'execute_authorized', execute)
    environment = SimpleNamespace(exists=True, require_current_runtime=lambda: None,
        require_compute=lambda: 'controlled-runtime', installed=lambda: {'torch': '1', 'transformers': '1', 'peft': '1'})
    app = Serving(tmp_path)
    app.httpd.daemon_threads = False
    app.ui.root, app.ui.settings.setup_done = tmp_path / 'traind', True
    app.ui.environment = app.runner.environment = environment
    identity = tls.identity(tmp_path / 'tls', 'studio')
    app.ui.studio_identity = lambda: identity
    member = mint_pool('delivery-example-pool', app.keyfile)
    Roster(member.key).enrol(identity.beacon, 'delivery-device', 'test')
    session = app.ui.sessions.open('owner-session', 'launch-ticket')
    app.cookie = 'poolhouse_ui=' + session.sid
    open_page = request.getfixturevalue('studio_browser')
    try:
        with open_page() as page:
            page.context.add_cookies([{'name': 'poolhouse_ui', 'value': session.sid,
                                      'url': f'http://127.0.0.1:{app.port}/ui/'}])
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda req: state['posts'].append((req.url, req.method, req.post_data_json)) if req.method in {'POST', 'PUT', 'DELETE'} and '/ui/studio/' in req.url else None)
            page.goto(f'http://127.0.0.1:{app.port}/ui/#studio')
            choose_template(page, 'Prompt injection check')
            focus_node(page, 'model')
            page.get_by_label('Choose model', exact=True).select_option('test-decision-model')
            page.get_by_role('button', name='Save', exact=True).click()
            expect(page.locator('.studio-status')).to_contain_text('Saved')
            open_live(page)
            yield page, app, state, errors
    finally:
        app.close()
        if hasattr(app.ui, 'studio'):
            app.ui.studio.close()
            app.ui.studio.store.close()


def register_destination(page):
    page.get_by_text('Destinations and selected outputs', exact=True).click()
    page.get_by_text('Manage registered destinations', exact=True).click()
    page.get_by_label('Destination name', exact=True).fill('Decision receiver')
    page.get_by_label('Destination URL (HTTPS)', exact=True).fill('https://receiver.invalid/decisions')
    page.get_by_role('button', name='Register destination', exact=True).click()
    expect(page.locator('.studio-live [role="status"]')).to_contain_text('Destination saved at revision 1')


def configure_delivery(page):
    register_destination(page)
    page.get_by_label('Send selected outputs to a registered destination', exact=True).check()
    selector = page.get_by_label('Delivery destination', exact=True)
    selector.select_option(index=1)
    page.get_by_label('assess · signal (json)', exact=True).check()
    page.get_by_role('button', name='Create paused stream', exact=True).click()
    expect(page.get_by_role('button', name='Start stream', exact=True)).to_be_enabled()


def start_sample(page):
    page.get_by_role('button', name='Start stream', exact=True).click()
    expect(page.get_by_role('button', name='Pause stream', exact=True)).to_be_enabled()
    page.get_by_role('button', name='Send synthetic sample', exact=True).click()


def open_event_delivery(page):
    button = page.get_by_label('Live decisions').get_by_role('button', name=re.compile('^Delivery '))
    expect(button).to_be_visible(timeout=10000)
    button.click()
    expect(page.get_by_label('Delivery details', exact=True)).to_be_visible()


def test_live_default_is_local_and_exact_signal_projection_reaches_receiver(delivery_page):
    page, _app, state, errors = delivery_page
    expect(page.get_by_label('Send selected outputs to a registered destination', exact=True)).not_to_be_checked()
    assert state['sends'] == []
    configure_delivery(page)
    expect(page.locator('.studio-live')).to_contain_text('Pinned destination: https://receiver.invalid/decisions · revision 1 · outputs: assess.signal')
    submitted = [body for url, _, body in state['posts'] if url.endswith('/subscriptions')][-1]
    assert submitted['destinations'][0] == {'kind': 'live'}
    assert submitted['destinations'][1]['outputs'] == [{'node': 'assess', 'port': 'signal'}]
    assert set(submitted['destinations'][1]) == {'kind', 'id', 'revision', 'outputs'}
    assert submitted['destinations'][1]['revision'] == 1
    start_sample(page)
    expect(page.get_by_label('Live decisions')).to_contain_text('Event 1 · done', timeout=10000)
    assert state['model_calls'] == ['assess'] and len(state['sends']) == 1
    target, envelope = state['sends'][0]
    assert target['url'] == 'https://receiver.invalid/decisions'
    assert [(item['node'], item['port']) for item in envelope['body']['outputs']] == [('assess', 'signal')]
    assert 'untrusted_record' not in json.dumps(envelope['body'])
    assert all(item['port'] != 'record' for item in envelope['body']['outputs'])
    assert not errors


def test_uncertain_attestation_and_explicit_retry_keep_one_fixed_intent(delivery_page):
    page, _app, state, errors = delivery_page
    state['mode'] = 'interrupted'
    configure_delivery(page)
    start_sample(page)
    expect(page.get_by_label('Live decisions')).to_contain_text('interrupted', timeout=10000)
    open_event_delivery(page)
    panel = page.get_by_label('Delivery details', exact=True)
    expect(panel.get_by_role('button', name='Attest not delivered', exact=True)).to_be_enabled()
    assert len(state['sends']) == 1
    panel.get_by_role('button', name='Attest not delivered', exact=True).click()
    expect(panel).to_contain_text('Owner attestation: not_delivered')
    expect(panel.get_by_role('button', name='Retry this exact delivery', exact=True)).to_be_enabled()
    assert len(state['sends']) == 1
    state['mode'] = 'done'
    panel.get_by_role('button', name='Retry this exact delivery', exact=True).click()
    expect(panel).to_contain_text('Delivery · done', timeout=10000)
    assert len(state['sends']) == 2
    assert state['sends'][0] == state['sends'][1]
    attest = next(body for url, _, body in state['posts'] if url.endswith('/reconcile'))
    retried = next(body for url, _, body in state['posts'] if url.endswith('/retry'))
    assert attest['evidence'] == 'owner_attestation'
    assert all(key not in retried for key in ('url', 'body', 'headers'))
    assert retried['delivery_id'] == attest['delivery_id'] and retried['request_digest'] == attest['request_digest']
    assert not errors


def test_changed_destination_never_rebinds_existing_stream_or_draft(delivery_page):
    page, _app, state, errors = delivery_page
    configure_delivery(page)
    page.get_by_label('Destination URL (HTTPS)', exact=True).fill('https://other.invalid/decisions')
    page.get_by_role('button', name='Save destination', exact=True).click()
    expect(page.locator('.studio-live [role="status"]')).to_contain_text('Destination saved at revision 2')
    expect(page.locator('.studio-live')).to_contain_text('Pinned destination: https://receiver.invalid/decisions · revision 1')
    expect(page.get_by_label('Delivery destination', exact=True)).to_have_value(re.compile(':1$'))
    page.get_by_role('button', name='Create paused stream', exact=True).click()
    expect(page.locator('.studio-live [role="status"]')).to_contain_text('Choose a current registered destination revision')
    submitted = [body for url, _, body in state['posts'] if url.endswith('/subscriptions')]
    assert len(submitted) == 1
    assert not state['sends']
    page.get_by_role('button', name='Revoke destination', exact=True).click()
    expect(page.locator('.studio-live [role="status"]')).to_contain_text('Destination revoked')
    expect(page.get_by_role('button', name='Save destination', exact=True)).to_be_disabled()
    assert not state['sends'] and not errors


def test_cancelled_stream_retains_owner_history_without_resuming_or_sending(delivery_page):
    page, _app, state, errors = delivery_page
    state['mode'] = 'interrupted'
    configure_delivery(page)
    start_sample(page)
    expect(page.get_by_label('Live decisions')).to_contain_text('interrupted', timeout=10000)
    page.get_by_role('button', name='Cancel stream', exact=True).click()
    expect(page.locator('.studio-live')).to_contain_text(' · cancelled · ')
    page.get_by_role('button', name='Refresh streams', exact=True).click()
    pending = page.get_by_label('Pending deliveries').get_by_role('button', name=re.compile('^Pending delivery '))
    expect(pending).to_be_visible()
    pending.click()
    panel = page.get_by_label('Delivery details', exact=True)
    panel.get_by_role('button', name='Attest delivered', exact=True).click()
    expect(panel).to_contain_text('Owner attestation: delivered')
    expect(panel.get_by_role('button', name='Retry this exact delivery', exact=True)).to_have_count(0)
    expect(page.get_by_role('button', name='Start stream', exact=True)).to_have_count(0)
    assert len(state['sends']) == 1 and not errors


def test_narrow_keyboard_configuration_has_no_implicit_output_or_sticky_workflow(delivery_page):
    page, _app, state, errors = delivery_page
    page.set_viewport_size({'width': 390, 'height': 844})
    register_destination(page)
    enabled = page.get_by_label('Send selected outputs to a registered destination', exact=True)
    enabled.focus()
    enabled.press('Space')
    page.get_by_label('Delivery destination', exact=True).select_option(index=1)
    assert page.get_by_label('Outputs to send').locator('input:checked').count() == 0
    page.get_by_role('button', name='Create paused stream', exact=True).click()
    expect(page.locator('.studio-live [role="status"]')).to_contain_text('Choose the exact current output ports to send')
    assert not any(url.endswith('/subscriptions') for url, _, _ in state['posts'])
    signal = page.get_by_label('assess · signal (json)', exact=True)
    signal.focus()
    signal.press('Space')
    expect(signal).to_be_checked()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.get_by_role('button', name='New', exact=True).click()
    expect(enabled).not_to_be_checked()
    assert page.get_by_label('Outputs to send').locator('input:checked').count() == 0
    assert not state['sends'] and not errors


@pytest.mark.parametrize('change', ['new-draft', 'new-workflow'])
def test_late_destination_save_does_not_replace_newer_editor_state(delivery_page, change):
    page, _app, _state, errors = delivery_page
    page.get_by_text('Destinations and selected outputs', exact=True).click()
    page.get_by_text('Manage registered destinations', exact=True).click()
    page.get_by_label('Destination name', exact=True).fill('Submitted receiver')
    page.get_by_label('Destination URL (HTTPS)', exact=True).fill('https://receiver.invalid/submitted')
    held = []
    def delay_ack(route):
        response = route.fetch()
        held.append((route, response))
        page.evaluate('window.deliveryAckHeld = true')
    page.route('**/ui/studio/destinations', delay_ack)
    page.get_by_role('button', name='Register destination', exact=True).click()
    page.wait_for_function('() => window.deliveryAckHeld === true')
    if change == 'new-workflow':
        page.get_by_role('button', name='New', exact=True).click()
        expect(page.get_by_label('Workflow title', exact=True)).to_have_value('Untitled experiment')
    page.get_by_label('Destination name', exact=True).fill('Newer draft')
    page.get_by_label('Destination URL (HTTPS)', exact=True).fill('https://other.invalid/newer')
    route, response = held[0]
    route.fulfill(response=response)
    page.wait_for_function('() => !document.querySelector("studio-view").liveBusy')
    expect(page.get_by_label('Destination name', exact=True)).to_have_value('Newer draft')
    expect(page.get_by_label('Destination URL (HTTPS)', exact=True)).to_have_value('https://other.invalid/newer')
    expect(page.get_by_role('button', name='Register destination', exact=True)).to_be_enabled()
    assert not errors


def test_concurrent_destination_revision_change_rejects_stream_creation(delivery_page):
    page, app, state, errors = delivery_page
    register_destination(page)
    page.get_by_label('Send selected outputs to a registered destination', exact=True).check()
    page.get_by_label('Delivery destination', exact=True).select_option(index=1)
    page.get_by_label('assess · signal (json)', exact=True).check()
    changed = []
    def change_before_create(route):
        target = route.request.post_data_json['destinations'][1]
        status, result, _ = app.call('/ui/studio/destinations/' + target['id'], method='PUT', cookie=app.cookie,
            body={'label': 'Changed receiver', 'url': 'https://other.invalid/new', 'revision': 1, 'idempotency_key': 'concurrent-update'})
        assert status == 200 and result['destination']['revision'] == 2
        changed.append(target)
        route.continue_()
    page.route('**/ui/studio/subscriptions', change_before_create)
    page.get_by_role('button', name='Create paused stream', exact=True).click()
    expect(page.locator('.studio-live [role="status"]')).to_contain_text('changed', ignore_case=True)
    assert changed[0]['revision'] == 1
    expect(page.get_by_label('Delivery destination', exact=True)).to_have_value(re.compile(':1$'))
    expect(page.get_by_label('Delivery destination', exact=True).locator('option:checked')).to_have_attribute('disabled', '')
    assert app.ui.studio_streams.store.active() == []
    assert not state['sends'] and not state['model_calls'] and not errors
