"""Shared token controls supply their own single accessible field label."""
from pathlib import Path
from tempfile import mkdtemp

import pytest
from browser_expect import expect
from test_models_settings_feedback_browser import feedback_page  # noqa: F401
from test_output_token_slider_browser import generation_page  # noqa: F401
from test_studio_canvas_browser import add, studio_browser, studio_page  # noqa: F401

from poolhouse.fleet.conversations import Conversations
from poolhouse.fleet.themes import PRESETS

pytestmark = pytest.mark.slow


def snapshot(page, name):
    path=Path(mkdtemp(prefix='poolhouse-token-label-png-')).resolve()/name
    page.screenshot(path=str(path))
    print('VISUAL_PNG',path,flush=True)


@pytest.mark.parametrize('theme',['light','dark'])
def test_chat_token_control_has_one_label_and_one_zero_hint(feedback_page,tmp_path,theme):  # noqa: F811
    served,page,errors=feedback_page
    served.ui.conversations=Conversations(tmp_path/'chats')
    served.ui.conversations.start(model='offline-model',title='Single token label')
    page.goto(f'http://127.0.0.1:{served.port}/ui/#chat')
    page.get_by_role('link',name='Single token label',exact=True).click()
    page.locator('#conversation-details').click()
    cap=page.locator('#chat-output-tokens').get_by_role('slider',name='Maximum output tokens',exact=True)
    expect(cap).to_be_enabled()
    page.evaluate('theme => fleetModel.setPreferences({...fleetModel.preferences,resolved_theme:theme})',PRESETS[theme])
    snapshot(page,f'{theme}-chat-single-label.png')
    expect(page.locator('#chat-options').get_by_text('Maximum output tokens',exact=True)).to_have_count(1)
    expect(page.locator('#chat-options').get_by_text('Select 0 for no user cap.',exact=False)).to_have_count(1)
    assert not errors


@pytest.mark.parametrize('theme',['light','dark'])
def test_studio_token_control_has_one_label_in_both_hosts(generation_page,theme):  # noqa: F811
    page,_state,errors=generation_page
    add(page,'Generate')
    page.evaluate('theme => fleetModel.setPreferences({...fleetModel.preferences,resolved_theme:theme})',PRESETS[theme])
    snapshot(page,f'{theme}-studio-single-label.png')
    for host in [page.locator('.studio-node'),page.locator('.studio-inspector')]:
        expect(host.get_by_role('slider',name='max_tokens',exact=True)).to_be_visible()
        expect(host.get_by_text('max_tokens',exact=True)).to_have_count(1)
    assert not errors
