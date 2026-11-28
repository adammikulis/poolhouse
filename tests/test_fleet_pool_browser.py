"""Pool overview and benchmark controls driven in Chromium, on a daemon simulated in the browser (tests/web_assets.py)."""

import pytest
from web_assets import SimulatedDaemon, canned, fleet_answer, pool_answer

pytestmark = pytest.mark.slow


@pytest.fixture
def pool_page(browser_owner):
    """``open(answers)`` shows the Pool page of a simulated daemon: ``(page, errors)``."""
    pytest.importorskip("playwright.sync_api")
    errors = []
    with browser_owner.pages(viewport={"width": 1280, "height": 900}) as page:
        page.on("pageerror", lambda error: errors.append(str(error)))

        def show(answers):
            daemon = SimulatedDaemon(answers)
            daemon.attach(page)
            page.goto(daemon.url + "#pool")
            return page, errors

        yield show


def test_pool_capacity_selection_and_leave_failure(pool_page):
    from playwright.sync_api import expect
    fleet = fleet_answer({"name": "cedar", "slots": 3, "device": {}, "models": ["demo-model"]})
    left = []

    def pool(request):
        if request.request.method == "DELETE":
            left.append(request.request.url)
            return canned(request, {"error": "Pool has active jobs"}, 400)
        return canned(request, pool_answer("governed"))

    page, errors = pool_page({"/ui/fleet": lambda request: canned(request, fleet), "/ui/pool": pool})
    expect(page.locator("#pool-stat")).to_contain_text("0/3")
    expect(page.get_by_role("heading", name="Devices", exact=True)).to_be_visible()
    page.get_by_text("Pool connections & joining", exact=True).click()
    page.locator("#pool-joined").get_by_role("button", name="Leave", exact=True).click()
    expect(page.locator("#pool-joined [role=alert]")).to_have_text("Pool has active jobs")
    assert len(left) == 1
    page.locator("#pool-sweep summary").first.click()
    page.locator("#bench-model-0").check()
    expect(page.locator("#benchmark-run")).to_be_enabled()
    fleet["members"].clear()
    page.evaluate("document.querySelector('pool-view').draw()")
    expect(page.locator("#benchmark-run")).to_be_disabled()
    expect(page.locator("#benchmark-status")).to_contain_text("Choose at least one device")
    assert not errors


def test_benchmark_rejection_and_stop_error_are_visible(pool_page):
    from playwright.sync_api import expect
    page, errors = pool_page({"/ui/fleet": fleet_answer({"name": "cedar", "device": {}, "models": ["demo-model"]}),
                              "/ui/bench/sweep": {"ok": False}})
    page.locator("#pool-sweep summary").first.click()
    page.locator("#bench-model-0").check()
    page.locator("#benchmark-run").click()
    expect(page.locator("#benchmark-status")).to_contain_text("Could not start benchmark")
    expect(page.locator("#benchmark-stop")).to_be_hidden()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors
