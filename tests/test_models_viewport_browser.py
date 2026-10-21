"""A large installed library and discovery stay inside the Models workspace."""
import pytest
from browser_expect import expect
from test_fleet_ui import Serving
from test_studio_canvas_browser import studio_browser  # noqa: F401

pytestmark = pytest.mark.slow


@pytest.fixture
def inventory_page(tmp_path, studio_browser):  # noqa: F811
    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    rows = [{'id': f'fixture-{index}', 'name': f'Orchard-{index:03d}',
             'path': f'/fixture/models/orchard-{index:03d}.gguf', 'family': 'Orchard',
             'format': 'gguf', 'kind': 'text', 'status': 'ready', 'servable': True,
             'context_length': 32768, 'shards': 1, 'is_complete': True, 'files': [],
             'size_bytes': 1_000_000_000, 'mtime': index} for index in range(106)]
    state = {'error': False, 'free_gb': 500, 'rows': rows, 'getting': [], 'unfinished': []}
    with studio_browser() as page:
        page.route('**/ui/models', lambda route: route.fulfill(status=503 if state['error'] else 200,
            json={'error': 'Fixture inventory unavailable'} if state['error'] else
                 {'library': rows, 'elsewhere': [], 'free_gb': state['free_gb'], 'getting': state['getting'], 'unfinished': state['unfinished']}))
        page.route('**/ui/serving', lambda route: route.fulfill(json={'running': [], 'can_serve': True}))
        page.route('**/ui/models/startup', lambda route: route.fulfill(json={'models': []}))
        page.route('**/ui/models/popular?*', lambda route: route.fulfill(json={
            'models': [{'name': f'Fixture-Hub-{index}', 'file': f'fixture-{index}.gguf',
                        'ref': f'hf:sample/repo-{index}/fixture-{index}.gguf', 'family': 'Fixture',
                        'gb': 1, 'what': 'Isolated discovery fixture'} for index in range(24)],
            'families': ['Fixture'], 'page': 0, 'pages': 1}))
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            yield served, page, state, errors
        finally:
            served.close()


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize(('width', 'height'), [(1440, 1000), (960, 1000), (390, 844), (960, 600)])
def test_large_library_keeps_search_and_actions_visible_across_discovery(inventory_page, tmp_path, theme, width, height):
    served, page, state, errors = inventory_page
    served.ui.settings.appearance = {'active_theme': theme, 'saved_themes': []}
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(f'http://127.0.0.1:{served.port}/ui/#models')
    results = page.get_by_role('region', name='Installed model results', exact=True)
    expect(results.locator('article')).to_have_count(106)
    page.screenshot(path=tmp_path / 'models-installed-initial.png')
    assert page.evaluate('document.documentElement.scrollHeight <= innerHeight')
    assert results.evaluate('e => e.scrollHeight > e.clientHeight && e.clientHeight > 120')
    results.focus()
    page.keyboard.press('End')
    page.wait_for_function("() => document.querySelector('.library-results').scrollTop > 0")
    search = page.get_by_role('searchbox', name='Search installed models', exact=True)
    expect(search).to_be_in_viewport()
    expect(page.locator('#add-model')).to_be_in_viewport()
    last = results.locator('article').last
    last.locator('summary').filter(has_text='Context length').click()
    context = last.get_by_role('slider', name='Context length', exact=True)
    files = last.locator('summary').filter(has_text='Files and model details')
    files.click()
    context.focus()
    before = results.evaluate('e => e.scrollTop')
    page.screenshot(path=tmp_path / 'models-last-options.png')
    state['free_gb'] = 499
    state['rows'][0]['mtime'] += 1
    page.locator('models-view').evaluate('async e => await e.draw()')
    expect(context).to_be_focused()
    expect(files.locator('..')).to_have_attribute('open', '')
    page.get_by_role('button', name='+ Add a model', exact=True).click()
    expect(page.get_by_role('searchbox', name='Search models', exact=True)).to_be_focused()
    page.get_by_role('button', name='Hugging Face', exact=True).click()
    discovered = page.get_by_role('region', name='Model discovery results', exact=True)
    expect(discovered.locator('.model-row')).to_have_count(24)
    page.screenshot(path=tmp_path / 'models-discovery.png')
    assert page.evaluate('document.documentElement.scrollHeight <= innerHeight')
    discovered.focus()
    page.keyboard.press('End')
    expect(page.get_by_role('searchbox', name='Search models', exact=True)).to_be_in_viewport()
    page.get_by_role('button', name='Back to installed models', exact=True).click()
    expect(context).to_be_focused()
    assert results.evaluate('e => e.scrollTop') == pytest.approx(before, abs=1)
    page.screenshot(path=tmp_path / 'models-back.png')
    search.fill('not present')
    expect(results.get_by_role('heading', name='No matches', exact=True)).to_be_visible()
    page.screenshot(path=tmp_path / 'models-no-results.png')
    assert not errors


def test_inventory_failure_is_not_an_empty_library_and_retry_retains_search(inventory_page, tmp_path):
    served, page, state, errors = inventory_page
    page.goto(f'http://127.0.0.1:{served.port}/ui/#models')
    search = page.get_by_role('searchbox', name='Search installed models', exact=True)
    search.fill('Orchard-105')
    state['error'] = True
    page.locator('models-view').evaluate('async e => await e.draw()')
    expect(page.get_by_role('heading', name='Installed models unavailable', exact=True)).to_be_visible()
    page.screenshot(path=tmp_path / 'models-inventory-error.png')
    expect(search).to_have_value('Orchard-105')
    state['error'] = False
    page.get_by_role('button', name='Retry installed models', exact=True).click()
    expect(page.locator('.library-results article')).to_have_count(1)
    expect(search).to_have_value('Orchard-105')
    page.screenshot(path=tmp_path / 'models-inventory-recovered.png')
    assert not errors


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize(('width', 'height'), [(960, 600), (390, 844)])
def test_auxiliary_controls_share_a_bounded_region(inventory_page, tmp_path, theme, width, height):
    served, page, state, errors = inventory_page
    served.ui.settings.appearance = {'active_theme': theme, 'saved_themes': []}
    state['getting'] = [{'id': 'fixture-download', 'name': 'Fixture active download',
                         'state': 'running', 'done': 12, 'total': 100}]
    state['unfinished'] = [{'name': f'Fixture-paused-{index}.part', 'size': 20} for index in range(8)]
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(f'http://127.0.0.1:{served.port}/ui/#models')
    results = page.get_by_role('region', name='Installed model results', exact=True)
    expect(results.locator('article')).to_have_count(106)
    page.locator('#models-note').evaluate("e => e.textContent='Fixture action needs attention.'")
    page.locator('#models-room').evaluate('e => e.open=true')
    auxiliary = page.get_by_role('region', name='Model downloads and device controls', exact=True)
    discard = auxiliary.get_by_role('button', name='Discard', exact=True).last
    discard.focus()
    expect(discard).to_be_in_viewport()
    assert results.evaluate('e => e.clientHeight > 120')
    assert page.evaluate('document.documentElement.scrollHeight <= innerHeight')
    page.screenshot(path=tmp_path / 'models-auxiliary-paused.png')
    page.locator('#models-room summary').focus()
    expect(page.locator('#models-room summary')).to_be_in_viewport()
    page.screenshot(path=tmp_path / 'models-auxiliary-memory.png')
    assert not errors


@pytest.mark.parametrize(('width', 'theme'), [(960, 'light'), (390, 'dark')])
def test_discovery_filter_summary_survives_source_and_pane_changes(inventory_page, tmp_path, width, theme):
    served, page, _, errors = inventory_page
    served.ui.settings.appearance = {'active_theme': theme, 'saved_themes': []}
    page.set_viewport_size({'width': width, 'height': 844})
    page.goto(f'http://127.0.0.1:{served.port}/ui/#models')
    page.get_by_role('button', name='+ Add a model', exact=True).click()
    page.get_by_role('button', name='Hugging Face', exact=True).click()
    summary = page.locator('model-browser details.aside > summary')
    summary.click()
    page.get_by_role('checkbox', name='Fixture', exact=True).uncheck()
    expect(summary).to_have_text('Advanced model filters · 0 families')
    summary.click()
    expect(summary).to_be_focused()
    page.screenshot(path=tmp_path / 'models-filter-summary.png')
    page.get_by_role('button', name='In your Pool', exact=True).click()
    expect(summary).to_have_text('Advanced model filters')
    page.get_by_role('button', name='Hugging Face', exact=True).click()
    expect(summary).to_have_text('Advanced model filters · 0 families')
    summary.click()
    page.get_by_role('checkbox', name='Include uncensored builds', exact=True).check()
    expect(summary).to_have_text('Advanced model filters · uncensored included')
    page.screenshot(path=tmp_path / 'models-filters-open.png')
    page.get_by_role('button', name='Back to installed models', exact=True).click()
    page.get_by_role('button', name='+ Add a model', exact=True).click()
    expect(page.get_by_role('checkbox', name='Include uncensored builds', exact=True)).to_be_checked()
    expect(summary).to_have_text('Advanced model filters · uncensored included')
    assert not errors
