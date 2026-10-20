"""The URL policy guard: which addresses `poolhouse.http.check` will fetch.

DNS is faked throughout, so nothing here resolves a real host.
"""

from __future__ import annotations

import pytest

from poolhouse.http import Refused, Server, check


@pytest.fixture
def public_dns(monkeypatch):
    """Every invented host resolves somewhere public, except *.internal, a LAN."""
    def addresses(host):
        return ["10.0.0.5"] if host.endswith(".internal") else ["1.2.3.4"]
    monkeypatch.setattr("poolhouse.http._addresses", addresses)


def test_a_public_url_passes_the_check(public_dns):
    assert check("https://quenlow.example/about?x=1") == "https://quenlow.example/about?x=1"


@pytest.mark.parametrize("url", [
    "file:///etc/hosts",
    "ftp://quenlow.example/",
    "/just/a/path",
    "http://localhost/admin",
    "http://printer.local/",
    "http://127.0.0.1:8080/",
    "http://[::1]/",
    "http://10.1.2.3/",
    "http://192.168.1.1/",
    "http://172.16.0.9/",
    "http://169.254.169.254/latest/meta-data/",
    "http://intranet.internal/",
])
def test_a_url_on_this_side_of_the_router_is_refused(public_dns, url):
    with pytest.raises(Refused):
        check(url)


def test_a_host_dns_cannot_resolve_is_refused(monkeypatch):
    import socket

    def gone(host, port):
        raise socket.gaierror("not found")
    monkeypatch.setattr("poolhouse.http.socket.getaddrinfo", gone)
    with pytest.raises(Refused, match="cannot resolve"):
        check("https://nowhere.example/")


def test_a_server_binds_without_a_reverse_lookup_and_answers(monkeypatch):
    import socket
    import threading
    import urllib.request
    from http.server import BaseHTTPRequestHandler

    def lookup(*_a):
        raise AssertionError("the bind looked its own address up")

    class Hello(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"hello")

        def log_message(self, *_a):
            pass

    monkeypatch.setattr(socket, "getfqdn", lookup)
    server = Server(("127.0.0.1", 0), Hello)
    try:
        assert server.server_name == "127.0.0.1"
        assert server.server_port == server.server_address[1] != 0
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=5) as r:
            assert r.read() == b"hello"
    finally:
        server.shutdown()
        server.server_close()


def test_a_message_about_a_url_carries_no_password_and_no_secret_parameter():
    from poolhouse.http import shown

    got = shown("https://user:pw@example.com:8443/v1/x?token=abc&model=m&api_key=k&x=1#frag")
    assert "abc" not in got and "pw" not in got and "user" not in got and "api_key=k" not in got
    assert "model=m" in got and "x=1" in got and got.startswith("https://example.com:8443/v1/x")


def test_an_unreachable_url_is_reported_without_its_secrets():
    from poolhouse.http import Options, ServerUnreachable, request_json

    with pytest.raises(ServerUnreachable) as caught:
        request_json("http://127.0.0.1:9/x?access_token=SUPERSECRET&a=1", options=Options(timeout=1))
    assert "SUPERSECRET" not in str(caught.value) and "a=1" in str(caught.value)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "data:text/plain,hi",
                                 "jar:file:///x!/y", "/etc/passwd"])
def test_only_http_and_https_are_opened(url):
    from poolhouse.http import ServerError, open_stream, request_bytes

    with pytest.raises(ServerError, match="only http"):
        open_stream(url)
    with pytest.raises(ServerError, match="only http"):
        request_bytes(url)


@pytest.mark.parametrize(('host', 'direct'), [('127.0.0.1', True), ('127.9.8.7', True),
    ('[::1]', True), ('localhost', False), ('127.0.0.1.example', False),
    ('192.0.2.1', False), ('127.1', False)])
@pytest.mark.parametrize('cancelled', [False, True])
def test_guarded_numeric_loopback_has_no_ambient_proxy(monkeypatch, host, direct, cancelled):
    import urllib.request
    from types import SimpleNamespace

    from poolhouse import http

    seen, guarded = [], []
    control = object() if cancelled else None
    monkeypatch.setattr(http.http_cancel, 'active', lambda: control)
    monkeypatch.setattr(http.http_cancel, 'handlers', lambda *_: [])
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *handlers:
                        SimpleNamespace(open=lambda *_args, **_kwargs: seen.extend(handlers)))
    url = f'https://{host}:16443/status'
    http._open(http.build_request(url), 1, lambda value: guarded.append(value) or value)
    proxy = [handler for handler in seen if isinstance(handler, urllib.request.ProxyHandler)]
    assert bool(proxy) is direct
    assert not proxy or proxy[0].proxies == {}
    assert guarded == ([url] if direct else [])


def test_unguarded_loopback_keeps_the_default_opener(monkeypatch):
    import urllib.request

    from poolhouse import http

    seen = []
    monkeypatch.setattr(urllib.request, 'urlopen', lambda request, **kwargs: seen.append(request.full_url))
    http._open(urllib.request.Request('http://127.0.0.1/status'), 1, None)
    assert seen == ['http://127.0.0.1/status']


def test_loopback_initial_guard_refusal_precedes_networking(monkeypatch):
    import urllib.request

    from poolhouse import http

    def network(*_args, **_kwargs):
        pytest.fail('a rejected destination reached the opener')
    def refuse(_url):
        raise Refused('destination changed')
    monkeypatch.setattr(urllib.request, 'build_opener', network)
    with pytest.raises(Refused, match='destination changed'):
        http._open(urllib.request.Request('http://127.0.0.1/status'), 1, refuse)


def test_guarded_loopback_reaches_local_server_without_ambient_proxy(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler

    from poolhouse import http

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'local')
        def log_message(self, *_args):
            pass
    server = Server(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv('http_proxy', 'http://127.0.0.1:9')
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:9')
    monkeypatch.setenv('no_proxy', '')
    monkeypatch.setenv('NO_PROXY', '')
    url = f'http://127.0.0.1:{server.server_port}/status'
    try:
        assert http.request_bytes(url, options=http.Options(timeout=1, guard=lambda value: value)).body == b'local'
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_loopback_direct_opener_still_refuses_a_remote_redirect():
    from poolhouse import http

    url = 'http://127.0.0.1/status'
    def exact(value):
        if value != url:
            raise Refused('destination changed')
        return value
    with pytest.raises(Refused, match='destination changed'):
        http._Guarded(exact).redirect_request(http.build_request(url), None, 302,
                                             'Found', {}, 'http://192.0.2.1/status')


def test_request_options_snapshot_headers_and_hide_secrets():
    from dataclasses import FrozenInstanceError
    from typing import Any, cast

    from poolhouse.http import Options

    headers = {'Authorization': 'Bearer private-header'}
    options = Options(headers=headers, token='private-token')
    headers['Authorization'] = 'replacement'
    assert options.headers['Authorization'] == 'Bearer private-header'
    with pytest.raises(TypeError):
        cast(Any, options.headers)['Authorization'] = 'replacement'
    with pytest.raises(FrozenInstanceError):
        cast(Any, options).timeout = 1
    assert 'private-header' not in repr(options) and 'private-token' not in repr(options)


@pytest.mark.parametrize('entry', ['open_stream', 'request_bytes', 'request_json'])
def test_request_entry_default_delivery_options(entry):
    import inspect

    from poolhouse import http

    default = inspect.signature(getattr(http, entry)).parameters['options'].default
    assert default.timeout == 180.0 and default.token == '' and dict(default.headers) == {}
    assert default.guard is None
    assert default.retry == http.Retry(tries=1, backoff=0.5, when_unreachable=True)


def test_json_options_preserve_authenticated_headers_payload_and_retry(monkeypatch):
    from poolhouse import http

    seen = []
    def reply(url, *, data, method, options):
        seen.append((url, data, method, options))
        return http.Reply(200, b'{"accepted":true}', {})
    monkeypatch.setattr(http, 'request_bytes', reply)
    def guard(value):
        return value
    options = http.Options(headers={'Authorization': 'Bearer explicit', 'X-Check': 'yes'},
                           token='token-value', timeout=None,
                           retry=http.Retry(tries=3, backoff=0.25), guard=guard)
    assert http.request_json('http://127.0.0.1/request', payload={'value': 1},
                             method='PUT', options=options) == {'accepted': True}
    url, body, method, forwarded = seen[0]
    assert url == 'http://127.0.0.1/request' and body == b'{"value": 1}' and method == 'PUT'
    assert forwarded.token == options.token and forwarded.timeout is None
    assert forwarded.retry == options.retry and forwarded.guard is guard
    assert dict(forwarded.headers) == {'Content-Type': 'application/json', **options.headers}
    assert 'Content-Type' not in options.headers


@pytest.mark.parametrize("limit", [-1, True, 1.5, float("inf"), "4"])
def test_invalid_http_error_body_limits_are_refused(limit):
    from poolhouse.http import Options

    with pytest.raises(ValueError, match="nonnegative integer"):
        Options(error_body_limit=limit)


def test_oversized_http_error_stops_before_decryption_or_retry(monkeypatch):
    import io
    import urllib.error

    from poolhouse import http

    calls, reads = [], []

    class Body(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    def opened(*args):
        calls.append(args)
        raise urllib.error.HTTPError("http://127.0.0.1:9/x", 500, "error", {}, Body(b"secret-body"))

    def decrypt(*args):
        raise AssertionError("oversized error reached decryption")

    monkeypatch.setattr(http, "_open", opened)
    monkeypatch.setattr(http.Sealed, "open", decrypt)
    with pytest.raises(http.ServerError, match="exceeds the byte limit") as caught:
        http.open_stream("http://127.0.0.1:9/x", options=http.Options(
            error_body_limit=4, retry=http.Retry(tries=3)))
    assert len(calls) == 1 and reads == [5]
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("limit", [None, 20])
def test_bounded_and_default_http_errors_preserve_status_and_detail(server, limit):
    from poolhouse import http

    url = server(lambda *_: (422, b"small detail")).base_url
    with pytest.raises(http.ServerError) as caught:
        http.open_stream(url, options=http.Options(error_body_limit=limit))
    assert caught.value.status == 422 and caught.value.body == "small detail"


def test_http_error_limit_preserves_positional_guard():
    from poolhouse.http import ONCE, Options

    def guard(url):
        return url

    options = Options({}, "", 1, ONCE, guard)
    assert options.guard is guard and options.error_body_limit is None


@pytest.mark.parametrize('cancelled', [False, True])
@pytest.mark.parametrize('explicit', [False, True])
def test_request_tls_context_overrides_pin_for_normal_and_cancelled_open(monkeypatch, cancelled, explicit):
    import ssl
    import urllib.request
    from types import SimpleNamespace

    from poolhouse import http

    selected = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    global_pin = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    url = 'https://127.0.0.1:18443/request-context'
    observed, cancelled_contexts = [], []
    original_handlers = http.http_cancel.handlers
    def handlers(control, context):
        cancelled_contexts.append(context)
        return original_handlers(control, context)
    def opener(*configured):
        observed.extend(handler._context for handler in configured if isinstance(handler, urllib.request.HTTPSHandler))
        return SimpleNamespace(open=lambda *args, **kwargs: SimpleNamespace(headers={}, status=200))
    monkeypatch.setattr(urllib.request, 'build_opener', opener)
    monkeypatch.setattr(http.http_cancel, 'handlers', handlers)
    options = http.Options(tls_context=selected if explicit else None, guard=lambda value: value)
    with http.http_cancel.scope(http.http_cancel.Cancellation() if cancelled else None):
        http.pin('127.0.0.1:18443', global_pin)
        try:
            http.open_stream(url, options=options)
        finally:
            http.pin('127.0.0.1:18443', None)
    expected = selected if explicit else global_pin
    assert observed == [expected]
    assert cancelled_contexts == ([expected] if cancelled else [])
    assert 'tls_context' not in repr(options)


def test_request_tls_context_cannot_be_serialized_configuration():
    from poolhouse import http

    with pytest.raises(TypeError, match='SSLContext'):
        http.Options(tls_context={'certificate': 'not a trusted software object'})
