"""The experimental notice is text on the page, not a dialog: nothing in the fleet interface waits for a person to accept it."""

from __future__ import annotations

from pathlib import Path

import pytest
from browser_expect import expect
from test_studio_canvas_browser import studio_browser  # noqa: F401 (fixture)
from web_assets import SimulatedDaemon

from poolhouse.fleet import notice_copy, page, routes, settings

WEB = Path(page.__file__).parent / "web"


@pytest.mark.slow
@pytest.mark.parametrize("width", [1440, 390])
def test_the_notice_opens_from_the_footer_and_never_pushes_the_app_down(studio_browser, width):  # noqa: F811
    pytest.importorskip("playwright.sync_api")
    daemon = SimulatedDaemon()
    fits = "document.documentElement.scrollHeight <= innerHeight && document.documentElement.scrollWidth <= innerWidth"
    with studio_browser() as screen:
        screen.set_viewport_size({"width": width, "height": 900})
        daemon.attach(screen)
        screen.goto(daemon.url)
        expect(screen.locator("studio-view > section")).to_be_visible()
        assert screen.locator("#app").bounding_box()["y"] == 0
        assert screen.evaluate(fits)
        warning = screen.get_by_text(notice_copy.PARAGRAPHS[1], exact=True)
        expect(warning).to_be_hidden()
        screen.locator(".version-footer").get_by_text(notice_copy.TITLE, exact=True).click()
        expect(warning).to_be_in_viewport(ratio=1)
        expect(screen.get_by_role("link", name="Report bugs on GitHub", exact=True)).to_be_in_viewport(ratio=1)
        assert screen.evaluate(fits)


def test_the_measurement_page_without_the_app_shows_the_notice_outright():
    html = page.render(page.FIT_ONLY)
    assert 'class="card wide calculation-notice"' in html and notice_copy.PARAGRAPHS[1] in html
    assert 'class="experimental-note"' not in html


def test_the_page_carries_the_notice_as_text_and_no_dialog_or_accept_button():
    html = page.render()
    assert "Poolhouse is experimental" in html and "Report bugs on GitHub" in html
    assert "experimental-notice" not in html and "experimental-accept" not in html
    assert not (WEB / "components" / "experimental-notice.html").exists()


def test_no_route_takes_an_acceptance_and_no_setting_holds_one():
    served = [p for p in Path(routes.__file__).parent.rglob("*") if p.suffix in {".py", ".html"} and "/ui/notice" in p.read_text(encoding="utf-8")]
    assert served == []
    assert "experimental_notice_accepted" not in settings.Settings.__dataclass_fields__
    assert not hasattr(settings.Settings, "accept_notice")
