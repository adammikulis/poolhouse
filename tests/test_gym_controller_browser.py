"""Explicit Gym controller drafts and existing setup transport, without compute."""

import json
import re

import pytest
from playwright.sync_api import expect
from test_gym_world_ui import (  # noqa: F401
    browser,
    daemon,
    gym_page,
    joined,
    no_release_lookup,
    open_page,
)

pytestmark = pytest.mark.slow


def live_controller(page):
    page.evaluate("""() => {const g=document.querySelector('gym-view');
      clearInterval(g.timer);g.stream=async()=>{};g.session='controlled';
      g.controller.value='decider';g.modelOptions.choices({decision:[{id:'strands',label:'Default',checkpoint:null},{id:'trained',label:'Trained example',checkpoint:'/models/trained'}],vision:[]});g.draw({environment:'car',controller:'decider',status:'paused',sequence:0});
    }""")


def snapshot(controller='native-idm'):
    return {'environment': 'car', 'controller': controller, 'status': 'paused', 'sequence': 1}


def test_controller_draft_waits_for_apply_and_late_ack_keeps_newer_selection(gym_page):  # noqa: F811
    page, errors = gym_page
    held = []
    page.route('**/ui/gym/sessions/controlled', lambda route: held.append(route))
    live_controller(page)
    page.get_by_label('Decision model', exact=True).select_option('trained')
    page.locator('gym-view .gym-advanced > summary').click()
    page.get_by_label('Decision device', exact=True).select_option('cpu')
    page.get_by_label('Maximum decision input age (seconds)', exact=True).fill('2')
    page.get_by_label('Trained decision checkpoint directory', exact=True).fill('/models/trained')
    page.get_by_label('Trained decision checkpoint directory', exact=True).press('Tab')
    page.get_by_label('Controller', exact=True).select_option('native-idm')
    assert not held
    page.get_by_role('button', name='Apply controller', exact=True).click()
    page.wait_for_function("() => document.querySelector('gym-view').controllerOperation != null")
    assert len(held) == 1
    assert held[0].request.post_data_json['payload']['controller'] == 'native-idm'
    assert held[0].request.post_data_json['payload']['decision_device'] == 'cpu'
    assert held[0].request.post_data_json['payload']['decision_max_age_s'] == 2
    assert held[0].request.post_data_json['payload']['decision_checkpoint'] == '/models/trained'
    assert page.get_by_role('button', name='Apply controller', exact=True).is_disabled()
    page.evaluate("document.querySelector('gym-view').apply()")
    assert len(held) == 1
    page.get_by_label('Controller', exact=True).select_option('manual')
    assert not page.get_by_role('button', name='Apply manual action', exact=True).is_visible()
    assert not page.get_by_label('Manual action', exact=True).is_visible()
    held[0].fulfill(json=snapshot())
    page.wait_for_function("() => !document.querySelector('gym-view').controllerOperation")
    assert page.get_by_label('Controller', exact=True).input_value() == 'manual'
    expect(page.locator('gym-view').get_by_role('status').filter(has_text='Selected settings')).to_contain_text(
        'Current controller: decider')
    page.get_by_role('button', name='Single step', exact=True).click()
    assert len(held) == 2
    assert held[1].request.post_data_json['command'] == 'step'
    held[1].fulfill(json=snapshot('decider'))
    page.wait_for_function("() => document.querySelector('gym-view').lastSnapshot.sequence === 1")
    assert not errors


@pytest.mark.parametrize('status', [403, 500])
def test_failed_apply_keeps_newer_draft_and_uncertain_outcome_requires_refresh(gym_page, status):  # noqa: F811
    page, errors = gym_page
    held = []
    page.route('**/ui/gym/sessions/controlled', lambda route: held.append(route))
    live_controller(page)
    page.get_by_label('Controller', exact=True).select_option('native-idm')
    page.get_by_role('button', name='Apply controller', exact=True).click()
    page.get_by_label('Controller', exact=True).select_option('manual')
    held[0].fulfill(status=status, content_type='application/json', body=json.dumps({'error': 'Update rejected'}))
    page.wait_for_function("() => !document.querySelector('gym-view').controllerOperation")
    assert page.get_by_label('Controller', exact=True).input_value() == 'manual'
    apply = page.get_by_role('button', name='Apply controller', exact=True)
    if status == 500:
        assert apply.is_disabled()
        page.evaluate("document.querySelector('gym-view').apply()")
        assert len(held) == 1
        page.get_by_role('button', name='Refresh controller status', exact=True).click()
        assert held[1].request.method == 'GET'
        held[1].fulfill(json=snapshot())
        expect(apply).to_be_enabled()
        assert len(held) == 2
    else:
        expect(apply).to_be_enabled()
    assert not errors


def test_apply_ack_from_previous_session_cannot_change_current_world(gym_page):  # noqa: F811
    page, errors = gym_page
    held = []
    page.route('**/ui/gym/sessions/controlled', lambda route: held.append(route))
    live_controller(page)
    page.get_by_role('button', name='Apply controller', exact=True).click()
    page.evaluate("""() => {const g=document.querySelector('gym-view');g.session='replacement';
      g.draw({environment:'car',controller:'manual',status:'paused',sequence:4});}""")
    held[0].fulfill(json=snapshot())
    page.wait_for_function("() => !document.querySelector('gym-view').controllerOperation")
    assert page.evaluate("document.querySelector('gym-view').lastSnapshot.controller") == 'manual'
    assert page.evaluate("document.querySelector('gym-view').session") == 'replacement'
    assert not errors


@pytest.mark.parametrize('transition', ['stay', 'route', 'draft', 'generation'])
def test_default_model_setup_uses_fixed_job_and_never_applies_controller(gym_page, transition):  # noqa: F811
    page, errors = gym_page
    installs, pending, commands = [], [], []
    page.route('**/ui/studio/resources/decision/install', lambda route: (
        installs.append(route.request.post_data_json), route.fulfill(json={'job': {'id': 'decision-setup', 'state': 'queued'}})))
    page.route('**/ui/setup/jobs', lambda route: pending.append(route))
    page.route('**/ui/gym/sessions/controlled', lambda route: (
        commands.append(route.request.method), route.fulfill(json=snapshot())))
    if transition == 'generation':
        page.evaluate('window.gymAuthGeneration=0;window.fleetModel.sessionContext=()=>window.gymAuthGeneration')
    live_controller(page)
    assert not installs
    page.locator('gym-view .gym-advanced > summary').click()
    button = page.get_by_role('button', name=re.compile(r'^(Set up default decision model|Setting up decision model…)$'))
    with page.expect_request(lambda request: request.url.endswith('/ui/setup/jobs')):
        button.click()
        expect(button).to_be_disabled()
        page.evaluate("document.querySelector('gym-view').installDefaultDecision()")
        assert installs == [{}]
        page.wait_for_function("() => document.querySelector('gym-view').note.textContent === 'queued'")
        if transition == 'route':
            page.evaluate("window.fleetModel.go('chat')")
        elif transition == 'draft':
            page.get_by_label('Controller', exact=True).select_option('manual')
        elif transition == 'generation':
            page.evaluate('window.gymAuthGeneration++')
        if transition != 'stay':
            page.evaluate("document.querySelector('gym-view').note.textContent='New context'")
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(resolve))')
    assert len(pending) == 1
    pending[0].fulfill(json={'jobs': [{'id': 'decision-setup', 'state': 'done', 'result': {}}]})
    page.wait_for_function("() => !document.querySelector('gym-view').decisionInstall")
    assert not commands
    assert page.get_by_label('Controller', exact=True).input_value() == ('manual' if transition == 'draft' else 'decider')
    if transition == 'stay':
        expect(page.locator('#gym-note')).to_contain_text('ready. Apply it')
    else:
        assert page.locator('#gym-note').text_content() == 'New context'
    assert not errors


@pytest.mark.parametrize('transition', ['draft', 'world', 'route', 'generation'])
def test_stale_unauthorized_reply_cannot_replace_newer_context(gym_page, transition):  # noqa: F811
    page, errors = gym_page
    held = []
    page.route('**/ui/gym/sessions/controlled', lambda route: held.append(route))
    if transition == 'generation':
        page.evaluate('window.gymAuthGeneration=0;window.fleetModel.sessionContext=()=>window.gymAuthGeneration')
    live_controller(page)
    page.get_by_role('button', name='Apply controller', exact=True).click()
    if transition == 'draft':
        page.get_by_label('Controller', exact=True).select_option('manual')
    elif transition == 'world':
        page.get_by_label('Environment', exact=True).select_option('warehouse')
    elif transition == 'route':
        page.evaluate("window.fleetModel.go('chat')")
    else:
        page.evaluate('window.gymAuthGeneration++')
    held[0].fulfill(status=401, json={'error': 'Expired old operation'})
    page.wait_for_function("() => !document.querySelector('gym-view').controllerOperation")
    assert page.evaluate('window.fleetModel.route') == ('chat' if transition == 'route' else 'gym')
    if transition == 'draft':
        assert page.get_by_label('Controller', exact=True).input_value() == 'manual'
    elif transition == 'world':
        assert page.get_by_label('Environment', exact=True).input_value() == 'warehouse'
    assert not errors


def test_telemetry_cannot_refill_a_cleared_pending_checkpoint(gym_page):  # noqa: F811
    page, errors = gym_page
    live_controller(page)
    page.get_by_label('Controller', exact=True).select_option('ppo')
    page.get_by_label('PPO checkpoint path').fill('/models/saved-policy.zip')
    page.get_by_label('PPO checkpoint path').fill('')
    page.evaluate("""() => document.querySelector('gym-view').draw({environment:'car',controller:'ppo',
      status:'paused',checkpoint:'/models/saved-policy.zip',sequence:2})""")
    assert page.get_by_label('PPO checkpoint path').input_value() == ''
    assert not errors


def test_uncertain_apply_after_navigation_retains_reconciliation_without_stale_note(gym_page):  # noqa: F811
    page, errors = gym_page
    held = []
    page.route('**/ui/gym/sessions/controlled', lambda route: held.append(route))
    live_controller(page)
    page.get_by_role('button', name='Apply controller', exact=True).click()
    page.evaluate("window.fleetModel.go('chat');document.querySelector('gym-view').note.textContent='New context'")
    held[0].fulfill(status=500, json={'error': 'Uncertain old operation'})
    page.wait_for_function("() => !document.querySelector('gym-view').controllerOperation")
    assert page.evaluate("document.querySelector('gym-view').controllerUnknown")
    assert page.locator('#gym-note').text_content() == 'New context'
    assert page.evaluate('window.fleetModel.route') == 'chat'
    assert not errors
