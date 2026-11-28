"""What a device can do, driven in headless Chromium against a real daemon.

The model, the install and the other computer are canned plumbing (`device_uses_kit`), marked as such on screen. Each
journey saves a screenshot under the test's own folder and prints its path.
"""

from __future__ import annotations

import pytest
from extras import optional

pw = optional("playwright.sync_api")

pytestmark = [pytest.mark.slow, pytest.mark.needs("playwright.sync_api", reason="poolhouse[scrape]")]
pytest_plugins = ("testfarm_kit", "extras")

from device_uses_kit import OTHER, OWN, CannedEnvironment, canned_pool  # noqa: E402
from test_fleet_page import (  # noqa: E402, F401  (fixtures)
    browser,
    daemon,
    joined,
    no_release_lookup,
    open_page,
)

PANEL = "pool-view device-uses"


def shot(page, tmp_path, name):
    path = tmp_path / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)
    print(f"screenshot: {path}")


def card(page, use, panel=PANEL):
    return page.locator(f'{panel} [data-use="{use}"]')


def test_with_no_model_the_devices_screen_says_what_this_computer_can_do(joined, open_page, tmp_path):  # noqa: F811
    page, errors = open_page(joined, cookie=joined.cookie)
    page.get_by_role("heading", name="What this computer can do", exact=True).wait_for()
    panel = page.locator(PANEL)
    pw.expect(panel).to_contain_text("No AI model needed")
    pw.expect(panel).to_contain_text("does not split either one across computers")
    pw.expect(card(page, "chat")).to_contain_text("Needs a model")
    assert card(page, "guests").count() == 0, "a use whose screen the page does not have is left out"
    assert panel.locator("[data-use] button").count() == panel.locator("[data-use]").count(), "one action each"
    shot(page, tmp_path, "devices-no-model")
    page.set_viewport_size({"width": 420, "height": 900})
    assert panel.evaluate("n => n.scrollWidth <= n.clientWidth")
    shot(page, tmp_path, "devices-narrow")
    card(page, "models").get_by_role("button", name="Add a model", exact=True).click()
    page.wait_for_url("**/#models")
    assert not errors


def test_another_computers_page_offers_only_what_works_and_goes_back(joined, open_page, tmp_path):  # noqa: F811
    joined.ui.pool_status = lambda force=False: canned_pool()
    joined.ui.pool_actions = None
    page, errors = open_page(joined, cookie=joined.cookie)
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.get_by_role("button", name="What you can do with it: canned-laptop", exact=True).click()
    page.get_by_role("heading", name="What you can do with canned-laptop from here", exact=True).wait_for()
    panel = page.locator(PANEL)
    assert panel.locator("[data-use]").count() == 1
    pw.expect(panel).to_contain_text("cannot be sent to canned-laptop from here yet")
    card(page, "tests").get_by_role("button").click()
    pw.expect(card(page, "tests")).to_contain_text("Copied.")
    copied = page.evaluate("navigator.clipboard.readText()")
    assert OWN in copied and OTHER in copied
    shot(page, tmp_path, "other-computer")
    panel.get_by_role("button", name="Back to this computer", exact=True).click()
    page.get_by_role("heading", name="What this computer can do", exact=True).wait_for()
    assert not errors


def test_choosing_training_installs_what_it_needs_and_opens_the_studio(joined, open_page, tmp_path):  # noqa: F811
    joined.ui.environment = environment = CannedEnvironment()
    page, _ = open_page(joined, cookie=joined.cookie)
    pw.expect(card(page, "training")).to_contain_text("Installs when you choose it")
    shot(page, tmp_path, "training-needs-install")
    card(page, "training").get_by_role("button", name="Install and start", exact=True).click()
    page.wait_for_url("**/#studio")
    assert environment.asked == [["core", "torch-cpu"]]
    assert joined.ui.settings.download_sources == "both"


def test_first_run_shows_the_uses_and_an_action_finishes_setup(daemon, open_page, tmp_path):  # noqa: F811
    page, errors = open_page(daemon)
    page.fill("#setup-device-name", "quillhaven")
    panel = "first-run device-uses"
    card(page, "models", panel).wait_for()
    assert page.locator(f"{panel} .use-others").count() == 0
    shot(page, tmp_path, "first-run")
    card(page, "models", panel).get_by_role("button", name="Add a model", exact=True).click()
    page.wait_for_url("**/#models")
    assert daemon.ui.settings.setup_done is True and daemon.ui.name == "quillhaven"
    assert daemon.ui.settings.download_sources == "both"
    assert not errors


def test_adding_a_device_says_what_it_adds(joined, open_page, tmp_path):  # noqa: F811
    page, errors = open_page(joined, cookie=joined.cookie)
    page.click("pool-view summary:has-text('Pool connections')")
    form = page.locator("pool-view #pool-add-form")
    pw.expect(form).to_contain_text("What another computer adds")
    pw.expect(form).to_contain_text("you cannot yet send it chat, files, training or other jobs")
    shot(page, tmp_path, "adding-a-device")
    assert not errors
