"""The pages work from a plain-http, non-secure origin, which is how a person reaches a daemon over a LAN address.

A browser exposes `crypto.randomUUID`, `crypto.subtle`, a service worker and the like only to a secure context (https
or loopback). `http://<this machine's LAN address>:<port>` is neither. The daemon listens on loopback, so a byte
forwarder on the LAN address stands in for the network between two machines; the page, its origin and the walk through
the chat are the real ones, and nothing here stands in for a missing browser API.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from test_fleet_ui import WORDS, Serving

from poolhouse.fleet.conversations import Conversations
from poolhouse.fleet.serving import Serving as Models
from poolhouse.testing.canned import CANNED_MODEL_ID, Canned, CannedServer, marked
from tests.pool_support import join_pool_by_passphrase

pytestmark = pytest.mark.slow

LAN_ADDRESS = "192.0.2.10"
"""A documentation address: a browser treats it as an insecure origin, and the flag maps it to loopback."""
PIECES = ("Plain ", "http ", "works.")
SECURE_ONLY = ("crypto.randomUUID", "crypto.subtle", "navigator.serviceWorker", "getUserMedia",
               "showDirectoryPicker", "showOpenFilePicker", "showSaveFilePicker")


def test_the_chat_is_answered_from_a_plain_http_lan_address(tmp_path, monkeypatch, playwright):
    import time

    from playwright.sync_api import Error, expect

    monkeypatch.setenv("POOLHOUSE_CACHE", str(tmp_path / "cache"))
    model = CannedServer(Canned(pieces=PIECES, gap=0.0))
    served = Serving(tmp_path, name="laptop")
    try:
        browser = playwright.chromium.launch(args=[f"--host-resolver-rules=MAP {LAN_ADDRESS} 127.0.0.1"])
    except Error as exc:                                # pragma: no cover - depends on setup
        served.close()
        model.close()
        pytest.skip(f"chromium did not launch: {exc}")
    try:
        join_pool_by_passphrase(WORDS, group="home", path=served.keyfile)
        served.call("/ui/setup/join", method="POST", body={"passphrase": WORDS, "group": "home"})
        served.ui.setup_finished()
        served.ui.root = served.files.parent
        served.ui.conversations = Conversations(tmp_path / "chats")
        models = Models(tmp_path / "serving.json")
        decoder = models.register(model.port, [CANNED_MODEL_ID])
        decoder.capabilities = {"chat": True}
        monkeypatch.setattr(models, "all", lambda: [decoder])
        served.ui.serving = models
        served.ui._members = (time.time(), [])

        page = browser.new_page()
        page.set_default_timeout(15000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"http://{LAN_ADDRESS}:{served.port}/ui#chat")
        page.fill("#p", WORDS)
        page.click("#signin-go")
        page.locator("#chat-askrow:not([hidden])").wait_for()
        assert page.evaluate("window.isSecureContext") is False
        assert page.evaluate("typeof crypto.randomUUID") == "undefined"

        page.fill("#ask", "hello over the LAN")
        page.click("#chat-send")
        expect(page.locator("#chat-messages")).to_contain_text(marked("".join(PIECES)))
        expect(page.locator("#chat-messages")).to_contain_text("hello over the LAN")
        assert errors == []
        kept = served.ui.conversations.search("")
        assert [[m.content for m in one.messages] for one in kept] == [["hello over the LAN",
                                                                         marked("".join(PIECES))]]
    finally:
        browser.close()
        served.close()
        model.close()


def test_no_page_calls_a_browser_api_that_only_a_secure_context_has():
    root = Path(__file__).resolve().parent.parent / "src" / "poolhouse"
    found = [f"{path.relative_to(root)}:{number}: {line.strip()[:100]}"
             for path in sorted(root.rglob("*")) if path.suffix in {".html", ".js", ".mjs"}
             for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
             if any(api in line for api in SECURE_ONLY)]
    assert found == []
