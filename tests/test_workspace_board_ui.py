"""The ml-board element: what the folder holds, and the element driven in headless Chromium
against the real read-only route over the person's session on the node."""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from poolhouse import node_launch
from poolhouse.board import person
from poolhouse.ui import assets_dir
from poolhouse.workspace import boardroute

pytest_plugins = ["node_kit"]


@pytest.fixture
def team(workspace_node, monkeypatch):
    node = workspace_node
    monkeypatch.setattr(node_launch, "default_state", lambda: node.state)
    node.alice, node.bob = node.member("alice"), node.member("bob")
    node.sessions = {"alice": node.session(node.alice), "bob": node.session(node.bob)}
    node.me = person.session(Path.cwd(), node.client)
    return node


class Port(int):
    session = ""


@pytest.fixture
def served(team):
    server = boardroute.serve(lambda: person.session(Path.cwd(), team.client))
    server.start()
    port = Port(server.port)
    port.session = server.session
    yield port
    server.stop()


def test_the_page_is_served_with_a_policy_that_forbids_eval_and_other_origins(team, served):
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", int(served), timeout=10)
    conn.request("GET", "/")
    policy = conn.getresponse().getheader("Content-Security-Policy")
    assert "default-src 'none'" in policy and "unsafe-eval" not in policy
    assert "connect-src 'self'" in policy


def test_the_element_is_loaded_by_ml_ui_and_builds_nothing_from_markup():
    source = (assets_dir() / "board.js").read_text(encoding="utf-8")
    assert 'import "./board.js"' in (assets_dir() / "ml-ui.js").read_text(encoding="utf-8")
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "new Function",
                   "document.write", "window.open",
                   "target="):
        assert banned not in source, banned
    assert re.search(r'define\("ml-board"', source)
    assert 'method: document ? "POST" : "GET"' in source
    assert 'this.request("post", {}, document)' in source
    assert "PUT" not in source and "DELETE" not in source


@pytest.fixture(scope="module")
def browser():
    sync = pytest.importorskip("playwright.sync_api", reason="poolhouse[scrape]")
    with sync.sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True)
        except Exception as exc:                       # noqa: BLE001
            pytest.skip(f"chromium did not launch: {exc}")
        yield b
        b.close()


@pytest.mark.slow
def test_hostile_text_is_shown_as_text_and_the_page_runs_nothing_and_holds_no_token(team, served, browser):
    alice = team.sessions["alice"]
    alice.room("create", "#ops")
    alice.post("#ops", "note",
               '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>‮'
               "[link](http://evil.example) http://evil.example\x07", subject="<b>bold</b>")
    alice.post(team.me.name, "note", "a dm <i>x</i>")
    page = browser.new_context(bypass_csp=True).new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    seen = []
    page.on("request", lambda r: seen.append((r.method, r.url)))
    page.goto(f"http://127.0.0.1:{served}/?session={served.session}")
    page.wait_for_selector("ml-board", state="attached")
    page.wait_for_function("() => document.querySelector('ml-board')?.shadowRoot?.querySelector('nav button')")
    page.get_by_role("button", name="#ops").click()
    page.wait_for_function("() => document.querySelector('ml-board')?.shadowRoot?.querySelector('.row')")
    row = page.evaluate("document.querySelector('ml-board')?.shadowRoot?.querySelector('.row .subject').textContent")
    assert row == "<b>bold</b>"
    page.evaluate("document.querySelector('ml-board')?.shadowRoot?.querySelector('.row').click()")
    page.wait_for_function("() => document.querySelector('ml-board')?.shadowRoot?.querySelector('.msg pre')")
    found = page.evaluate("""() => {
      const root = document.querySelector('ml-board').shadowRoot;
      return { text: root.querySelector('.msg pre').textContent,
               images: root.querySelectorAll('img,script,a,iframe').length,
               pwned: window.__pwned ?? null, html: document.documentElement.outerHTML };
    }""")
    assert "<img src=x" in found["text"] and "‮" not in found["text"] and "\x07" not in found["text"]
    assert found["images"] == 0 and found["pwned"] is None and errors == []
    assert team.me.token not in found["html"] and all(m == "GET" for m, _ in seen)
    assert all(u.startswith(f"http://127.0.0.1:{served}/") for _, u in seen)
    page.get_by_role("button", name=f"{min(team.me.name, team.alice.name)} and {max(team.me.name, team.alice.name)}").click()
    page.wait_for_function("() => document.querySelector('ml-board')?.shadowRoot?.querySelector('.msg pre')"
                           "?.textContent.includes('a dm')")
    assert "a dm <i>x</i>" in page.evaluate("document.querySelector('ml-board')?.shadowRoot?.querySelector('.msg pre').textContent")
    page.close()


@pytest.mark.slow
def test_the_live_feed_picks_up_a_new_message_and_backs_off_when_the_route_fails(team, served, browser):
    alice = team.sessions["alice"]
    alice.room("create", "#ops")
    page = browser.new_context(bypass_csp=True).new_page()
    page.goto(f"http://127.0.0.1:{served}/?session={served.session}")
    page.wait_for_function("() => document.querySelector('ml-board')?.shadowRoot?.querySelector('nav button')")
    page.get_by_role("button", name="#ops").click()
    page.wait_for_timeout(500)
    began = time.monotonic()
    alice.post("#ops", "note", "later", subject="arrives by push")
    page.wait_for_function(
        "() => document.querySelector('ml-board')?.shadowRoot?.querySelector('.row .subject')?.textContent"
        " === 'arrives by push'", timeout=15000)
    assert time.monotonic() - began < 3
    delays = page.evaluate("import('/ui/ml-ui/board.js').then(m => [m.nextDelay(3000, false),"
                           " m.nextDelay(3000, true), m.nextDelay(40000, true), m.nextDelay(60000, true)])")
    assert delays == [3000, 6000, 60000, 60000]
    assert page.evaluate("import('/ui/ml-ui/board.js').then(m => m.line('a\\u202eb\\nc', 10))") == "a b c"
    page.close()


@pytest.mark.slow
def test_the_person_chats_from_the_page_and_an_agent_reads_it(team, served, browser):
    alice = team.sessions["alice"]
    alice.room("create", "#ops")
    posts = []
    page = browser.new_context(bypass_csp=True).new_page()
    page.on("request", lambda r: posts.append((r.method, r.url)) if r.method != "GET" else None)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{served}/?session={served.session}")
    page.wait_for_function("() => document.querySelector('ml-board')?.shadowRoot?.querySelector('nav button')")
    page.get_by_role("button", name="#ops").click()
    page.wait_for_function("() => document.querySelector('ml-board')?.shadowRoot?.querySelector('.composer textarea')")
    page.evaluate("""() => {
      const root = document.querySelector('ml-board').shadowRoot;
      root.querySelector('.composer input').value = 'from the page';
      const box = root.querySelector('.composer textarea');
      box.value = '<img src=x onerror="window.__pwned=1"> hello agents';
      box.dispatchEvent(new Event('input'));
      root.querySelector('.composer button').click();
    }""")
    page.wait_for_function(
        "() => document.querySelector('ml-board')?.shadowRoot?.querySelector('.row .subject')?.textContent === 'from the page'")
    mine = [m for m in alice.read(channel="#ops", kind="message").entries if m.sender == team.me.name]
    assert [m.text for m in mine] == ['<img src=x onerror="window.__pwned=1"> hello agents']
    assert page.evaluate("window.__pwned ?? null") is None and errors == []
    assert [m for m, _ in posts] == ["POST"] and posts[0][1].endswith("/board/post")
    page.evaluate("document.querySelector('ml-board').readonly = true")
    page.wait_for_function("() => !document.querySelector('ml-board').shadowRoot.querySelector('.composer')")
    page.close()


@pytest.mark.slow
def test_drafts_are_scoped_to_channel_and_survive_reload_without_posting(team, served, browser):
    from playwright.sync_api import expect

    alice = team.sessions["alice"]
    alice.room("create", '#ops')
    alice.room("create", '#second')
    context = browser.new_context()
    page = context.new_page()
    posts = []
    page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' else None)
    page.goto(f'http://127.0.0.1:{served}/?session={served.session}')
    page.get_by_role('button', name='#ops', exact=True).click()
    editor = page.locator('ml-board textarea')
    editor.fill('Unsent operations draft')
    page.get_by_role('button', name='#second', exact=True).click()
    expect(editor).to_have_value('')
    editor.fill('A different channel draft')
    page.reload()
    expect(editor).to_have_value('A different channel draft')
    page.get_by_role('button', name='#ops', exact=True).click()
    expect(editor).to_have_value('Unsent operations draft')
    assert not posts
    assert not team.me.read(channel='#ops', kind="message").entries
    context.close()


@pytest.mark.slow
def test_a_subagent_is_listed_with_its_parent_and_its_message_keeps_its_exact_name(team, served, browser):
    child = team.member("review", parent=team.alice)
    team.session(child).post("#general", "note", "child message", subject="Readable child")
    page = browser.new_context(bypass_csp=True).new_page()
    page.goto(f'http://127.0.0.1:{served}/?session={served.session}')
    page.get_by_role('button', name=team.alice.name, exact=True).wait_for()
    page.get_by_role('button', name=child.name, exact=True).wait_for()
    page.get_by_role('combobox', name='Message an agent').select_option(child.name)
    page.get_by_role('button', name='Open', exact=True).click()
    page.wait_for_function("name => document.querySelector('ml-board').view.b === name", arg=child.name)
    page.get_by_role('button', name='#general', exact=False).click()
    page.get_by_role('button', name='Readable child', exact=False).click()
    author = page.locator('ml-board .who')
    author.wait_for()
    assert author.inner_text().startswith(child.name)
    assert author.get_attribute('title') == child.name
    page.close()
