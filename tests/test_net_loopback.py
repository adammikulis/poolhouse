"""Loopback transports cannot become an internet client, proxy or TLS bypass."""

import socket
import ssl
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

from poolhouse import http, httpguard
from poolhouse.coordination import verify
from poolhouse.net import loopback
from poolhouse.testing.fakegithub import loopback_get


@contextmanager
def site(status=200, location="", context=None):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            seen.append((self.command, self.path, self.headers.get("Host"), self.headers.get("Authorization")))
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Length", "5")
            self.end_headers()
            self.wfile.write(b"reply")

        do_POST = do_GET

    server = http.Server(("127.0.0.1", 0), Handler)
    if context:
        server.socket = context.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        yield f"{'https' if context else 'http'}://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)
        assert not worker.is_alive()


@pytest.mark.parametrize("url", [
    "http://example.invalid/", "http://localhost/", "http://127.1/", "http://2130706433/",
    "http://127.0.0.1.example.invalid/", "http://192.168.1.1/", "http://169.254.169.254/",
    "http://[::ffff:127.0.0.1]/", "http://[::1%25lo0]/", "http://user@127.0.0.1/",
    "http://@127.0.0.1/", "http://127.0.0.1/#fragment", "http://127.0.0.1/\n",
    " http://127.0.0.1/", "file:///tmp/page", "http://\uff11\uff12\uff17.\uff10.\uff10.\uff11/",
])
def test_nonliteral_or_nonloopback_url_is_rejected_before_network(monkeypatch, url):
    def forbidden(*_args, **_kwargs):
        pytest.fail("untrusted URL reached DNS or a connection")
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(httpguard._Pinned, "connect", forbidden)
    with pytest.raises(httpguard.Refused):
        loopback.fetch(url)
    with pytest.raises(httpguard.Refused):
        loopback_get(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:1234/path?q=1", "http://[::1]:1234/path"])
def test_literal_destination_and_exact_guard_are_preserved(monkeypatch, url):
    def fetch(destination, *, method, headers, limits):
        assert destination == url and not limits.follow_redirects
        assert limits.vet(destination) is None
        with pytest.raises(httpguard.Refused):
            limits.vet(destination + "/changed")
        return httpguard.Fetched(destination, 200, {}, b"answer")
    monkeypatch.setattr(httpguard, "fetch", fetch)
    assert loopback.fetch(url).body == b"answer"


@pytest.mark.parametrize("location", ["http://example.invalid/private", "http://169.254.169.254/",
                                       "http://localhost/", "/next", "https://[::1]/"])
def test_first_redirect_is_returned_without_any_second_connection(location):
    with site(302, location) as (base, seen):
        answer = loopback.fetch(base + "/redirect")
        assert answer.status == 302 and answer.body == b"reply" and answer.redirects == ()
        assert len(seen) == 1 and seen[0][1] == "/redirect"


def test_custom_host_method_query_and_proxy_isolation(monkeypatch):
    with site() as (proxy, proxy_seen), site() as (base, seen):
        for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "ALL_PROXY"):
            monkeypatch.setenv(name, proxy)
        monkeypatch.setenv("NO_PROXY", "")
        assert loopback_get(base + "/path?q=1", host="attacker.example:1234", method="POST") == (200, b"reply")
        assert seen == [("POST", "/path?q=1", "attacker.example:1234", None)] and proxy_seen == []


def test_insecure_tls_context_is_refused_before_connection(monkeypatch):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    monkeypatch.setattr(httpguard._Pinned, "connect", lambda *_: pytest.fail("insecure TLS connected"))
    with pytest.raises(httpguard.Refused, match="certificate"):
        loopback.fetch("https://127.0.0.1/", context=context)


@pytest.mark.parametrize("wrong_san", [False, True])
def test_actual_ca_and_anonymous_refusal_without_global_pin_or_credentials(monkeypatch, tmp_path, wrong_san):
    from test_http_trust import _self_signed

    certificate, key = _self_signed(tmp_path)
    if wrong_san:
        wrong_server_name(certificate, key)
    authority = Path(certificate).read_text()
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(certificate, key)
    monkeypatch.setattr(http.serverkeys, "for_url", lambda *_: pytest.fail("ambient credential lookup"))
    with site(401, context=server_context) as (base, seen):
        port = int(base.rsplit(":", 1)[1])
        class Docker:
            def call(self, _args):
                return type("Result", (), {"stdout": authority})()
        if wrong_san:
            with pytest.raises(verify.Refused):
                verify.host_tls(Docker(), "owned", port)
            assert seen == []
            return
        verify.host_tls(Docker(), "owned", port)
        assert seen[0][3] == ""
        assert base.removeprefix("https://") not in http._PINNED
        with pytest.raises(httpguard.Refused):
            loopback.fetch(base)


def wrong_server_name(certificate, key):
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization

    original = x509.load_pem_x509_certificate(Path(certificate).read_bytes())
    private = serialization.load_pem_private_key(Path(key).read_bytes(), password=None)
    replacement = (x509.CertificateBuilder().subject_name(original.subject).issuer_name(original.issuer)
                   .public_key(original.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(original.not_valid_before_utc).not_valid_after(original.not_valid_after_utc)
                   .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.2"))]), critical=False)
                   .sign(private, hashes.SHA256()))
    Path(certificate).write_bytes(replacement.public_bytes(serialization.Encoding.PEM))


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), float("-inf"), 0, -1, 11, True, "5", None])
def test_unbounded_or_invalid_timeout_is_refused_before_network(monkeypatch, timeout):
    monkeypatch.setattr(httpguard._Pinned, "connect", lambda *_: pytest.fail("invalid timeout connected"))
    with pytest.raises(httpguard.Refused, match="timeout"):
        loopback.fetch("http://127.0.0.1/", timeout=timeout)
