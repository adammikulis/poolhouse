"""Pool overview and benchmark controls driven in Chromium."""

import pytest

pytest_plugins = ("test_fleet_usability_browser",)

pytestmark = pytest.mark.slow


def test_pool_capacity_selection_and_leave_failure(usability_page):
    from playwright.sync_api import expect
    served, page, errors = usability_page
    members = [{"name": "cedar", "slots": 3, "device": {}, "models": ["demo-model"]}]
    page.route("**/ui/fleet", lambda route: route.fulfill(json={"members": members, "models": ["demo-model"], "bench": {}}))
    page.route("**/ui/pools", lambda route: route.fulfill(status=400, json={"error": "Pool has active jobs"})
               if route.request.method == "DELETE" else route.fulfill(json={"pools": [{"group": "lab", "mode": "prod"}]}))
    page.goto(f"http://127.0.0.1:{served.port}/ui/#pool")
    expect(page.locator("#pool-stat")).to_contain_text("0/3")
    expect(page.get_by_role("heading", name="Compute pool", exact=True)).to_be_visible()
    page.get_by_text("Manage pool connections", exact=True).click()
    page.locator("#pool-joined").get_by_role("button", name="Leave", exact=True).click()
    expect(page.locator("#pool-joined [role=alert]")).to_have_text("Pool has active jobs")
    page.locator("#pool-sweep summary").first.click()
    page.locator("#bench-model-0").check()
    expect(page.locator("#benchmark-run")).to_be_enabled()
    members.clear()
    page.evaluate("document.querySelector('pool-view').draw()")
    expect(page.locator("#benchmark-run")).to_be_disabled()
    expect(page.locator("#benchmark-status")).to_contain_text("Choose at least one device")
    assert not errors


def test_benchmark_rejection_and_stop_error_are_visible(usability_page):
    from playwright.sync_api import expect
    served, page, errors = usability_page
    page.route("**/ui/fleet", lambda route: route.fulfill(json={"members": [{"name": "cedar", "device": {}, "models": ["demo-model"]}], "models": ["demo-model"], "bench": {}}))
    page.route("**/ui/bench/sweep", lambda route: route.fulfill(json={"ok": False}))
    page.goto(f"http://127.0.0.1:{served.port}/ui/#pool")
    page.locator("#pool-sweep summary").first.click()
    page.locator("#bench-model-0").check()
    page.locator("#benchmark-run").click()
    expect(page.locator("#benchmark-status")).to_contain_text("Could not start benchmark")
    expect(page.locator("#benchmark-stop")).to_be_hidden()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors
