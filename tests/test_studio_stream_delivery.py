"""Delivery cannot redirect, retry an uncertain POST, or widen registered authority."""
import hashlib
import io
import json
import socket
import ssl
from types import SimpleNamespace

import pytest

from poolhouse import httpguard
from poolhouse.graph.guard import MAX_BODY
from poolhouse.studio.stream_delivery import deliver


@pytest.fixture
def delivery(monkeypatch):
    destination = {'id': 'registered-hook', 'revision': 1, 'url': 'https://receiver.example/records'}
    body = {'record': {'policy': 'untrusted payload', 'amount': 12}}
    raw = json.dumps(body, sort_keys=True, separators=(',', ':')).encode()
    envelope = {'delivery_id': 'delivery-1', 'request_digest': hashlib.sha256(raw).hexdigest(), 'body': body}
    state = SimpleNamespace(status=200, response=json.dumps({key: envelope[key] for key in ('delivery_id', 'request_digest')}).encode(),
                            addresses=['93.184.216.34', '1.1.1.1'], connections=[], requests=[], current=True, failure=None)
    def dns(host, port, **_kwargs):
        assert host == 'receiver.example' and port == 443
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, port)) for address in state.addresses]
    class Connection:
        def __init__(self, host, port, address, timeout, context):
            assert host == 'receiver.example' and port == 443
            assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
            state.connections.append(address)
        def request(self, method, target, body, headers):
            state.requests.append((method, target, body, headers))
            if state.failure:
                raise state.failure
        def getresponse(self):
            return Response()
        def close(self):
            pass
    class Response:
        status = property(lambda self: state.status)
        def __init__(self):
            self.stream = io.BytesIO(state.response)
        def read1(self, n):
            return self.stream.read(n)
        def getheader(self, key):
            return {'Content-Length': str(len(state.response)), 'Location': 'http://169.254.169.254/secrets'}.get(key)
        def getheaders(self):
            return []
    monkeypatch.setattr(socket, 'getaddrinfo', dns)
    monkeypatch.setattr(httpguard, '_Pinned', Connection)
    return destination, envelope, state


def test_exact_receipt_single_pinned_attempt_no_ambient_credentials_or_proxy(delivery, monkeypatch):
    destination, envelope, state = delivery
    for name in ('HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'HF_TOKEN', 'OPENAI_API_KEY'):
        monkeypatch.setenv(name, 'must-not-be-used')
    result = deliver(destination, envelope, authorize=lambda: state.current)
    assert result['state'] == 'done' and result['receipt']['delivery_id'] == 'delivery-1'
    assert state.connections == ['93.184.216.34']
    method, path, body, headers = state.requests[0]
    assert method == 'POST' and path == '/records' and json.loads(body) == envelope['body']
    assert headers['Idempotency-Key'] == envelope['delivery_id']
    assert 'Authorization' not in headers and 'Cookie' not in headers and 'Host' not in headers
    assert 'must-not-be-used' not in str(headers)


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '::ffff:127.0.0.1'])
def test_any_private_dns_answer_refuses_even_with_ambient_allowlist(delivery, monkeypatch, address):
    destination, envelope, state = delivery
    monkeypatch.setenv(httpguard.ALLOW_ENV, 'receiver.example')
    state.addresses.append(address)
    assert deliver(destination, envelope, authorize=lambda: True)['state'] == 'blocked'
    assert not state.connections


@pytest.mark.parametrize('status', [301, 302, 307, 400, 401, 429, 500])
def test_non2xx_is_uncertain_and_redirect_is_never_followed(delivery, status):
    destination, envelope, state = delivery
    state.status = status
    assert deliver(destination, envelope, authorize=lambda: True)['state'] == 'interrupted'
    assert len(state.requests) == len(state.connections) == 1


def test_possible_send_failure_never_tries_second_dns_address(delivery):
    destination, envelope, state = delivery
    state.failure = OSError('possibly committed; private error')
    result = deliver(destination, envelope, authorize=lambda: True)
    assert result == {'state': 'interrupted', 'reason': 'delivery_uncertain'}
    assert len(state.requests) == len(state.connections) == 1


@pytest.mark.parametrize('response', [b'not json', b'{}', b'{"delivery_id":"other"}', b'[]', b'"secret"'])
def test_forged_or_malformed_receipt_does_not_complete(delivery, response):
    destination, envelope, state = delivery
    state.response = response
    assert deliver(destination, envelope, authorize=lambda: True)['state'] == 'interrupted'


def test_oversize_response_is_uncertain(delivery):
    destination, envelope, state = delivery
    state.response = b'x' * (MAX_BODY + 1)
    assert deliver(destination, envelope, authorize=lambda: True)['state'] == 'interrupted'
    assert len(state.requests) == 1


@pytest.mark.parametrize('url', ['http://receiver.example/x', 'https://user:secret@receiver.example/x',
                                 'https://receiver.example/x#other', ' https://receiver.example/x',
                                 'https://receiver.example/x\n', 'file:///tmp/private'])
def test_invalid_registered_destination_never_sends(delivery, url):
    destination, envelope, state = delivery
    destination['url'] = url
    assert deliver(destination, envelope, authorize=lambda: True)['state'] == 'blocked'
    assert not state.connections


def test_changed_body_payload_headers_and_false_authorization_never_send(delivery):
    destination, envelope, state = delivery
    envelope['body']['record']['amount'] = 13
    assert deliver(destination, envelope, authorize=lambda: True)['state'] == 'blocked'
    destination['headers'] = {'Authorization': 'forged'}
    assert deliver(destination, envelope, authorize=lambda: True)['state'] == 'blocked'
    assert not state.connections


def test_revocation_before_and_after_possible_send(delivery):
    destination, envelope, state = delivery
    assert deliver(destination, envelope, authorize=lambda: False)['state'] == 'blocked'
    assert not state.connections
    def authorize():
        return not state.requests
    assert deliver(destination, envelope, authorize=authorize)['state'] == 'interrupted'
    assert len(state.requests) == 1


def test_maintained_socket_connects_pinned_ip_with_original_tls_sni_and_host(monkeypatch):
    destination = {'id': 'fixed-hook', 'revision': 2, 'url': 'https://receiver.example/events'}
    envelope = {'delivery_id': 'delivery-2', 'request_digest': hashlib.sha256(b'{}').hexdigest(), 'body': {}}
    receipt = json.dumps({key: envelope[key] for key in ('delivery_id', 'request_digest')}).encode()
    seen = {'connect': [], 'sni': [], 'sent': []}
    class Socket:
        def sendall(self, data):
            seen['sent'].append(data)
        def makefile(self, mode):
            return io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: ' + str(len(receipt)).encode() + b'\r\n\r\n' + receipt)
        def close(self):
            pass
    sock = Socket()
    def connect(address, timeout):
        seen['connect'].append(address)
        return sock
    def wrap(context, connection, *, server_hostname):
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        assert connection is sock
        seen['sni'].append(server_hostname)
        return sock
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *_args, **_kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))])
    monkeypatch.setattr(socket, 'create_connection', connect)
    monkeypatch.setattr(ssl.SSLContext, 'wrap_socket', wrap)
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    assert deliver(destination, envelope, authorize=lambda: True)['state'] == 'done'
    assert seen['connect'] == [('93.184.216.34', 443)]
    assert seen['sni'] == ['receiver.example']
    assert b'Host: receiver.example:443\r\n' in b''.join(seen['sent'])


@pytest.mark.parametrize('key', ['delivery_id', 'request_digest'])
def test_ambiguous_duplicate_receipt_fields_remain_uncertain(delivery, key):
    destination, envelope, state = delivery
    values = {name: envelope[name] for name in ('delivery_id', 'request_digest')}
    state.response = ('{"' + key + '":"conflicting",' + json.dumps(values)[1:]).encode()
    assert deliver(destination, envelope, authorize=lambda: True)['state'] == 'interrupted'
    assert len(state.requests) == 1
