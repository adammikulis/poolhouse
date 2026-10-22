"""Optional model start controls preserve drafts and use the actual serving route."""
from pathlib import Path
from tempfile import mkdtemp

import pytest
from browser_expect import expect
from test_chat_inline_model_browser import inline_models, open_picker  # noqa: F401
from test_studio_canvas_browser import studio_browser  # noqa: F401

from poolhouse.fleet.serving import Hosting, Served

pytestmark = pytest.mark.slow


def options(page):
    details = page.locator('#chat-model-list .model-start-options')
    details.locator('summary').click()
    expect(details.locator('#ctx')).to_have_count(0)
    return details


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('width', [1440, 390])
def test_picker_context_and_output_use_actual_start_and_observed_context(inline_models, monkeypatch, width, theme):  # noqa: F811
    app, page, model, starts, current, _inventory = inline_models
    contexts = []
    app.ui.settings.appearance = {"active_theme": theme, "saved_themes": []}
    pictures = Path(mkdtemp(prefix="poolhouse-start-options-png-")).resolve()

    def snapshot(name):
        path = pictures / f"{theme}-{width}-{name}.png"
        page.screenshot(path=str(path))
        print("VISUAL_PNG", path, flush=True)
    page.set_viewport_size({'width': width, 'height': 1000})

    def start(_host, path, settings):
        starts.append(str(path))
        contexts.append(settings.context)
        served = Served(port=12345, models=[model.name], aliases=[str(model)],
                        context=16384, slot_context=16384, capabilities={'chat': True})
        current[:] = [served]
        return served

    monkeypatch.setattr(Hosting, 'start', start)
    open_picker(app, page)
    controls = options(page)
    context = controls.get_by_role('slider', name='Context length', exact=True)
    expect(page.locator('html')).to_have_attribute('data-theme', theme)
    snapshot('initial')
    context.focus()
    context.press('Home')
    context.press('ArrowRight')
    expect(context).to_have_value('1')
    expect(controls).to_contain_text('4,096 tokens requested')
    expect(controls.locator('output-token-slider')).to_have_js_property('value', '8192')
    snapshot('context-4096-raw-output')
    output = controls.get_by_role('slider', name='Maximum output tokens', exact=True)
    output.focus()
    output.press('End')
    expect(controls.locator('output-token-slider')).to_have_js_property('value', '4096')
    snapshot('explicit-output-4096')
    page.get_by_role('button', name=f'{model.name} Available Start in Chat', exact=True).click()
    expect(page.locator('#chat-model-dialog')).not_to_be_visible()
    snapshot('started')
    assert contexts == [4096]
    assert starts == [str(model)]
    expect(page.locator('#chat-output-tokens')).to_have_js_property('value', '4096')
    expect(page.locator('#chat-note')).to_contain_text('Requested context: 4,096 tokens. Running context: 16,384 tokens.')


def test_options_survive_search_and_cancel_does_not_start(inline_models):  # noqa: F811
    app, page, _model, starts, _current, _inventory = inline_models
    open_picker(app, page)
    controls = options(page)
    controls.get_by_role('slider', name='Context length', exact=True).press('End')
    expect(controls).to_contain_text('16,384 tokens requested')
    search = page.get_by_role('searchbox', name='Search models and workspace agents', exact=True)
    search.fill('no match')
    search.fill('')
    expect(page.locator('.model-start-options')).to_contain_text('16,384 tokens requested')
    page.get_by_role('button', name='Close', exact=True).click()
    assert not starts
    expect(page.locator('#model')).to_have_value('')


def test_unknown_maximum_and_saved_output_are_not_rewritten(inline_models):  # noqa: F811
    app, page, _model, starts, _current, inventory = inline_models
    inventory[0].pop('context_length')
    open_picker(app, page)
    page.locator('chat-view').evaluate("chat => chat.outputTokens.value = '32768'")
    # Reopen initializes no new model options and never applies a draft to the conversation.
    controls = options(page)
    expect(controls).to_contain_text('maximum is not reported')
    controls.locator('output-token-slider').evaluate("node => node.value = '32768'")
    controls.get_by_role('slider', name='Context length', exact=True).press('Home')
    expect(controls.locator('output-token-slider')).to_have_js_property('value', '32768')
    expect(controls).to_contain_text('Saved limit exceeds')
    page.get_by_role('button', name='Close', exact=True).click()
    expect(page.locator('#chat-output-tokens')).to_have_js_property('value', '32768')
    assert not starts


def test_models_primary_start_uses_visible_optional_context(inline_models, monkeypatch):  # noqa: F811
    app, page, model, starts, current, inventory = inline_models
    inventory[:] = [inventory[0]]
    contexts, errors = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def start(_host, path, settings):
        starts.append(str(path))
        contexts.append(settings.context)
        served = Served(port=12345, models=[model.name], aliases=[str(model)],
                        context=16384, slot_context=16384, capabilities={'chat': True})
        current[:] = [served]
        return served

    monkeypatch.setattr(Hosting, 'start', start)
    page.goto(f'http://127.0.0.1:{app.port}/ui#models')
    library = page.locator('models-library')
    button = library.get_by_role('button', name='Start model', exact=True)
    expect(button).to_be_visible()
    library.locator('summary').filter(has_text='Context length').click()
    slider = library.get_by_role('slider', name='Context length', exact=True)
    slider.press('Home')
    slider.press('ArrowRight')
    button.click()
    expect(page.locator('#models-note')).to_contain_text('is ready')
    page.locator('models-view').evaluate('async node => await node.draw()')
    expect(library.locator('.model-start-options')).not_to_be_visible()
    assert contexts == [4096]
    assert starts == [str(model)]
    assert not errors


def test_changed_model_bound_preserves_raw_draft_and_blocks_until_explicit_edit(inline_models):  # noqa: F811
    app, page, model, starts, _current, inventory = inline_models
    open_picker(app, page)
    controls = options(page)
    context = controls.get_by_role('slider', name='Context length', exact=True)
    context.press('End')
    inventory[0]['context_length'] = 4096
    page.locator('chat-view').evaluate('async chat => {await chat.modelPicker.catalogue(); chat.modelPicker.render();}')
    expect(controls).to_contain_text('16,384 tokens requested')
    expect(context).to_have_value('1')
    page.get_by_role('button', name=f'{model.name} Available Start in Chat', exact=True).click()
    expect(page.locator('#chat-note')).to_contain_text('supported limit')
    assert not starts
    expect(page.locator('#model')).to_have_value('')
    context.press('Home')
    context.press('End')
    page.get_by_role('button', name=f'{model.name} Available Start in Chat', exact=True).click()
    expect(page.locator('#chat-model-dialog')).not_to_be_visible()
    assert starts == [str(model)]


@pytest.mark.parametrize('edit', ['sidebar', 'output'])
def test_newer_main_selection_or_output_survives_a_late_model_start(inline_models, monkeypatch, edit):  # noqa: F811
    app, page, model, starts, current, _inventory = inline_models
    current[:] = [Served(port=12346, models=['resident-a'], aliases=[str(model.parent / 'a.gguf')], capabilities={'chat': True}),
                  Served(port=12347, models=['resident-b'], aliases=[str(model.parent / 'b.gguf')], capabilities={'chat': True})]

    def start(_host, path, _settings):
        starts.append(str(path))
        served = Served(port=12345, models=[model.name], aliases=[str(model)], capabilities={'chat': True})
        current.append(served)
        return served

    monkeypatch.setattr(Hosting, 'start', start)
    held = []

    def hold(route):
        held.append((route, route.fetch()))
        page.locator('body').evaluate("node => node.dataset.startHeld = 'true'")

    page.route('**/ui/serving', hold)
    open_picker(app, page)
    page.get_by_role('button', name=f'{model.name} Available Start in Chat', exact=True).click()
    expect(page.locator('body')).to_have_attribute('data-start-held', 'true')
    page.get_by_role('button', name='Close', exact=True).click()
    if edit == 'sidebar':
        page.get_by_role('button', name='● resident-b', exact=True).click()
    else:
        page.locator('#conversation-details').click()
        page.locator('#chat-output-tokens').get_by_role('slider').press('End')
    chosen = page.locator('#model').input_value()
    output = page.locator('#chat-output-tokens').evaluate('node => node.value')
    held[0][0].fulfill(response=held[0][1])
    page.wait_for_function('() => !document.querySelector("chat-view").modelPicker.startPending')
    expect(page.locator('#model')).to_have_value(chosen)
    expect(page.locator('#chat-output-tokens')).to_have_js_property('value', output)
    assert chosen == ('resident-b' if edit == 'sidebar' else 'resident-a')
    assert starts == [str(model)]


@pytest.mark.parametrize('maximum,expected', [(1024,[1024]), (6000,[2048,4096,6000])])
def test_context_uses_shared_stops_and_exact_model_boundary(inline_models,maximum,expected):  # noqa: F811
    app,page,_model,starts,_current,inventory=inline_models
    inventory[0]['context_length']=maximum
    open_picker(app,page)
    controls=options(page)
    context=controls.locator('poolhouse-slider').first
    assert context.evaluate('node => node.stops') == expected
    context.get_by_role('slider').press('Home')
    expect(controls).to_contain_text(f'{expected[0]:,} tokens requested')
    context.get_by_role('slider').press('End')
    expect(controls).to_contain_text(f'{maximum:,} tokens requested')
    assert not starts


def test_explicit_output_endpoint_commits_once_without_rewriting_untouched_draft(inline_models):  # noqa: F811
    app,page,_model,_starts,_current,_inventory=inline_models
    open_picker(app,page)
    controls=options(page)
    context=controls.get_by_role('slider',name='Context length',exact=True)
    context.press('Home')
    context.press('ArrowRight')
    host=controls.locator('output-token-slider')
    expect(host).to_have_js_property('value','8192')
    host.evaluate("node => {node.events={input:0,change:0}; for(const type of ['input','change']) node.addEventListener(type,()=>node.events[type]++);}")
    cap=host.get_by_role('slider')
    cap.press('End')
    expect(host).to_have_js_property('value','4096')
    assert host.evaluate('node => node.events') == {'input':1,'change':1}
    host.evaluate("node => {node.value='42013'; node.events={input:0,change:0};}")
    box=cap.bounding_box()
    cap.click(position={'x':box['width']-8,'y':box['height']/2})
    expect(host).to_have_js_property('value','4096')
    assert host.evaluate('node => node.events') == {'input':1,'change':1}
    host.evaluate("node => {node.value='42013'; node.events={input:0,change:0};}")
    page.mouse.move(box['x']+box['width']-8,box['y']+box['height']/2)
    page.mouse.down()
    cap.dispatch_event('pointercancel',{'pointerId':1})
    page.mouse.up()
    assert host.evaluate('node => node.events') == {'input':0,'change':0}
    expect(host).to_have_js_property('value','42013')
