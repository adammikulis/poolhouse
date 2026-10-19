"""The Projects page in headless Chromium: where a shared project's source is, and getting it onto this device."""

import json
from urllib.parse import urlsplit

import pytest
from web_assets import serve_page_asset

from poolhouse.fleet.page import render

pytestmark = pytest.mark.slow

PROJECT = {"id": "project-a", "name": "Shared app", "member": "mac-test", "state": "shared"}


def answer(request, state):
    """The daemon's routes for the page: the shell, its assets, one shared project and its checkout."""
    path = urlsplit(request.request.url).path
    state["requests"].append((request.request.method, path))
    if request.request.method == "POST":
        state["posts"].append((path, request.request.post_data_json))
    if path == "/":
        return request.fulfill(body=render(), content_type="text/html")
    if serve_page_asset(request, path):
        return
    body = {"ok": True}
    if path == "/ui/setup":
        body = {"needs_setup": False, "needs_password": False, "name": "local-test"}
    elif path == "/ui/room":
        body = {"machine": {"supported": False}}
    elif path == "/ui/projects":
        body = {"projects": [PROJECT], "candidates": [{"id": "local-a", "name": "Local app"}],
                "devices": [{"name": "old-device", "state": "upgrade_required"}]}
    elif path == "/ui/projects/project-a/checkout":
        body = {"checkout": "/isolated/shared-app", "attached": False}
    return request.fulfill(body=json.dumps(body), content_type="application/json")


@pytest.fixture
def app(playwright):
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.set_default_timeout(5000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    state = {"requests": [], "posts": []}
    page.route("http://design.test/**", lambda request: answer(request, state))
    page.goto("http://design.test/")
    try:
        yield page, state, errors
    finally:
        browser.close()


def project_calls(state, method=None):
    return [(m, path) for m, path in state["requests"] if path.startswith("/ui/projects/") and method in (None, m)]


def test_projects_show_where_the_source_is_and_get_it_on_request(app):
    page, state, errors = app
    page.get_by_role("link", name="Work", exact=True).click()
    page.get_by_role("link", name="Projects", exact=True).click()
    page.get_by_text("old-device: Update Poolhouse on this device to share projects.", exact=True).wait_for()
    page.get_by_role("button", name="Open project", exact=True).click()
    assert page.get_by_text("Source available on mac-test", exact=True).is_visible()
    assert page.get_by_text("Agent access", exact=False).count() == 0
    assert project_calls(state) == []
    page.get_by_role("button", name="Get source on this device", exact=True).click()
    page.get_by_text("Source ready at /isolated/shared-app.", exact=True).wait_for()
    assert project_calls(state) == [("POST", "/ui/projects/project-a/checkout")]
    assert ("/ui/projects/project-a/checkout", {"member": "mac-test"}) in state["posts"]
    assert not errors


def test_sharing_a_local_project_posts_the_chosen_candidate(app):
    page, state, errors = app
    page.get_by_role("link", name="Work", exact=True).click()
    page.get_by_role("link", name="Projects", exact=True).click()
    page.get_by_role("button", name="Share with pool", exact=True).click()
    page.get_by_text("Done", exact=True).wait_for()
    assert ("/ui/projects", {"action": "share", "candidate_id": "local-a"}) in state["posts"]
    assert not errors
