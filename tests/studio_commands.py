"""Command disclosures open before a browser exercises Studio actions."""

COMMANDS = {
    'Undo': ('Edit', 'undo'), 'Redo': ('Edit', 'redo'),
    'Copy': ('Edit', 'copy'), 'Paste': ('Edit', 'paste'),
    'Delete': ('Edit', 'delete'), 'Group selection': ('Edit', 'group'),
    'Arrange': ('View', 'layout'), 'Outline': ('View', 'outline-toggle'),
    'Live sources': ('View', 'live'),
    'Share': ('Share and import', 'share'), 'Import': ('Share and import', 'import'),
    'Pool library': ('Share and import', 'package-library'),
}


def studio_tools(page):
    tools = page.get_by_role('button', name='Workflow tools', exact=True)
    if tools.is_visible() and tools.get_attribute('aria-expanded') == 'false':
        tools.click()


def studio_command(page, name):
    studio_tools(page)
    disclosure, control = COMMANDS[name]
    button = page.locator('#studio-' + control)
    if not button.is_visible():
        page.locator('.studio-command > summary').filter(has_text=disclosure).click()
    return button


def workflow_picker(page):
    studio_tools(page)
    select = page.get_by_label('Saved workflows', exact=True)
    if not select.is_visible():
        page.locator('.studio-command > summary').filter(has_text='Open workflow').click()
    return select
