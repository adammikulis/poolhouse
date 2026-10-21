"""Dataset journeys use the real local routes and isolated Hub metadata."""
import pytest
from browser_expect import expect
from test_dataset_catalogue import REPO, remote  # noqa: F401
from test_dataset_import_browser import configure
from test_dataset_imports import served  # noqa: F401
from test_studio_canvas_browser import add, studio_browser  # noqa: F401

pytestmark = pytest.mark.slow


@pytest.fixture
def dataset_audit_page(served, remote, studio_browser):  # noqa: F811
    app, _, _, _, session = served
    with studio_browser() as page:
        page.context.add_cookies([{'name': 'poolhouse_ui', 'value': session.sid,
                                  'url': f'http://127.0.0.1:{app.port}'}])
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        yield page, app, errors, remote
        assert not errors


def capture(page, tmp_path, name):
    page.screenshot(path=tmp_path / (name + '.png'))
    assert page.evaluate('document.documentElement.scrollHeight <= innerHeight + 1')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def assert_result_cards(results):
    assert results.locator('.dataset-result').evaluate_all("""cards => cards.every((card, index) => {
      const box=card.getBoundingClientRect(), next=cards[index+1]?.getBoundingClientRect();
      return [...card.children].every(child => {const rect=child.getBoundingClientRect();
        return rect.top >= box.top && rect.bottom <= box.bottom && rect.left >= box.left && rect.right <= box.right;})
        && (!next || next.top >= box.bottom);
    })""")


def inspect_result_endpoints(page, results, tmp_path):
    assert_result_cards(results)
    results.focus()
    page.keyboard.press('End')
    page.wait_for_function("() => {const e=document.querySelector('data-discover .dataset-results');return e.scrollTop+e.clientHeight>=e.scrollHeight-1;}")
    results.locator('.dataset-result').last.focus()
    expect(results.locator('.dataset-result').last).to_be_in_viewport()
    capture(page, tmp_path, 'data-discovery-last-result')
    results.focus()
    page.keyboard.press('Home')
    page.wait_for_function("() => document.querySelector('data-discover .dataset-results').scrollTop===0")
    results.locator('.dataset-result').first.focus()
    expect(results.locator('.dataset-result').first).to_be_in_viewport()


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize(('width', 'height'), [(1440, 900), (960, 1000), (390, 844), (960, 600)])
def test_discover_configure_import_preview_and_append_to_workflow(dataset_audit_page, tmp_path, theme, width, height):
    page, app, _, remote_state = dataset_audit_page
    app.ui.settings.appearance = {'active_theme': theme, 'saved_themes': []}
    page.goto(f'http://127.0.0.1:{app.port}/ui/#studio')
    add(page, 'Prompt template')
    page.locator('.studio-inspector').get_by_label('template', exact=True).fill('Retain this prompt')
    page.locator('.studio-inspector').get_by_label('template', exact=True).press('Tab')
    before = page.evaluate("structuredClone(document.querySelector('studio-view').graph)")
    page.set_viewport_size({'width': width, 'height': height})
    page.get_by_role('link', name='Datasets', exact=True).click()
    expect(page.get_by_role('tab', name='Local datasets', exact=True)).to_have_attribute('aria-selected', 'true')
    capture(page, tmp_path, 'data-local-library')
    values, _ = remote_state
    values['search'] = [values['main'], *[{**values['main'], 'id': f'sample-lab/records-{index}'} for index in range(23)]]
    values['search'][1]['id'] = 'sample-lab/a-long-dataset-name-with-wrapped-metadata-and-a-clear-selection'
    values['search'][1]['tags'] = ['license:example-long-readable-dataset-license']
    page.get_by_role('tab', name='Discover Hugging Face', exact=True).click()
    results = page.locator('data-discover .dataset-results')
    expect(results.locator('.dataset-result')).to_have_count(24)
    capture(page, tmp_path, 'data-discovery-results')
    inspect_result_endpoints(page, results, tmp_path)
    results.locator('.dataset-result').first.click()
    expect(page.get_by_role('heading', name=REPO, exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='Import selected preview rows', exact=True)).to_be_visible()
    capture(page, tmp_path, 'data-dataset-detail')
    configure(page)
    capture(page, tmp_path, 'data-import-mapping')
    assert page.get_by_label('Import field text', exact=True).evaluate('e => e.getBoundingClientRect().width >= 13 && e.getBoundingClientRect().height >= 13')
    page.get_by_role('button', name='Import selected preview rows', exact=True).click()
    expect(page.locator('data-import .dataset-import-progress')).to_contain_text('1 imported rows')
    capture(page, tmp_path, 'data-import-finished')
    page.get_by_role('button', name='Preview imported dataset', exact=True).click()
    expect(page.locator('data-view #preview-title')).to_have_text('Observed records')
    expect(page.locator('data-view #structured-preview')).to_contain_text('second')
    capture(page, tmp_path, 'data-managed-preview')
    page.get_by_role('button', name='Use in Studio', exact=True).click()
    page.wait_for_function("() => document.querySelector('studio-view').graph.nodes.some(n => n.type === 'dataset')")
    graph = page.evaluate("document.querySelector('studio-view').graph")
    assert graph['nodes'][:-1] == before['nodes'] and graph['edges'] == before['edges']
    assert graph['nodes'][-1]['bindings']['dataset']['kind'] == 'dataset'
    capture(page, tmp_path, 'data-studio-append')
    assert not app.runner.jobs


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_narrow_import_mapping_keeps_long_fields_and_checkbox_usable(dataset_audit_page, served, tmp_path, theme):  # noqa: F811
    page, app, _, _ = dataset_audit_page
    _, _, preview, _, _ = served
    field = 'source_field_with_a_long_descriptive_name'
    preview['columns'] = [{'name': field, 'type': 'string'}]
    preview['rows'] = [{field: 'first'}, {field: 'second'}]
    app.ui.settings.appearance = {'active_theme': theme, 'saved_themes': []}
    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(f'http://127.0.0.1:{app.port}/ui/#data')
    page.get_by_role('tab', name='Discover Hugging Face', exact=True).click()
    page.locator('.dataset-result').first.click()
    enabled = page.get_by_label('Import field ' + field, exact=True)
    output = page.get_by_label('Output field for ' + field, exact=True)
    output.fill('a_long_output_field_name_that_remains_editable')
    expect(enabled).to_be_checked()
    enabled.click()
    expect(enabled).not_to_be_checked()
    enabled.click()
    expect(enabled).to_be_checked()
    output.focus()
    expect(output).to_be_focused()
    assert enabled.evaluate('e => e.getBoundingClientRect().width === 16 && e.getBoundingClientRect().height === 16')
    assert output.locator('..').evaluate("""row => {const box=row.getBoundingClientRect(), owner=row.closest('.dataset-import').getBoundingClientRect();
      return box.left>=owner.left && box.right<=owner.right && [...row.children].every(child=>child.getBoundingClientRect().right<=box.right);}""")
    capture(page, tmp_path, 'data-long-field-mapping')
