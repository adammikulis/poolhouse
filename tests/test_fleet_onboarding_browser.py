"""First-launch progress and failed-save behavior in Chromium."""

import pytest
from browser_expect import expect

try:
    import playwright.sync_api  # noqa: F401
except ModuleNotFoundError:
    pytest_plugins = []

    @pytest.fixture
    def daemon():
        raise pytest.skip.Exception("needs playwright: poolhouse[scrape]", allow_module_level=False)

    @pytest.fixture
    def joined():
        raise pytest.skip.Exception("needs playwright: poolhouse[scrape]", allow_module_level=False)

    @pytest.fixture
    def open_page(daemon):
        return None
else:
    pytest_plugins = ["test_fleet_page"]

pytestmark = pytest.mark.slow


def test_setup_names_the_current_stage(daemon, open_page):
    page, errors = open_page(daemon)
    page.wait_for_selector("#first-run:not([hidden])")
    expect(page.locator("#first-run-card > .wizard-step")).to_have_text("Step 1 of 3")
    expect(page.get_by_role("heading", name="Welcome to Poolhouse", exact=True)).to_be_visible()
    page.get_by_role("button", name="Continue", exact=True).click()
    expect(page.locator("#first-run-card > .wizard-step")).to_have_text("Step 2 of 3")
    expect(page.get_by_role("heading", name="Make this device ready", exact=True)).to_be_visible()
    assert not errors


def test_failed_startup_save_stays_on_the_step(daemon, open_page, monkeypatch):
    monkeypatch.setattr(daemon.ui, "apply_prefs", lambda req: {"error": "Startup unavailable"})
    page, errors = open_page(daemon)
    page.wait_for_selector("#first-run:not([hidden])")
    page.get_by_role("button", name="Continue", exact=True).click()
    prepare = page.get_by_role("button", name="Prepare and continue", exact=True)
    prepare.click()
    page.wait_for_selector("#first-run [role=alert]:not(:empty)")
    assert page.locator("#first-run [role=alert]:visible").inner_text() == "Startup unavailable"
    assert prepare.is_enabled()
    assert page.locator("#first-run-card > .wizard-step").inner_text() == "Step 2 of 3"
    assert not errors
