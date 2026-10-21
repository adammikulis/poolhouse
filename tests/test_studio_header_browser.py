"""Studio header exposes workflow identity and discoverable secondary commands."""
import pytest
from browser_expect import expect
from test_studio_canvas_browser import add, studio_browser, studio_page  # noqa: F401
from test_studio_initial_load_browser import delayed_page, release  # noqa: F401

from poolhouse.fleet.themes import PRESETS

pytestmark = pytest.mark.slow


def menu(page, name):
    tools = page.get_by_role('button', name='Workflow tools', exact=True)
    if tools.is_visible() and tools.get_attribute('aria-expanded') == 'false':
        tools.click()
    return page.locator('.studio-command').filter(has=page.get_by_text(name, exact=True))


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('width', [1440, 390])
def test_header_identity_and_keyboard_commands_remain_compact(studio_page, theme, width):  # noqa: F811
    page, _state, errors = studio_page
    page.set_viewport_size({'width': width, 'height': 850})
    page.wait_for_function('() => !!fleetModel.preferences.resolved_theme')
    page.evaluate('theme => fleetModel.setPreferences({...fleetModel.preferences,resolved_theme:theme})', PRESETS[theme])
    expect(page.get_by_label('Workflow title', exact=True)).to_be_visible()
    for name in ['New', 'Save', 'Run workflow']:
        expect(page.get_by_role('button', name=name, exact=True)).to_be_visible()
    assert page.get_by_role('heading', name='Studio', exact=True).bounding_box()['width'] == 1
    opened = menu(page, 'Open workflow')
    opened.locator('summary').click()
    expect(page.get_by_label('Saved workflows', exact=True)).to_be_visible()
    edit = menu(page, 'Edit')
    edit.locator('summary').focus()
    page.keyboard.press('Enter')
    expect(opened).not_to_have_attribute('open', '')
    expect(page.get_by_role('button', name='Undo', exact=True)).to_be_disabled()
    page.keyboard.press('Tab')
    page.keyboard.press('Escape')
    expect(edit.locator('summary')).to_be_focused()
    expect(edit).not_to_have_attribute('open', '')
    add(page, 'Prompt template')
    edit = menu(page, 'Edit')
    edit.locator('summary').click()
    page.get_by_role('button', name='Undo', exact=True).click()
    expect(edit.locator('summary')).to_be_focused()
    expect(page.locator('.studio-node')).to_have_count(0)
    edit.locator('summary').click()
    page.get_by_role('button', name='Redo', exact=True).click()
    expect(page.locator('.studio-node')).to_have_count(1)
    view = menu(page, 'View')
    view.locator('summary').click()
    page.get_by_role('button', name='Outline', exact=True).click()
    expect(page.locator('.studio-outline')).to_be_visible()
    share = menu(page, 'Share and import')
    share.locator('summary').click()
    page.get_by_role('button', name='Share', exact=True).click()
    expect(page.locator('#studio-share-dialog')).to_be_visible()
    page.get_by_role('button', name='Close', exact=True).click()
    view.locator('summary').click()
    page.get_by_label('Workflow title', exact=True).click()
    expect(view).not_to_have_attribute('open', '')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert page.evaluate('document.documentElement.scrollHeight <= innerHeight')
    assert not errors


def test_pending_run_keeps_primary_busy_state_and_menus_do_not_submit(studio_page):  # noqa: F811
    page, state, errors = studio_page
    add(page, 'Prompt template')
    held = []

    def submit(route):
        if route.request.method == 'POST':
            held.append(route)
        else:
            route.fallback()

    page.route('**/ui/studio/runs', submit)
    run = page.get_by_role('button', name='Run workflow', exact=True)
    run.click()
    expect(run).to_be_disabled()
    edit = menu(page, 'Edit')
    edit.locator('summary').click()
    expect(page.get_by_role('button', name='Undo', exact=True)).to_be_enabled()
    page.keyboard.press('Escape')
    expect(edit.locator('summary')).to_be_focused()
    assert len(held) == 1
    result = {'id': 'run-1', 'state': 'done', 'revision': 1, 'nodes': {}}
    state['runs'] = [result]
    held[0].fulfill(json={'run': result})
    expect(run).to_be_enabled()
    assert len(held) == 1
    assert not errors


def test_header_commands_preserve_invalid_draft_and_canvas_selection(studio_page, tmp_path):  # noqa: F811
    page, state, errors = studio_page
    add(page, 'Report')
    page.get_by_text('Sweep parameters', exact=True).click()
    page.get_by_label('Sweep iterations', exact=True).fill('invalid integer')
    selected = page.locator('studio-view').evaluate('e => [...e.selected]')
    edit = menu(page, 'Edit')
    edit.locator('summary').focus()
    page.keyboard.press('Enter')
    page.keyboard.press('Escape')
    assert page.locator('studio-view').evaluate('e => [...e.selected]') == selected
    page.get_by_role('button', name='Save', exact=True).click()
    expect(page.get_by_label('Sweep iterations', exact=True)).to_be_focused()
    expect(page.get_by_label('Sweep iterations', exact=True)).to_have_value('invalid integer')
    page.screenshot(path=tmp_path / 'invalid-draft.png')
    assert state['saved'] == [] and state['submissions'] == []
    menu(page, 'Open workflow').locator('summary').click()
    page.get_by_label('Saved workflows', exact=True).select_option('')
    expect(page.get_by_label('Sweep iterations', exact=True)).to_be_focused()
    assert state['saved'] == [] and state['submissions'] == []
    assert not errors


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_mobile_tools_keep_primary_actions_and_return_keyboard_focus(studio_page, tmp_path, theme):  # noqa: F811
    page, _state, errors = studio_page
    page.set_viewport_size({'width': 390, 'height': 600})
    page.evaluate('theme=>fleetModel.setPreferences({...fleetModel.preferences,resolved_theme:theme})', PRESETS[theme])
    tools = page.get_by_role('button', name='Workflow tools', exact=True)
    expect(tools).to_be_visible()
    expect(tools).to_have_attribute('aria-expanded', 'false')
    assert page.locator('.studio-heading').bounding_box()['height'] <= 110
    page.screenshot(path=tmp_path / 'mobile-primary.png')
    before = page.locator('studio-view').evaluate('e=>({graph:structuredClone(e.rootGraph()),serial:e.editSerial})')
    tools.focus()
    tools.press('Enter')
    expect(page.locator('.studio-command-bar')).to_be_visible()
    page.screenshot(path=tmp_path / 'mobile-tools.png')
    page.keyboard.press('Escape')
    expect(tools).to_be_focused()
    expect(tools).to_have_attribute('aria-expanded', 'false')
    menu(page, 'Share and import').locator('summary').click()
    page.get_by_role('button', name='Share', exact=True).click()
    page.screenshot(path=tmp_path / 'mobile-share-dialog.png')
    expect(page.get_by_role('button', name='Close', exact=True)).to_be_in_viewport()
    expect(page.get_by_role('button', name='Export file', exact=True)).to_be_in_viewport()
    page.locator('#studio-share-dialog').evaluate('dialog=>dialog.style.fontSize="200%"')
    page.get_by_label('Licence', exact=True).focus()
    expect(page.get_by_label('Licence', exact=True)).to_be_in_viewport()
    expect(page.get_by_role('button', name='Close', exact=True)).to_be_in_viewport()
    page.screenshot(path=tmp_path / 'mobile-share-expanded-text.png')
    page.get_by_role('button', name='Close', exact=True).click()
    expect(menu(page, 'Share and import').locator('summary')).to_be_focused()
    page.get_by_label('Workflow title', exact=True).click()
    expect(tools).to_have_attribute('aria-expanded', 'false')
    assert page.locator('studio-view').evaluate('e=>({graph:structuredClone(e.rootGraph()),serial:e.editSerial})') == before
    assert not errors


@pytest.mark.parametrize('delayed_page', ['/workflows'], indirect=True)
@pytest.mark.parametrize('width', [1440, 390])
def test_open_workflow_menu_stays_open_when_initial_inventory_arrives(delayed_page, width):  # noqa: F811
    page, state, old, errors = delayed_page
    page.set_viewport_size({'width': width, 'height': 850})
    opened = menu(page, 'Open workflow')
    opened.locator('summary').click()
    release(page, state)
    expect(opened).to_have_attribute('open', '')
    select = page.get_by_label('Saved workflows', exact=True)
    expect(select).to_be_visible()
    select.select_option(old['id'])
    expect(page.get_by_label('Workflow title', exact=True)).to_have_value(old['title'])
    assert not errors


def test_share_escape_closes_dialog_without_changing_underlying_canvas(studio_page):  # noqa: F811
    page, _state, errors = studio_page
    page.set_viewport_size({'width': 390, 'height': 600})
    add(page, 'Prompt template')
    before = page.locator('studio-view').evaluate('e=>({selected:[...e.selected],serial:e.editSerial})')
    share = menu(page, 'Share and import')
    share.locator('summary').click()
    page.get_by_role('button', name='Share', exact=True).click()
    page.keyboard.press('Escape')
    expect(page.locator('#studio-share-dialog')).to_be_hidden()
    expect(share.locator('summary')).to_be_focused()
    assert page.locator('studio-view').evaluate('e=>({selected:[...e.selected],serial:e.editSerial})') == before
    assert not errors


def test_long_share_content_scrolls_inside_modal_with_actions_visible(studio_page, tmp_path):  # noqa: F811
    page, _state, errors = studio_page
    page.set_viewport_size({'width': 390, 'height': 600})
    add(page, 'Prompt template')
    prompt = page.locator('.studio-inspector').get_by_label('Prompt text', exact=True)
    prompt.fill('evidence' * 500)
    prompt.press('Tab')
    menu(page, 'Share and import').locator('summary').click()
    page.get_by_role('button', name='Share', exact=True).click()
    expect(page.locator('.studio-dialog-content #studio-portable')).to_have_count(1)
    expect(page.get_by_role('button', name='Close', exact=True)).to_be_in_viewport()
    page.locator('#studio-portable input').focus()
    expect(page.locator('#studio-portable input')).to_be_in_viewport()
    expect(page.get_by_role('button', name='Export file', exact=True)).to_be_in_viewport()
    page.screenshot(path=tmp_path / 'mobile-share-long-content.png')
    assert not errors
