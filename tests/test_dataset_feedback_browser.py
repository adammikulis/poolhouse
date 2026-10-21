"""Import feedback stays visible without covering edits or moving focus."""
import pytest
from browser_expect import expect
from test_dataset_catalogue import remote  # noqa: F401
from test_dataset_imports import served  # noqa: F401
from test_studio_canvas_browser import studio_browser  # noqa: F401

pytestmark = [pytest.mark.slow, pytest.mark.parametrize('theme', ['light', 'dark'])]


@pytest.fixture
def feedback_page(served, remote, studio_browser, theme):  # noqa: F811
    app, _, _, _, session = served
    app.ui.settings.appearance = {'active_theme': theme, 'saved_themes': []}
    with studio_browser() as page:
        page.context.add_cookies([{'name': 'poolhouse_ui', 'value': session.sid,
                                  'url': f'http://127.0.0.1:{app.port}'}])
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'http://127.0.0.1:{app.port}/ui/#data')
        page.get_by_role('tab', name='Discover Hugging Face', exact=True).click()
        page.locator('.dataset-result').first.click()
        expect(page.get_by_role('button', name='Import selected preview rows', exact=True)).to_be_visible()
        yield page, app
        assert not errors


@pytest.mark.parametrize(('width', 'height'), [(960, 600), (390, 844)])
def test_completed_import_reveals_result_and_preview_without_an_extra_scroll(feedback_page, tmp_path, width, height):
    page, _ = feedback_page
    page.set_viewport_size({'width': width, 'height': height})
    page.get_by_role('button', name='Import selected preview rows', exact=True).click()
    status = page.locator('data-import .dataset-import-progress')
    expect(status).to_contain_text('2 imported rows')
    page.screenshot(path=tmp_path / 'data-import-completed.png')
    assert_control_contained(page.get_by_role('button', name='Preview imported dataset', exact=True), page.locator('.dataset-import-feedback'))
    expect(status).to_be_in_viewport()
    preview = page.get_by_role('button', name='Preview imported dataset', exact=True)
    preview.focus()
    page.evaluate("() => document.querySelector('data-import').refresh()")
    expect(preview).to_be_focused()
    assert page.evaluate('document.documentElement.scrollHeight <= innerHeight + 1')


def assert_control_contained(control, container):
    control.focus()
    expect(control).to_be_focused()
    expect(control).to_be_in_viewport(ratio=1)
    bounds = control.bounding_box()
    outer = container.bounding_box()
    assert bounds and outer
    assert bounds['y'] >= outer['y'] - 1
    assert bounds['y'] + bounds['height'] <= outer['y'] + outer['height'] + 1


@pytest.mark.parametrize(('width', 'height'), [(960, 600), (390, 844)])
def test_pending_and_failed_feedback_keeps_edits_uncovered(feedback_page, tmp_path, width, height):
    page, _ = feedback_page
    page.set_viewport_size({'width': width, 'height': height})
    run = {'id': 'fixture-import', 'state': 'running', 'nodes': {'import': {'state': 'running'}}}
    page.route('**/ui/workspace/datasets/preview-imports',
               lambda route: route.fulfill(status=202, json={'run': run}))
    page.route('**/ui/workspace/datasets/imports/fixture-import',
               lambda route: route.fulfill(json={'run': run}))
    page.get_by_role('button', name='Import selected preview rows', exact=True).click()
    status = page.locator('data-import .dataset-import-progress')
    expect(status).to_contain_text('running')
    field = page.get_by_label('Output field for text', exact=True)
    field.click()
    field.fill('next_prompt')
    field.focus()
    content = page.locator('.dataset-import-content')
    feedback = page.locator('.dataset-import-feedback')
    page.evaluate("() => document.querySelector('data-import').refresh()")
    expect(field).to_be_focused()
    page.screenshot(path=tmp_path / 'data-import-pending-edit.png')
    bounds, body, row = field.bounding_box(), content.bounding_box(), feedback.bounding_box()
    assert bounds['y'] >= body['y'] - 1
    assert bounds['y'] + bounds['height'] <= body['y'] + body['height'] + 1 <= row['y'] + 1
    assert page.evaluate("document.querySelector('data-import').operation.body.columns.text") == 'text'
    page.screenshot(path=tmp_path / 'data-import-pending-edit.png')
    for name in ['Cancel import', 'Refresh import status']:
        assert_control_contained(page.get_by_role('button', name=name, exact=True), feedback)
    cancel = page.get_by_role('button', name='Cancel import', exact=True)
    cancel.focus()
    page.evaluate("() => document.querySelector('data-import').refresh()")
    expect(cancel).to_be_focused()
    page.screenshot(path=tmp_path / 'data-import-pending-action-focus.png')
    run.update(state='failed', error='Isolated preview processing failed. ' * 12)
    page.evaluate("() => document.querySelector('data-import').refresh()")
    expect(status).to_contain_text('failed')
    expect(page.get_by_role('button', name='Cancel import', exact=True)).to_have_count(0)
    assert_control_contained(page.get_by_role('button', name='Refresh import status', exact=True), feedback)
    expect(page.get_by_role('button', name='Preview imported dataset', exact=True)).to_have_count(0)
    page.screenshot(path=tmp_path / 'data-import-failed.png')
    assert page.evaluate('document.documentElement.scrollHeight <= innerHeight + 1')


@pytest.mark.parametrize(('width', 'height'), [(960, 600), (390, 844)])
def test_unknown_feedback_survives_background_return_and_clears_null_selection(feedback_page, tmp_path, width, height):
    page, _ = feedback_page
    page.set_viewport_size({'width': width, 'height': height})
    held = []
    page.route('**/ui/workspace/datasets/preview-imports', lambda route: held.append(route))
    page.route('**/ui/workspace/datasets/imports?*',
               lambda route: route.fulfill(status=404, json={'error': 'No submitted import'}))
    page.get_by_role('button', name='Import selected preview rows', exact=True).click()
    expect(page.get_by_role('button', name='Import selected preview rows', exact=True)).to_be_disabled()
    local = page.get_by_role('tab', name='Local datasets', exact=True)
    local.click()
    local.focus()
    held[0].fulfill(status=503, json={'error': 'Isolated acknowledgement failure'})
    page.wait_for_function("() => document.querySelector('data-import').operation?.state === 'unknown'")
    expect(local).to_be_focused()
    expect(page.locator('.dataset-import-feedback')).to_be_hidden()
    page.get_by_role('tab', name='Discover Hugging Face', exact=True).click()
    feedback = page.locator('.dataset-import-feedback')
    check = page.get_by_role('button', name='Check this import', exact=True)
    assert_control_contained(check, feedback)
    page.evaluate("() => document.querySelector('data-import').draw()")
    expect(check).to_be_focused()
    page.screenshot(path=tmp_path / 'data-import-unknown-return.png')
    check.click()
    retry = page.get_by_role('button', name='Submit this same selection', exact=True)
    assert_control_contained(retry, feedback)
    page.screenshot(path=tmp_path / 'data-import-not-found.png')
    page.evaluate("() => document.querySelector('data-import').select(null, null, () => false)")
    expect(page.get_by_label('Imported dataset name', exact=True)).to_have_count(0)
    expect(page.locator('.dataset-import-selection')).to_be_hidden()
    # A missing preview must not hide the public source metadata or the owned outcome.
    expect(page.locator('.dataset-detail')).to_contain_text('sample-lab/records')
    expect(retry).to_be_visible()
    page.evaluate("() => document.querySelector('data-import').invalidate()")
    expect(feedback).to_be_hidden()
    expect(page.locator('.dataset-detail')).to_contain_text('sample-lab/records')
    assert page.evaluate('document.documentElement.scrollHeight <= innerHeight + 1')
