"""PNG evidence for token controls through visible private-browser interactions."""
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


CAPTURE_DIRS = {}


def capture(page, path):
    if path.parent not in CAPTURE_DIRS:
        CAPTURE_DIRS[path.parent] = Path(mkdtemp(prefix="poolhouse-token-png-")).resolve()
    target = CAPTURE_DIRS[path.parent] / path.name
    page.screenshot(path=str(target))
    print('VISUAL_PNG', target, flush=True)


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_settings_and_chat_token_png_journey(feedback_page, tmp_path, theme):  # noqa: F811
    served, page, errors = feedback_page
    served.ui.settings.context = 131072
    served.ui.settings.chat_max_output_tokens = 42013
    served.ui.conversations = Conversations(tmp_path / 'chats')
    conversation = served.ui.conversations.start(model='offline-model',title='Visual token check')
    page.goto(f'http://127.0.0.1:{served.port}/ui/#settings')
    cap = page.locator('#settings-tokens output-token-slider').get_by_role('slider')
    expect(cap).to_have_attribute('aria-valuetext','Custom · 42,013 tokens')
    page.evaluate('theme => fleetModel.setPreferences({...fleetModel.preferences,resolved_theme:theme})', PRESETS[theme])
    capture(page,tmp_path/f'{theme}-01-settings-initial.png')
    box = cap.bounding_box()
    page.mouse.move(box['x']+8+(box['width']-16)*3/9,box['y']+box['height']/2)
    page.mouse.down()
    page.mouse.up()
    expect(cap).to_have_attribute('aria-valuetext','2,048 tokens')
    capture(page,tmp_path/f'{theme}-02-settings-2k.png')
    cap.press('ArrowRight')
    expect(cap).to_have_attribute('aria-valuetext','4,096 tokens')
    capture(page,tmp_path/f'{theme}-03-settings-4k.png')
    page.get_by_role('button',name='Discard preferences',exact=True).click()
    expect(cap).to_have_attribute('aria-valuetext','Custom · 42,013 tokens')
    capture(page,tmp_path/f'{theme}-04-settings-custom-restored.png')
    page.get_by_role('navigation',name='Main navigation').get_by_role('link',name='Conversations',exact=True).click()
    page.get_by_role('link',name='Settings',exact=True).click()
    expect(cap).to_have_attribute('aria-valuetext','Custom · 42,013 tokens')
    capture(page,tmp_path/f'{theme}-05-settings-return.png')
    page.get_by_role('navigation',name='Main navigation').get_by_role('link',name='Conversations',exact=True).click()
    page.get_by_role('link',name='Visual token check',exact=True).click()
    page.locator('#conversation-details').click()
    chat = page.locator('#chat-output-tokens').get_by_role('slider')
    expect(chat).to_be_visible()
    expect(chat).to_be_enabled()
    saves=[]
    page.on('request',lambda request:saves.append(request.post_data_json)
            if request.method=='POST' and request.url.endswith(f'/ui/conversations/{conversation.id}') else None)
    box=chat.bounding_box()
    page.mouse.move(box['x']+box['width']*.7,box['y']+box['height']/2)
    page.mouse.down()
    page.mouse.move(box['x']+box['width']*.4,box['y']+box['height']/2)
    assert not saves
    capture(page,tmp_path/f'{theme}-06-chat-drag-held.png')
    with page.expect_request(f'**/ui/conversations/{conversation.id}'):
        page.mouse.up()
    page.locator('chat-view').evaluate('async node => await node.savePending')
    assert len(saves)==1
    capture(page,tmp_path/f'{theme}-07-chat-released.png')
    assert not errors


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_studio_token_png_journey(generation_page, tmp_path, theme):  # noqa: F811
    page,_state,errors=generation_page
    add(page,'Generate')
    page.evaluate('theme => fleetModel.setPreferences({...fleetModel.preferences,resolved_theme:theme})',PRESETS[theme])
    cap=page.locator('.studio-inspector').get_by_role('slider',name='max_tokens',exact=True)
    cap.press('End')
    expect(page.locator('.studio-node').get_by_role('slider',name='max_tokens',exact=True)).to_be_visible()
    capture(page,tmp_path/f'{theme}-08-studio-inline-inspector.png')
    page.set_viewport_size({'width':390,'height':1000})
    expect(cap).to_be_visible()
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    capture(page,tmp_path/f'{theme}-09-studio-narrow-inspector.png')
    inspector=page.locator('.studio-inspector').bounding_box()
    page.mouse.move(inspector['x']+inspector['width']/2,inspector['y']+inspector['height']/2)
    page.mouse.wheel(0,350)
    page.wait_for_function("() => {const box=document.querySelector('.studio-inspector output-token-slider').getBoundingClientRect();return box.bottom <= innerHeight && box.top >= 0;}")
    capture(page,tmp_path/f'{theme}-10-studio-narrow-ticks-scrolled.png')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors
