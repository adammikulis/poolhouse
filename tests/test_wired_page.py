"""The device-memory panel on the Settings screen, driven in headless Chromium.

The page, the route and the estimator are real; the machine's facts are the ones the hooks name. The panel reads the
limit and sizes a model against it; it has no control that changes the limit.
"""

from __future__ import annotations

import platform

import pytest

pytestmark = pytest.mark.slow

from test_fleet_page import (  # noqa: E402, F401
    browser,
    daemon,
    joined,
    no_release_lookup,
    open_page,
)

from poolhouse.serve import wired  # noqa: E402

GIB = 1024**3


@pytest.fixture
def wired_page(joined, tmp_path, monkeypatch, open_page):  # noqa: F811
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    joined.ui.room_hooks = wired.Hooks(total=128 * GIB, system="Darwin", read=lambda: 0,
                                       daemon=tmp_path / "d.plist", others=0)
    page, errors = open_page(joined, cookie=joined.cookie)
    page.click("#nav-settings")
    page.get_by_role("tab", name="Compute & device").click()
    page.wait_for_selector("#settings-wired .room-pick")
    return page, errors


def test_the_panel_says_what_models_may_use_and_what_is_left(wired_page):
    page, errors = wired_page
    text = page.locator("#settings-wired").inner_text()
    assert "96.0 GB" in text and "32.0 GiB is left for everything else" in text
    assert "never changes it" in text
    assert not errors


def test_the_panel_has_no_control_that_changes_the_limit(wired_page):
    page, _ = wired_page
    assert page.locator("#settings-wired poolhouse-slider").count() == 0
    assert page.locator("#settings-wired button").count() == 0
    assert "password" not in page.locator("#settings-wired").inner_text().lower()
