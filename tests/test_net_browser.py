"""A browser a model drives cannot be pointed at this machine or its network."""

import contextlib
import socket
import threading

import pytest

from poolhouse.httpguard import Limits
from poolhouse.net import browserguard
from tests.net_site import Site

LOCAL = Limits(allow_hosts=frozenset({"127.0.0.1"}))


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost:8080/", "http://169.254.169.254/latest/", "http://10.0.0.1/",
    "http://[::1]/", "http://192.168.1.1/admin", "file:///etc/passwd", "ftp://example.org/x",
    "ws://127.0.0.1/socket", "chrome://settings", "http://user:pw@8.8.8.8/",
])
def test_requests_to_this_machine_and_its_network_are_not_allowed(url):
    assert browserguard.allowed(url) is False


@pytest.mark.parametrize("url", ["data:text/plain,hi", "blob:https://example.org/abc", "about:blank"])
def test_inline_content_reaches_nowhere_and_is_allowed(url):
    assert browserguard.allowed(url) is True


def test_a_public_address_is_allowed():
    assert browserguard.allowed("http://8.8.8.8/") is True


AROUND = """<html><body><p>hello</p><script>
const url = (tag) => 'ws://127.0.0.1:PORT/' + tag;
const worker = (tag) => URL.createObjectURL(new Blob(["new WebSocket('" + url(tag) + "')"], {type: 'text/javascript'}));
const tries = [
  () => new WebSocket(url('page')),
  () => new Worker(worker('worker')),
  () => new SharedWorker(worker('shared-worker')),
  () => { const f = document.createElement('iframe'); document.body.appendChild(f);
          new f.contentWindow.WebSocket(url('frame')); new f.contentWindow.Worker(worker('frame-worker')); },
  () => { const w = window.open(''); new w.Worker(worker('popup-worker')); },
  () => navigator.serviceWorker.register('/sw.js'),
];
for (const one of tries) { try { one(); } catch (e) {} }
</script></body></html>"""


@pytest.mark.slow
def test_a_real_browser_connects_no_socket_and_runs_nothing_that_sends_around_the_guard():
    sync = pytest.importorskip("playwright.sync_api")
    reached = []
    listener = socket.create_server(("127.0.0.1", 0))

    def listen():
        with contextlib.suppress(OSError):
            while True:
                connection, _ = listener.accept()
                reached.append(connection.recv(200).split(b"\r\n")[0])
                connection.close()

    threading.Thread(target=listen, daemon=True).start()
    try:
        with Site() as site:
            page_text = AROUND.replace("PORT", str(listener.getsockname()[1]))
            site.add("/page", page_text.encode(), headers={"Content-Type": "text/html"})
            worker = site.add("/sw.js", b"// plumbing only", headers={"Content-Type": "text/javascript"})
            with sync.sync_playwright() as play:
                try:
                    browser = play.chromium.launch(headless=True)
                except sync.Error as exc:
                    pytest.skip(f"no browser: {exc}")
                context = browser.new_context(accept_downloads=False, service_workers="block")   # as `scrape.browser` makes one
                refused = browserguard.install(context, LOCAL)
                page = context.new_page()
                page.goto(f"{site.base}/page", wait_until="load")
                page.wait_for_timeout(1500)
                browser.close()
    finally:
        listener.close()
    assert reached == [] and worker.seen == []
    assert any(url.endswith("/page") for url in refused)


@pytest.mark.slow
def test_a_real_browser_is_stopped_from_every_request_the_guard_refuses():
    sync = pytest.importorskip("playwright.sync_api")
    with Site() as site, Site() as other:
        other.add("/secret.png", b"\x89PNG\r\n\x1a\n")
        site.add("/page", (
            "<html><body><p>hello</p>"
            f'<img src="http://127.0.0.2:{other.port}/secret.png">'
            f'<img src="http://localhost:{other.port}/secret.png">'
            '<img src="http://169.254.169.254/latest/meta-data/">'
            "</body></html>").encode(), headers={"Content-Type": "text/html"})
        with sync.sync_playwright() as play:
            try:
                browser = play.chromium.launch(headless=True)
            except sync.Error as exc:
                pytest.skip(f"no browser: {exc}")
            context = browser.new_context(accept_downloads=False)
            refused = browserguard.install(context, LOCAL)
            page = context.new_page()
            page.goto(f"{site.base}/page", wait_until="load")
            text = page.inner_text("body")
            browser.close()
        assert "hello" in text
        assert other.routes["/secret.png"].seen == []
        assert any("169.254.169.254" in url for url in refused)
        assert any(f"localhost:{other.port}" in url for url in refused)
