"""Bounded token selection preserves stored values until explicit interaction."""
from itertools import pairwise

import pytest
from browser_expect import expect
from test_models_settings_feedback_browser import feedback_page  # noqa: F401
from test_studio_canvas_browser import studio_browser, studio_page  # noqa: F401

pytestmark = pytest.mark.slow


def test_context_reduction_preserves_saved_output_until_slider_changes(feedback_page):  # noqa: F811
    served, page, errors = feedback_page
    served.call('/ui/settings', method='POST',
                body={'context': 16384, 'chat_max_output_tokens': 12000})
    page.goto(f'http://127.0.0.1:{served.port}/ui/#settings')
    cap = page.get_by_role('slider', name='Default maximum output tokens', exact=True)
    expect(cap).to_have_attribute('aria-valuetext', 'Custom · 12,000 tokens')
    context = page.get_by_role('slider', name='Context length', exact=True)
    context.press('ArrowLeft')
    assert cap.evaluate('node => node.getRootNode().host.stops.at(-1)') == 8192
    expect(cap).to_have_attribute('aria-valuetext', 'Custom · 12,000 tokens')
    expect(page.locator('settings-view output-token-slider output')).to_have_text('Custom · 12,000 tokens')
    assert page.locator('settings-view').evaluate('node => node.pick.chat_max_output_tokens') == 12000
    context.press('ArrowRight')
    expect(cap).to_have_attribute('aria-valuetext', 'Custom · 12,000 tokens')
    context.press('ArrowLeft')
    page.locator('#settings-save').click()
    expect(page.locator('#settings-note .ok')).to_be_visible()
    _, saved, _ = served.call('/ui/settings')
    assert saved['settings']['chat_max_output_tokens'] == 12000
    assert saved['settings']['context'] == 8192
    cap.press('End')
    cap.press('ArrowLeft')
    expect(cap).to_have_attribute('aria-valuetext', '4,096 tokens')
    page.locator('#settings-save').click()
    expect(page.locator('#settings-note .ok')).to_be_visible()
    _, saved, _ = served.call('/ui/settings')
    assert saved['settings']['chat_max_output_tokens'] == 4096
    assert not errors


def test_studio_generation_control_intersects_schema_and_context_without_writes(feedback_page):  # noqa: F811
    served, page, errors = feedback_page
    page.goto(f'http://127.0.0.1:{served.port}/ui/#studio')
    page.wait_for_function('() => !!window.studioModel')
    page.evaluate('''() => {
      fleetModel.setPreferences({context:4096});
      const host = document.createElement('div'); host.id = 'token-fixture';
      document.body.append(host); window.tokenChanges = [];
      studioModel.field(host, 'max_tokens', {type:'integer',minimum:1,maximum:2048,default:512},
        3000, value => window.tokenChanges.push(value));
    }''')
    cap = page.locator('#token-fixture').get_by_role('slider', name='max_tokens', exact=True)
    assert cap.evaluate('node => node.getRootNode().host.stops.at(0)') == 1
    assert cap.evaluate('node => node.getRootNode().host.stops.at(-1)') == 2048
    expect(page.locator('#token-fixture output')).to_have_text('Custom · 3,000 tokens')
    assert page.evaluate('window.tokenChanges') == []
    page.evaluate('fleetModel.setPreferences({context:1024})')
    assert cap.evaluate('node => node.getRootNode().host.stops.at(-1)') == 1024
    expect(page.locator('#token-fixture output')).to_have_text('Custom · 3,000 tokens')
    assert page.evaluate('window.tokenChanges') == []
    cap.press('Home')
    assert page.evaluate('window.tokenChanges') == [1]
    cap.press('End')
    assert page.evaluate('window.tokenChanges') == [1, 1024]
    page.evaluate('fleetModel.setPreferences({context:"invalid"})')
    assert cap.evaluate('node => node.getRootNode().host.stops.at(-1)') == 2048
    page.locator('#token-fixture').evaluate('node => node.remove()')
    page.evaluate('fleetModel.setPreferences({context:8192})')
    assert page.evaluate('window.tokenChanges') == [1, 1024]
    assert not errors


@pytest.fixture
def generation_page(request, monkeypatch):
    import test_studio_canvas_browser

    from poolhouse.studio.catalog import node_catalog

    monkeypatch.setattr(test_studio_canvas_browser, 'SPECS',
                        [spec for spec in node_catalog() if spec['type'] == 'generate'])
    return request.getfixturevalue('studio_page')


def test_generation_node_uses_slider_in_inspector_and_canvas(generation_page):
    from test_studio_canvas_browser import add

    page, _state, errors = generation_page
    add(page, 'Generate')
    cap = page.locator('.studio-inspector').get_by_role('slider', name='max_tokens', exact=True)
    assert cap.evaluate('node => node.getRootNode().host.stops.at(-1)') == 8192
    expect(page.locator('.studio-node').get_by_role('slider', name='max_tokens', exact=True)).to_be_visible()
    cap.press('Home')
    assert page.locator('studio-view').evaluate('editor => editor.graph.nodes[0].parameters.max_tokens') == 1
    expect(page.locator('.studio-inspector output')).to_have_text('1 token')
    assert not errors


def test_chat_output_drag_saves_once_on_release(feedback_page, tmp_path):  # noqa: F811
    from poolhouse.fleet.conversations import Conversations

    served, page, errors = feedback_page
    served.ui.conversations = Conversations(tmp_path / "chats")
    status, made, _ = served.call('/ui/conversations', method='POST',
                                 body={'title': 'Token drag check', 'model': 'offline-model'})
    assert status == 201
    page.goto(f'http://127.0.0.1:{served.port}/ui/#chat')
    page.get_by_role('link', name='Token drag check', exact=True).click()
    page.locator('#conversation-details').click()
    cap = page.locator('#chat-output-tokens').get_by_role('slider')
    expect(cap).to_be_visible()
    saved = []
    page.on('request', lambda request: saved.append(request.post_data_json)
            if request.method == 'POST' and request.url.endswith(f"/ui/conversations/{made['id']}") else None)
    page.locator('#chat-output-tokens').evaluate('''node => {
      window.tokenEvents = {input:0, change:0};
      node.addEventListener('input', () => tokenEvents.input++);
      node.addEventListener('change', () => tokenEvents.change++);
    }''')
    box = cap.bounding_box()
    page.mouse.move(box['x'] + box['width'] * .7, box['y'] + box['height'] / 2)
    page.mouse.down()
    for fraction in [.6, .5, .4]:
        page.mouse.move(box['x'] + box['width'] * fraction, box['y'] + box['height'] / 2)
    assert page.evaluate('tokenEvents.input') >= 3
    assert page.evaluate('tokenEvents.change') == 0
    assert not saved
    page.mouse.up()
    page.wait_for_function('tokenEvents.change === 1')
    page.locator('chat-view').evaluate('async node => await node.savePending')
    assert len(saved) == 1
    assert saved[0]['settings']['max_output_tokens'] == int(page.locator('#chat-output-tokens').evaluate('node => node.value'))
    assert not errors


@pytest.mark.parametrize('width', [1440, 390])
def test_output_ticks_snap_pointer_and_keyboard_without_rounding_saved_custom(feedback_page, width):  # noqa: F811
    served, page, errors = feedback_page
    served.ui.settings.context = 131072
    served.ui.settings.chat_max_output_tokens = 42013
    page.set_viewport_size({'width':width,'height':1000})
    page.goto(f'http://127.0.0.1:{served.port}/ui/#settings')
    wrapper = page.locator('#settings-tokens output-token-slider')
    cap = wrapper.get_by_role('slider')
    expect(cap).to_have_attribute('aria-valuetext', 'Custom · 42,013 tokens')
    assert wrapper.evaluate('node => node.value') == '42013'
    page.locator('#settings-tokens #ctx').get_by_role('slider').press('ArrowLeft')
    expect(cap).to_have_attribute('aria-valuetext', 'Custom · 42,013 tokens')
    page.locator('#settings-tokens #ctx').get_by_role('slider').press('ArrowRight')
    assert wrapper.evaluate('node => node.value') == '42013'
    wrapper.evaluate('''node => {
      window.snappedValues=[];
      node.addEventListener('input', () => snappedValues.push(node.value));
    }''')
    ticks = wrapper.evaluate('node => node.slider.stops')
    assert ticks == [0,512,1024,2048,4096,8192,16384,32768,65536,131072]
    box = cap.bounding_box()
    page.mouse.move(box['x']+box['width']*.2,box['y']+box['height']/2)
    page.mouse.down()
    for fraction in [.31,.44,.57,.69,.82]:
        page.mouse.move(box['x']+box['width']*fraction,box['y']+box['height']/2)
    page.mouse.up()
    selected = page.evaluate('snappedValues')
    assert len(set(selected)) >= 4
    assert all(value == '' or int(value) in ticks for value in selected)
    cap.press('Home')
    assert wrapper.evaluate('node => node.value') == ''
    cap.press('ArrowRight')
    expect(cap).to_have_attribute('aria-valuetext','512 tokens')
    cap.press('End')
    expect(cap).to_have_attribute('aria-valuetext','131,072 tokens')
    cap.press('ArrowLeft')
    expect(cap).to_have_attribute('aria-valuetext','65,536 tokens')
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    boxes = wrapper.evaluate('''node => [...node.slider.shadowRoot.querySelectorAll('.tick')]
      .filter(tick => tick.textContent).map(tick => {const rect=tick.getBoundingClientRect();return {left:rect.left,right:rect.right};})''')
    assert all(left['right'] <= right['left'] for left,right in pairwise(boxes))
    expect(wrapper.locator('.tick.on')).to_have_text('64k')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors


def test_output_tick_schema_boundaries_and_disabled_range_preserve_raw(generation_page):
    page, _state, errors = generation_page
    page.evaluate('''() => {
      const node=document.createElement('output-token-slider');node.id='schema-ticks';
      node.configure({nullable:false,minimum:128,maximum:3000,context:5000});node.value=1700;
      document.body.append(node);
    }''')
    wrapper = page.locator('#schema-ticks')
    cap = wrapper.get_by_role('slider')
    expect(cap).to_have_attribute('aria-valuetext','Custom · 1,700 tokens')
    assert wrapper.evaluate('node => node.slider.stops') == [128,512,1024,2048,3000]
    cap.press('End')
    assert wrapper.evaluate('node => node.value') == '3000'
    cap.press('ArrowLeft')
    assert wrapper.evaluate('node => node.value') == '2048'
    cap.press('Home')
    assert wrapper.evaluate('node => node.value') == '128'
    wrapper.evaluate('node => node.configure({context:64})')
    expect(cap).to_be_disabled()
    assert wrapper.evaluate('node => node.value') == '128'
    assert not errors
