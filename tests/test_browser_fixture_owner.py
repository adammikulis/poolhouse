"""Worker-owned browser lifetime and isolation between application journeys."""

import pytest
from test_fleet_usability_browser import usability_page  # noqa: F401
from test_studio_canvas_browser import studio_browser  # noqa: F401

pytestmark = pytest.mark.slow

ORIGIN = 'http://browser-fixture.invalid/'


def test_consumers_share_owner_and_driver_with_private_contexts(browser_owner, playwright, studio_browser, usability_page):  # noqa: F811
    _served, other, _errors = usability_page
    with studio_browser() as page:
        assert page.context.browser is browser_owner
        assert other.context.browser is browser_owner
        assert page.context is not other.context
        assert browser_owner.browser_type is playwright.chromium
    assert not other.is_closed()


def test_storage_routes_and_init_scripts_do_not_cross_journeys(browser_owner, studio_browser):  # noqa: F811
    with studio_browser() as first:
        first.route(ORIGIN + '**', lambda route: route.fulfill(body='<p>first journey</p>'))
        first.add_init_script('window.privateJourney = true')
        first.goto(ORIGIN)
        first.context.add_cookies([{'name': 'session', 'value': 'private', 'url': ORIGIN}])
        first.evaluate("localStorage.setItem('journey', 'private'); sessionStorage.setItem('journey', 'private')")
    with studio_browser() as second:
        assert second.context.browser is browser_owner
        second.context.route(ORIGIN + '**', lambda route: route.fulfill(body='<p>second journey</p>'))
        second.goto(ORIGIN)
        assert second.locator('p').inner_text() == 'second journey'
        assert second.context.cookies() == []
        assert second.evaluate("[localStorage.getItem('journey'), sessionStorage.getItem('journey'), window.privateJourney ?? null]") == [None, None, None]


def test_journey_failure_closes_only_its_context(browser_owner, studio_browser):  # noqa: F811
    before = list(browser_owner.contexts)
    with pytest.raises(RuntimeError, match='journey failed'), studio_browser() as page:
        assert page.context in browser_owner.contexts
        raise RuntimeError('journey failed')
    assert browser_owner.contexts == before
    assert browser_owner.is_connected()


def test_page_creation_failure_closes_its_context(browser_owner, studio_browser, monkeypatch):  # noqa: F811
    before = list(browser_owner.contexts)
    make_context = browser_owner.new_context

    def context_with_failed_page(**options):
        context = make_context(**options)

        def fail():
            raise RuntimeError('page creation failed')

        monkeypatch.setattr(context, 'new_page', fail)
        return context

    monkeypatch.setattr(browser_owner, 'new_context', context_with_failed_page)
    with pytest.raises(RuntimeError, match='page creation failed'), studio_browser():
        pytest.fail('a page must not be yielded')
    assert browser_owner.contexts == before
