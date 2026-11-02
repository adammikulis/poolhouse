"""UI contract journeys with explicitly mocked software-producer transport.

The page, authentication, saved workflow and other Studio routes are real. These
checks do not prove SDK identity, provisioning, ingress or separate-process data.
"""
from __future__ import annotations

import copy
import json
import re
from urllib.parse import urlsplit

import pytest
import test_studio_live_browser
from browser_expect import expect
from test_studio_canvas_browser import studio_browser  # noqa: F401
from test_studio_live_browser import open_live

live_page = test_studio_live_browser.live_page
pytestmark = pytest.mark.slow
PROVIDER = 'software-producer'


def offer_value(offer):
    return json.dumps([offer['id'], offer['revision']], separators=(',', ':'))


@pytest.fixture
def producer_page(request):
    page, app, errors, posts = request.getfixturevalue('live_page')
    offers = [{'id': f'fixture-offer-{n}', 'revision': n, 'label': f'Fixture publisher {n}',
                   'device_label': 'Fixture device', 'software_label': f'Fixture software {n}',
                   'available': True, 'reason': ''} for n in (1, 2)]
    state = {'offers': offers, 'source': None, 'commands': [], 'held': [], 'hold': None,
                 'refresh_hold': False, 'pending': False}

    def source(label):
        return {'id': 'fixture-source', 'revision': 1, 'kind': 'webhook', 'provider': PROVIDER,
                    'label': label, 'available': True, 'formats': ['json'], 'requirements': [],
                    'connection': {'state': 'interrupted' if state['pending'] else 'ready',
                                    'producer': {key: offers[0][key] for key in
                                              ('id', 'label', 'device_label', 'software_label')},
                                    'reason': '', 'can_connect': True, 'can_rotate': True, 'can_revoke': True,
                                    'can_check': state['pending'], 'pending': ({'idempotency_key': 'original-intent'}
                                                                      if state['pending'] else None)}}

    def transport(route):
        path = urlsplit(route.request.url).path
        if path.endswith('/sources') and route.request.method == 'GET':
            inventory = route.fetch().json()
            inventory['providers'].append({'id': PROVIDER, 'label': 'Software producer', 'available': True,
                                               'requirements': [], 'offers': state['offers']})
            if state['source']:
                inventory['sources'].append(copy.deepcopy(state['source']))
            if state['refresh_hold']:
                state['held'].append((route, inventory))
                page.evaluate('window.producerHeld = true')
                return
            route.fulfill(json=inventory)
            return
        if route.request.method != 'POST':
            route.continue_()
            return
        body = route.request.post_data_json
        state['commands'].append((path, copy.deepcopy(body)))
        if path.endswith('/sources'):
            assert body['provider'] == PROVIDER
            state['source'] = source(body['label'])
        else:
            action = path.rsplit('/', 1)[-1]
            assert action in ('connect', 'rotate', 'revoke', 'connection-status')
            assert body['revision'] == state['source']['revision']
            state['source']['revision'] += 1
            connection = state['source']['connection']
            if action == 'connect':
                selected = next(row for row in state['offers'] if row['id'] == body['producer']['id'])
                connection['producer'] = {key: selected[key] for key in
                                          ('id', 'label', 'device_label', 'software_label')}
            connection.update(state='revoked' if action == 'revoke' else 'ready', pending=None, can_check=False)
            if action == 'revoke':
                connection.update(can_rotate=False, can_revoke=False)
                state['source']['available'] = False
        payload = {'source': copy.deepcopy(state['source'])}
        if state['hold'] == path.rsplit('/', 1)[-1]:
            state['held'].append((route, payload))
            page.evaluate('window.producerHeld = true')
        else:
            route.fulfill(status=201 if path.endswith('/sources') else 200, json=payload)

    page.route(re.compile(r'.*/ui/studio/sources(?:/[^?]*)?$'), transport)
    yield page, app, errors, posts, state


def choose_producer(page, state):
    open_live(page)
    page.get_by_text('Add a webhook source', exact=True).click()
    page.get_by_label('Webhook source provider', exact=True).select_option(PROVIDER)
    page.get_by_label('Live software producer offer', exact=True).select_option(offer_value(state['offers'][0]))
    page.get_by_label('Webhook source name', exact=True).fill('Fixture software source')


def create_producer(page, state):
    choose_producer(page, state)
    page.get_by_role('button', name='Connect publisher', exact=True).click()
    expect(page.get_by_label('Live source', exact=True)).to_have_value('fixture-source')
    expect(page.get_by_label('Software producer connection', exact=True)).to_be_visible()


def release_held(state, *, status=200):
    route, payload = state['held'].pop(0)
    route.fulfill(status=status, json=payload)


def test_mock_contract_create_is_explicit_and_never_starts_or_sends(producer_page):
    page, _app, errors, posts, state = producer_page
    choose_producer(page, state)
    assert not state['commands']
    page.get_by_role('button', name='Connect publisher', exact=True).click()
    expect(page.get_by_label('Live source', exact=True)).to_have_value('fixture-source')
    body = state['commands'][0][1]
    assert set(body) == {'kind', 'provider', 'label', 'producer', 'idempotency_key'}
    assert body['producer'] == {'id': 'fixture-offer-1', 'revision': 1}
    expect(page.get_by_label('Software producer connection', exact=True)).to_contain_text('Fixture software 1')
    assert not any(url.endswith(('/start', '/test-webhook', '/runs')) for url, _ in posts)
    expect(page.get_by_role('button', name='Send test webhook', exact=True)).to_have_count(0)
    assert not errors


def test_mock_contract_exact_revisions_and_original_labels(producer_page):
    page, _app, errors, _posts, state = producer_page
    create_producer(page, state)
    panel = page.get_by_label('Software producer connection', exact=True)
    page.get_by_label('Replacement software producer offer', exact=True).select_option(offer_value(state['offers'][1]))
    expect(panel).to_contain_text('Source producer: Fixture publisher 1')
    page.get_by_role('button', name='Connect selected producer', exact=True).click()
    expect(panel).to_contain_text('Source producer: Fixture publisher 2')
    page.get_by_role('button', name='Rotate producer binding', exact=True).click()
    expect(panel).to_contain_text('source revision 3')
    page.get_by_role('button', name='Revoke producer binding', exact=True).click()
    expect(panel).to_contain_text('Software producer · revoked')
    actions = state['commands'][1:]
    assert [path.rsplit('/', 1)[-1] for path, _ in actions] == ['connect', 'rotate', 'revoke']
    assert [body['revision'] for _, body in actions] == [1, 2, 3]
    assert actions[0][1]['producer'] == {'id': 'fixture-offer-2', 'revision': 2}
    assert all(set(body) == {'revision', 'idempotency_key'} for _, body in actions[1:])
    assert not errors


def test_mock_contract_receipt_check_uses_server_pending_key(producer_page):
    page, _app, errors, _posts, state = producer_page
    state['pending'] = True
    create_producer(page, state)
    assert len(state['commands']) == 1
    page.get_by_role('button', name='Check connection status', exact=True).click()
    expect(page.get_by_label('Software producer connection', exact=True)).to_contain_text('Software producer · ready')
    path, body = state['commands'][-1]
    assert path.endswith('/connection-status')
    assert body == {'revision': 1, 'idempotency_key': 'original-intent'}
    assert len(state['commands']) == 2
    assert not errors


def test_mock_contract_missing_offer_revision_is_retained_without_replacement(producer_page):
    page, _app, errors, _posts, state = producer_page
    choose_producer(page, state)
    selected = page.get_by_label('Live software producer offer', exact=True).input_value()
    state['offers'] = [state['offers'][1]]
    page.get_by_role('button', name='Refresh streams', exact=True).click()
    offer = page.get_by_label('Live software producer offer', exact=True)
    expect(offer).to_have_value(selected)
    expect(offer.locator('option:checked')).to_have_attribute('disabled', '')
    expect(page.get_by_role('button', name='Connect publisher', exact=True)).to_be_disabled()
    expect(page.get_by_label('Webhook source provider', exact=True)).to_have_value(PROVIDER)
    assert not state['commands']
    assert not errors


@pytest.mark.parametrize('phase', ['action', 'refresh'])
def test_mock_contract_busy_controls_disable_in_place(producer_page, phase):
    page, _app, errors, _posts, state = producer_page
    create_producer(page, state)
    if phase == 'action':
        state['hold'] = 'rotate'
    else:
        state['refresh_hold'] = True
    button = page.get_by_role('button', name='Rotate producer binding', exact=True)
    original = button.element_handle()
    button.focus()
    button.press('Enter')
    page.wait_for_function('() => document.querySelector("studio-view").liveBusy')
    expect(button).to_be_disabled()
    assert original.evaluate('(button) => button.isConnected')
    expect(page.get_by_role('button', name='Revoke producer binding', exact=True)).to_be_disabled()
    page.wait_for_function('() => window.producerHeld === true')
    assert len(state['commands']) == 2
    state['refresh_hold'] = False
    release_held(state)
    expect(page.get_by_role('button', name='Rotate producer binding', exact=True)).to_be_enabled()
    assert not errors


@pytest.mark.parametrize('phase', ['action', 'refresh'])
@pytest.mark.parametrize('change', ['new-draft', 'new-workflow', 'current'])
def test_mock_contract_delayed_denial_is_fenced_to_submitted_context(producer_page, phase, change):
    page, _app, errors, _posts, state = producer_page
    create_producer(page, state)
    state['hold'] = 'rotate' if phase == 'action' else None
    state['refresh_hold'] = phase == 'refresh'
    page.get_by_role('button', name='Rotate producer binding', exact=True).click()
    page.wait_for_function('() => window.producerHeld === true')
    if change == 'new-workflow':
        page.get_by_role('button', name='New', exact=True).click()
        expect(page.get_by_label('Workflow title', exact=True)).to_have_value('Untitled experiment')
    elif change == 'new-draft':
        page.get_by_label('Webhook source name', exact=True).fill('Newer source draft')
    note = page.locator('.studio-live [role="status"]')
    previous = note.inner_text()
    state['refresh_hold'] = False
    route, _payload = state['held'].pop(0)
    route.fulfill(status=403, json={'error': 'Controlled producer denial'})
    page.wait_for_function('() => !document.querySelector("studio-view").liveBusy')
    if change == 'current':
        expect(note).to_contain_text('Controlled producer denial')
        expect(page.get_by_label('Live source', exact=True).locator('option')).to_have_count(0)
    else:
        expect(note).to_have_text(previous)
        expect(page.get_by_label('Live source', exact=True).locator('option')).not_to_have_count(0)
        if change == 'new-draft':
            expect(page.get_by_label('Webhook source name', exact=True)).to_have_value('Newer source draft')
    assert len(state['commands']) == 2
    assert not errors


@pytest.mark.parametrize('phase', ['action', 'refresh'])
def test_mock_contract_delayed_create_keeps_new_offer_draft(producer_page, phase):
    page, _app, errors, _posts, state = producer_page
    choose_producer(page, state)
    if phase == 'action':
        state['hold'] = 'sources'
    else:
        state['refresh_hold'] = True
    page.get_by_role('button', name='Connect publisher', exact=True).click()
    page.wait_for_function('() => window.producerHeld === true')
    page.get_by_label('Webhook source name', exact=True).fill('New source draft')
    page.get_by_label('Live software producer offer', exact=True).select_option(offer_value(state['offers'][1]))
    state['refresh_hold'] = False
    release_held(state, status=201 if phase == 'action' else 200)
    page.wait_for_function('() => !document.querySelector("studio-view").liveBusy')
    expect(page.get_by_label('Webhook source name', exact=True)).to_have_value('New source draft')
    expect(page.get_by_label('Live software producer offer', exact=True)).to_have_value(offer_value(state['offers'][1]))
    expect(page.get_by_label('Live source', exact=True)).not_to_have_value('fixture-source')
    assert len(state['commands']) == 1
    assert not errors


def test_mock_contract_narrow_keyboard_and_untrusted_labels(producer_page):
    page, _app, errors, posts, state = producer_page
    page.set_viewport_size({'width': 390, 'height': 844})
    state['offers'][0]['software_label'] = '<img src=x onerror=window.injected=true>'
    choose_producer(page, state)
    name = '<svg onload=window.injected=true>'
    page.get_by_label('Webhook source name', exact=True).fill(name)
    button = page.get_by_role('button', name='Connect publisher', exact=True)
    button.focus()
    button.press('Enter')
    expect(page.get_by_label('Software producer connection', exact=True)).to_contain_text(name)
    assert page.evaluate('window.injected') is None
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert len(state['commands']) == 1
    assert not any(url.endswith('/start') for url, _ in posts)
    assert not errors
