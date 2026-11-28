"""A browser a model drives cannot be pointed at this machine or its network: whatever in a page asks, the connection goes through
the guard's proxy. Every server here is plumbing only: fixed bytes on loopback. The one the guard is told it may reach (``outside``)
stands for the public page; the other (``local``) is the service on this machine that nothing may reach. No name resolves."""

import base64
import contextlib
import importlib
import os
import socket
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler

import pytest

from poolhouse import httpguard, web
from poolhouse.http import Refused, Server
from poolhouse.httpguard import Limits

pytest_plugins = ["tests.sandbox_kit"]
browser = importlib.import_module("poolhouse.scrape.browser")  # the package exports a function of the same name
SECRET = "CANNED-LOCAL-SERVICE-CONTENT"


class Canned:
    """A server on loopback that counts connections, records requests, redirects on ``/r`` (302) and ``/r307``, and answers
    everything else with ``page``."""

    def __init__(self, page: str = "") -> None:
        self.page, self.connections, self.requests = page, 0, []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def answer(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.requests.append(f"{self.command} {self.path} {self.headers.get('Upgrade') or ''}".strip())
                parts = urllib.parse.urlsplit(self.path)
                moved = parts.path in ("/r", "/r307")
                body = b"" if moved else outer.page.encode()
                self.send_response((307 if parts.path == "/r307" else 302) if moved else 200)
                if moved:
                    self.send_header("Location", urllib.parse.parse_qs(parts.query)["to"][0])
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = answer

        class Counting(Server):
            def verify_request(self, request: object, client_address: object) -> bool:
                outer.connections += 1
                return True

            def handle_error(self, request: object, client_address: object) -> None:
                pass

        self.server = Counting(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        self.base = f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> "Canned":
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def outside(monkeypatch):
    """The page the guard may reach: it is named in the operator's list of allowed hosts by its own port, and no name resolves."""
    monkeypatch.setattr(httpguard, "_system_resolver", lambda host, port: [])
    with Canned() as made:
        monkeypatch.setenv(httpguard.ALLOW_ENV, f"127.0.0.1:{made.port},outside.example")
        yield made


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost:8080/", "http://169.254.169.254/latest/", "http://10.0.0.1/",
    "http://[::1]/", "http://192.168.1.1/admin", "http://0.0.0.0/", "file:///etc/passwd", "ftp://example.org/x",
    "ws://127.0.0.1/socket", "chrome://settings", "http://user:pw@8.8.8.8/",
])
def test_nothing_on_this_machine_or_its_network_may_be_connected_to(url):
    from poolhouse.scrape import browserguard

    assert browserguard.allowed(url) == []


def test_a_public_address_may_be_connected_to_and_is_the_address_given_back():
    from poolhouse.scrape import browserguard

    assert browserguard.allowed("http://8.8.8.8/") == ["8.8.8.8"]
    assert browserguard.allowed("https://tessyn.example/", Limits(resolver=lambda host, port: ["8.8.4.4"])) == ["8.8.4.4"]
    mixed = Limits(resolver=lambda host, port: ["8.8.4.4", "127.0.0.1"])
    assert browserguard.allowed("https://tessyn.example/", mixed) == [], "one private answer refuses the name"


def asked(guard, request: bytes, then: bytes = b"", credential: bytes | None = None) -> bytes:
    """Everything ``guard``'s proxy answers to ``request``, which presents the guard's own credential unless ``credential`` gives
    other header lines; ``then`` is sent once the first answer has arrived."""
    if credential is None:
        encoded = base64.b64encode(f"poolhouse:{guard.password}".encode())
        credential = b"Proxy-Authorization: Basic " + encoded + b"\r\n"
    first, rest = request.split(b"\r\n", 1)
    request = first + b"\r\n" + credential + rest
    with socket.create_connection(("127.0.0.1", guard.port), timeout=10) as client:
        client.sendall(request)
        got = bytearray()
        while data := client.recv(65536):
            got.extend(data)
            if then:
                client.sendall(then)
                then = b""
        return bytes(got)


@contextlib.contextmanager
def recording(reply: bytes):
    """A server that takes one connection, answers its first bytes with ``reply`` and keeps all it was sent."""
    listener = socket.create_server(("127.0.0.1", 0))
    got = bytearray()

    def serve() -> None:
        with contextlib.suppress(OSError):
            connection, _ = listener.accept()
            with connection:
                got.extend(connection.recv(65536))
                connection.sendall(reply)
                connection.settimeout(0.5)
                while data := connection.recv(65536):
                    got.extend(data)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1], got
    finally:
        listener.close()
        thread.join(5)


@pytest.mark.parametrize("request_line", [
    "CONNECT 127.0.0.1:{port} HTTP/1.1", "CONNECT localhost:{port} HTTP/1.1", "CONNECT [::1]:{port} HTTP/1.1",
    "CONNECT rebound.example:{port} HTTP/1.1", "GET http://127.0.0.1:{port}/secret HTTP/1.1",
    "GET http://rebound.example:{port}/secret HTTP/1.1", "GET http://2130706433:{port}/ HTTP/1.1",
])
def test_the_proxy_connects_to_nothing_on_this_machine_however_it_is_named(request_line):
    from poolhouse.scrape import browserguard

    rebound = Limits(resolver=lambda host, port: ["127.0.0.1"])   # the name's answer by the time the browser connects
    with Canned(SECRET) as local, browserguard.Guard(rebound) as guard:
        line = request_line.format(port=local.port)
        answer = asked(guard, f"{line}\r\nHost: x\r\n\r\n".encode())
        assert answer.startswith(b"HTTP/1.1 403 ") and SECRET.encode() not in answer
        assert local.connections == 0 and len(guard.refused) == 1


def test_the_proxy_dials_the_address_it_checked_and_sends_one_request_for_one_check():
    from poolhouse.scrape import browserguard

    looked = []

    def resolver(host, port):
        looked.append(host)
        return ["127.0.0.1"]

    reply = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: keep-alive\r\n\r\nok"
    with recording(reply) as (port, got):
        limits = Limits(allow_hosts=frozenset({"outside.example"}), resolver=resolver)   # the system knows no such name
        with browserguard.Guard(limits) as guard:
            request = (f"POST http://outside.example:{port}/a?x=1 HTTP/1.1\r\nHost: elsewhere\r\nProxy-Connection: keep-alive\r\n"
                       "Content-Length: 3\r\n\r\nabc"
                       "GET http://127.0.0.1:1/second HTTP/1.1\r\nHost: x\r\n\r\n").encode()
            answer = asked(guard, request, then=b"GET http://127.0.0.1:1/third HTTP/1.1\r\nHost: x\r\n\r\n")
    assert answer == b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
    assert bytes(got) == (f"POST /a?x=1 HTTP/1.1\r\nHost: outside.example:{port}\r\ncontent-length: 3\r\n"
                          "Connection: close\r\n\r\nabc").encode()
    assert looked == ["outside.example"]


def test_the_proxy_tunnels_to_a_host_it_may_reach():
    from poolhouse.scrape import browserguard

    with recording(b"pong") as (port, got):
        limits = Limits(allow_hosts=frozenset({"outside.example"}), resolver=lambda host, number: ["127.0.0.1"])
        with browserguard.Guard(limits) as guard:
            answer = asked(guard, f"CONNECT outside.example:{port} HTTP/1.1\r\nHost: x\r\n\r\n".encode(), then=b"ping")
    assert answer == b"HTTP/1.1 200 Connection established\r\n\r\npong"
    assert bytes(got) == b"ping"


@pytest.mark.parametrize("request_bytes", [
    b"POST http://127.0.0.1:PORT/ HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n",
    b"GET /secret HTTP/1.1\r\nHost: 127.0.0.1:PORT\r\n\r\n",
    b"CONNECT 127.0.0.1:PORT/path HTTP/1.1\r\n\r\n",
])
def test_the_proxy_takes_no_request_it_would_have_to_guess_at(outside, request_bytes):
    from poolhouse.scrape import browserguard

    with browserguard.Guard() as guard:
        answer = asked(guard, request_bytes.replace(b"PORT", str(outside.port).encode()))
    assert answer.startswith(b"HTTP/1.1 400 ") and outside.connections == 0


NAMED = Limits(allow_hosts=frozenset({"beyond.example"}), resolver=lambda host, port: ["127.0.0.1"])
"""A canned server stands for a host beyond this machine, which only the guard's own resolver names."""


@pytest.mark.parametrize("credential", [
    b"", b"Proxy-Authorization: Basic cG9vbGhvdXNlOndyb25n\r\n", b"Authorization: Basic cG9vbGhvdXNlOndyb25n\r\n", b"another guard's",
])
@pytest.mark.parametrize("request_line", ["CONNECT beyond.example:{port} HTTP/1.1", "GET http://beyond.example:{port}/x HTTP/1.1"])
def test_the_proxy_serves_nobody_without_its_guards_own_credential(credential, request_line):
    from poolhouse.scrape import browserguard

    with Canned(SECRET) as beyond, browserguard.Guard(NAMED) as guard, browserguard.Guard(NAMED) as other:
        assert guard.password != other.password and guard.password not in repr(guard)
        if credential == b"another guard's":
            credential = b"Proxy-Authorization: Basic " + base64.b64encode(f"poolhouse:{other.password}".encode()) + b"\r\n"
        answer = asked(guard, (request_line.format(port=beyond.port) + "\r\nHost: x\r\n\r\n").encode(), credential=credential)
        assert answer.startswith(b"HTTP/1.1 407 ") and b'Proxy-Authenticate: Basic realm="poolhouse"' in answer
        assert beyond.connections == 0 and guard.refused == []


def test_connections_that_say_nothing_do_not_keep_the_guards_browser_out(monkeypatch):
    from poolhouse.scrape import browserguard

    monkeypatch.setattr(browserguard, "HEAD_SECONDS", 0.2, raising=False)
    with browserguard.Guard() as guard:
        guard._server.limit = 2   # the machine's own bound, made small enough to fill here
        idle = [socket.create_connection(("127.0.0.1", guard.port), timeout=10) for _ in range(2)]
        try:
            for one in idle:
                while one.recv(65536):   # the proxy gives up on a connection that has not said who it is
                    pass
            assert asked(guard, b"CONNECT 127.0.0.1:9 HTTP/1.1\r\n\r\n").startswith(b"HTTP/1.1 403 ")
        finally:
            for one in idle:
                one.close()


CALLER = """import socket, sys
s = socket.create_connection(('127.0.0.1', int(sys.argv[1])), 10)
s.sendall(('CONNECT beyond.example:' + sys.argv[2] + ' HTTP/1.1\\r\\n\\r\\n').encode())
print(s.recv(200).split(b'\\r\\n')[0].decode())"""


@pytest.mark.slow
def test_a_process_in_an_open_coding_box_cannot_leave_this_machine_through_a_guards_proxy(tmp_path, seatbelt):
    from poolhouse.sandbox import run as boxed
    from poolhouse.sandbox.policy import Limits as Bounds, Net, Policy
    from poolhouse.scrape import browserguard

    work = str(tmp_path.resolve())
    reads = tuple(dict.fromkeys(os.path.realpath(each) for each in (sys.prefix, sys.base_prefix)))
    python = os.path.realpath(sys.executable)
    policy = Policy("coding-probe", read=(*reads, work, os.path.realpath("/etc/ssl")), write=(work,), exec=(python, *reads),
                    net=Net.loopback(), env={"HOME": work, "TMPDIR": work, "PATH": "/usr/bin:/bin"}, strict=True, open=True,
                    limits=Bounds(wall_seconds=60))
    with Canned(SECRET) as beyond, browserguard.Guard(NAMED) as guard:
        done = boxed([python, "-I", "-B", "-c", CALLER, str(guard.port), str(beyond.port)], policy, cwd=work)
        said = done.stdout if isinstance(done.stdout, str) else done.stdout.decode()
        assert (done.returncode, said.strip()) == (0, "HTTP/1.1 407 Proxy Authentication Required"), done.stderr
        assert beyond.connections == 0


PAGE = """<!doctype html><title>outside</title><body><p>hello</p>
<img id=inline src="data:image/gif;base64,R0lGODlhAQABAIAAAP///wAAACwAAAAAAQABAAACAkQBADs=">
<img src="LOCAL/img"><img src="http://169.254.169.254/latest/meta-data/"><img src="http://localhost:PORT/by-name">
<script>
const B = 'LOCAL', W = B.replace('http', 'ws');
const to = (path, status) => (status ? '/r307' : '/r') + '?to=' + encodeURIComponent(B + path);
const worker = (tag) => URL.createObjectURL(new Blob(["new WebSocket('" + W + "/" + tag + "')"], {type: 'text/javascript'}));
const frame = () => { const f = document.createElement('iframe'); document.body.appendChild(f); return f.contentWindow; };
const tries = [
  () => new WebSocket(location.origin.replace('http', 'ws') + '/control-socket'),
  () => fetch(B + '/fetch', {mode: 'no-cors'}),
  () => fetch(to('/redirect-fetch'), {mode: 'no-cors'}),
  () => { new Image().src = to('/redirect-img'); },
  () => fetch(to('/redirect-post', 307), {method: 'POST', mode: 'no-cors', body: 'chosen=by-page'}),
  () => new WebSocket(W + '/socket'),
  () => new WebSocketStream(W + '/socket-stream').opened.catch(() => {}),
  () => new (frame().WebSocket)(W + '/frame-socket'),
  () => new (frame().WebSocketStream)(W + '/frame-socket-stream').opened.catch(() => {}),
  () => new (window.open().WebSocket)(W + '/popup-socket'),
  () => new (window.open().WebSocketStream)(W + '/popup-socket-stream').opened.catch(() => {}),
  () => new Worker(worker('worker')),
  () => new SharedWorker(worker('shared-worker')),
  () => navigator.sendBeacon(B + '/beacon', 'x'),
  () => navigator.serviceWorker.register('/sw.js'),
  () => { for (const rel of ['preconnect', 'prefetch']) {
            const l = document.createElement('link'); l.rel = rel; l.href = B + '/link-' + rel; document.head.appendChild(l); } },
];
for (const one of tries) { try { const r = one(); if (r && r.catch) r.catch(() => {}); } catch (e) {} }
</script></body>"""

FORM = """(action) => { const f = document.createElement('form'); f.method = 'post'; f.action = action;
  const i = document.createElement('input'); i.name = 'chosen'; i.value = 'by-page'; f.appendChild(i);
  document.body.appendChild(f); setTimeout(() => f.submit(), 50); }"""


@contextlib.contextmanager
def guarded(profile):
    """A page of the guarded window as `scrape.browser` makes one; the test is skipped where no browser is installed."""
    pytest.importorskip("playwright.sync_api")
    window = browser.Window(profile=profile, channel="chromium", guarded=True, timeout_ms=20_000)
    made = browser.browser(window)
    try:
        page = made.__enter__()
    except browser.BrowserUnavailable as exc:
        pytest.skip(f"no browser: {exc}")
    try:
        yield page
    finally:
        made.__exit__(None, None, None)


@pytest.mark.slow
def test_a_real_guarded_browser_reaches_this_machine_by_no_redirect_form_or_socket(outside, tmp_path, caplog):
    with Canned(SECRET) as local:
        outside.page = PAGE.replace("LOCAL", local.base).replace("PORT", str(local.port))
        with guarded(tmp_path / "profile") as page:
            page.goto(f"{outside.base}/page", wait_until="load")
            page.wait_for_timeout(3000)
            assert page.evaluate("document.getElementById('inline').naturalWidth") == 1, "inline content still loads"
            page.evaluate(FORM, "/r307?to=" + urllib.parse.quote(f"{local.base}/form", safe=""))
            page.wait_for_timeout(2000)
            with contextlib.suppress(Exception):   # the refusal ends the navigation in the browser's own error
                page.goto(f"{outside.base}/r?to=" + urllib.parse.quote(f"{local.base}/secret", safe=""), wait_until="load")
            page.wait_for_timeout(1000)   # its error page arrives after the failure
            shown = page.content()
        assert (local.requests, local.connections) == ([], 0)
        assert SECRET not in shown
        assert "GET /control-socket websocket" in outside.requests, "a socket to a host it may reach still connects"
        assert any(line.startswith("POST /r307") for line in outside.requests), "the redirects were followed this far"
        assert "169.254.169.254" in caplog.text and f"localhost:{local.port}" in caplog.text


@pytest.mark.slow
def test_a_web_read_that_is_redirected_to_this_machine_returns_nothing_of_it(outside, tmp_path):
    def refused(url):
        raise Refused("plumbing only: the plain fetch refuses this redirect")

    with Canned(f"<html><body>{SECRET * 20}</body></html>") as local:
        start = f"{outside.base}/r?to=" + urllib.parse.quote(f"{local.base}/secret", safe="")
        with guarded(tmp_path / "probe"):
            pass   # skips here, not inside `web.read`, where no browser is installed
        with pytest.raises(Refused, match="plain fetch refuses"):
            web.read(start, fetch=refused, browse=lambda: browser.browser(
                browser.Window(profile=tmp_path / "profile", channel="chromium", guarded=True, timeout_ms=20_000)))
        assert (local.requests, local.connections) == ([], 0)


DATAGRAMS = """async ([port, tcp]) => {
  const frame = () => { const f = document.createElement('iframe'); document.body.appendChild(f); return f.contentWindow; };
  const tried = [];
  for (const w of [window, frame(), window.open()]) {
    try { const c = new w.RTCPeerConnection({iceServers: [{urls: 'stun:127.0.0.1:' + port},
              {urls: 'turn:127.0.0.1:' + tcp + '?transport=tcp', username: 'u', credential: 'p'}]});
          c.createDataChannel('x'); await c.setLocalDescription(await c.createOffer()); tried.push('rtc'); } catch (e) {}
    try { new w.WebTransport('https://127.0.0.1:' + port + '/t').ready.catch(() => {}); tried.push('transport'); } catch (e) {}
  }
  return tried;
}"""


@pytest.mark.slow
@pytest.mark.parametrize("script", [True, False])
def test_a_real_guarded_browser_sends_no_datagram_with_or_without_the_removal(outside, tmp_path, monkeypatch, script):
    from poolhouse.scrape import browserguard

    if not script:   # the launch options alone, as for a frame the script has not reached
        monkeypatch.setattr(browserguard.Guard, "install", lambda self, context: None)
    with Canned(SECRET) as local, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.settimeout(0.2)
        with guarded(tmp_path / "profile") as page:
            page.goto(f"{outside.base}/page", wait_until="load")
            tried = page.evaluate(DATAGRAMS, [listener.getsockname()[1], local.port])
            page.wait_for_timeout(3000)
        with pytest.raises(TimeoutError):
            listener.recv(2048)
        assert local.connections == 0
    assert script or {"rtc", "transport"} <= set(tried), "without the removal the page did make both attempts"
