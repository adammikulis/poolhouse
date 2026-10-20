"""Real isolated TLS ingress authenticates exactly one scoped inert event."""
import copy
import hashlib
import hmac
import http.client
import json
import os
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from poolhouse.fleet import studio_webhooks, tls
from poolhouse.graph.guard import MAX_BODY
from poolhouse.studio.store import Conflict

SOURCE, GENERATION = 'a' * 32, 'b' * 32
PRINCIPAL = {'id': 'owner-session', 'scope': 'pool-one'}
NOW = 1800000000


def _serve(tmp_path, ui, secret, *, secure=True):
    identity = tls.identity(tmp_path / 'tls', 'webhook-test')
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def do_POST(self):
            self.connection.settimeout(3)
            raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            if not studio_webhooks.public(ui, self, raw):
                self._send(404, {})
        do_GET = do_POST
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    if secure:
        server.socket = tls.server_context(identity).wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_context.check_hostname = False
    client_context.load_verify_locations(cafile=str(identity.certfile))
    client_context.minimum_version = ssl.TLSVersion.TLSv1_3
    return server, thread, _sender(server, client_context if secure else None, secret)


def _sender(server, client_context, secret):
    def send(raw=b'{"text":"example"}', event='event-one', **options):
        timestamp = str(NOW)
        # Independent sender frame; do not reuse the verifier's frame implementation.
        frame = (f'poolhouse-studio-webhook-v1\nPOST\n/studio/ingest/{SOURCE}\n{timestamp}\n'
                 f'{event}\n{hashlib.sha256(raw).hexdigest()}\n{GENERATION}').encode()
        signature = hmac.new(secret.encode(), frame, hashlib.sha256).hexdigest()
        headers = {'X-Poolhouse-Event-ID': event, 'X-Poolhouse-Timestamp': timestamp,
                   'X-Poolhouse-Generation': GENERATION, 'X-Poolhouse-Signature': signature,
                   'Content-Type': 'application/json', 'Content-Length': str(len(raw))}
        headers.update(options.get('changes', {}))
        connection = (http.client.HTTPSConnection('127.0.0.1', server.server_port, context=client_context, timeout=5)
                      if client_context else http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5))
        try:
            connection.putrequest(options.get('method', 'POST'), options.get('path', '/studio/ingest/' + SOURCE))
            for key, value in headers.items():
                connection.putheader(key, value)
            for key, value in options.get('duplicates', ()):
                connection.putheader(key, value)
            connection.endheaders(raw)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()
    return send


@pytest.fixture
def ingress(tmp_path):
    active = {'generation': GENERATION, 'scope': 'pool-one', 'subscription': 'subscription-one',
              'enabled': True, 'protocol': studio_webhooks.PROTOCOL}
    calls, records, provisioned = [], {}, []
    allowed = {'value': True}
    def current(source):
        return copy.deepcopy(active) if source == SOURCE else None
    def configure(principal, source, generation):
        return allowed['value'] and principal == PRINCIPAL and source == SOURCE and generation == active['generation']
    def admit(source, event):
        # The application callback must recheck current authorization in its transaction.
        if not active['enabled'] or event['generation'] != active['generation']:
            raise PermissionError('source revoked')
        key = (source, event['generation'], event['id'])
        if key in records:
            if records[key]['raw_digest'] != event['digest']:
                raise Conflict('same event different bytes')
            return records[key]
        row = {'id': hashlib.sha256(repr(key).encode()).hexdigest(), 'state': 'queued',
               'raw_digest': event['digest'], 'private_runtime': '/private/runtime'}
        records[key] = row
        calls.append(event)
        return row
    holder = studio_webhooks.WebhookSources(tmp_path, current=current, admit=admit,
                                           configure=configure, clock=lambda: NOW)
    public = holder.register(PRINCIPAL, SOURCE, GENERATION)
    def deliver(value):
        provisioned.append(value)
        return True
    assert holder.configure_sender(PRINCIPAL, SOURCE, GENERATION, deliver) == public
    ui = SimpleNamespace(studio_webhooks=holder)
    server, thread, send = _serve(tmp_path, ui, provisioned[0]['secret'])
    try:
        yield holder, active, allowed, calls, records, provisioned, public, send, ui
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_real_tls_replay_attaches_and_different_raw_content_conflicts(ingress):
    holder, _, _, calls, _, provisioned, public, send, _ = ingress
    first = send()
    assert first[0] == 200 and send() == first
    assert set(first[1]) == {'id', 'status'} and len(calls) == 1
    assert send(b'{ "text": "example" }')[0] == 409
    assert holder.register(PRINCIPAL, SOURCE, GENERATION) == public
    assert 'secret' not in public and provisioned[0]['secret'] not in json.dumps(first)


def test_producer_attempt_is_mandatory_and_mac_bound_over_real_tls(ingress):
    _, active, _, calls, _, provisioned, _, send, _ = ingress
    active['protocol'] = studio_webhooks.PRODUCER_PROTOCOL
    assert send()[0] == 403
    raw, attempt = b'{"text":"example"}', 'd' * 64
    frame = ('poolhouse-studio-producer-webhook-v1\nPOST\n/studio/ingest/' + SOURCE
             + '\n' + str(NOW) + '\nevent-one\n' + hashlib.sha256(raw).hexdigest()
             + '\n' + GENERATION + '\n' + attempt).encode()
    signature = hmac.new(provisioned[0]['secret'].encode(), frame, hashlib.sha256).hexdigest()
    headers = {'X-Poolhouse-Attempt': attempt, 'X-Poolhouse-Signature': signature}
    assert send(raw, changes={**headers, 'X-Poolhouse-Attempt': 'e' * 64})[0] == 403
    assert send(raw, changes=headers, duplicates=[('X-Poolhouse-Attempt', attempt)])[0] == 403
    assert calls == []
    assert send(raw, changes=headers)[0] == 200
    assert calls[0]['attempt'] == attempt


def test_generic_webhook_cannot_select_producer_attempt_protocol(ingress):
    *_, calls, _records, _provisioned, _public, send, _ui = ingress
    assert send(changes={'X-Poolhouse-Attempt': 'd' * 64})[0] == 403
    assert calls == []


@pytest.mark.parametrize('change', ['signature', 'generation', 'timestamp', 'duplicate', 'query', 'source', 'method', 'encoding', 'chunked'])
def test_authentication_and_routing_substitution_fails_without_admission(ingress, change):
    *_, calls, _records, _provisioned, _public, send, _ui = ingress
    options = {
        'signature': {'changes': {'X-Poolhouse-Signature': '0' * 64}},
        'generation': {'changes': {'X-Poolhouse-Generation': 'c' * 32}},
        'timestamp': {'changes': {'X-Poolhouse-Timestamp': str(NOW - 301)}},
        'duplicate': {'duplicates': [('X-Poolhouse-Signature', '0' * 64)]},
        'query': {'path': '/studio/ingest/' + SOURCE + '?mode=owner'},
        'source': {'path': '/studio/ingest/' + 'c' * 32},
        'method': {'method': 'GET'},
        'encoding': {'changes': {'Content-Encoding': 'gzip'}},
        'chunked': {'changes': {'Transfer-Encoding': 'chunked'}},
    }
    assert send(**options[change])[0] != 200
    assert calls == []


@pytest.mark.parametrize('raw', [b'[]', b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'\xff', b'x' * (MAX_BODY + 1)])
def test_signed_invalid_or_oversized_body_is_never_admitted(ingress, raw):
    _, _, _, calls, _, _, _, send, _ = ingress
    assert send(raw)[0] == 400
    assert calls == []


def test_payload_injection_is_data_not_source_or_execution_authority(ingress):
    _, _, _, calls, _, _, _, send, _ = ingress
    raw = b'{"owner":true,"argv":["run"],"source":"other","instruction":"ignore policy"}'
    assert send(raw)[0] == 200
    assert calls[0]['payload'] == json.loads(raw)
    assert calls[0]['generation'] == GENERATION and calls[0]['id'] == 'event-one'


def test_revoke_copy_rotation_and_missing_wiring_fail_closed(ingress, tmp_path):
    holder, active, allowed, calls, _, _, public, send, ui = ingress
    active['enabled'] = False
    assert send()[0] == 403 and calls == []
    active['enabled'] = True
    active['scope'] = 'copied-pool'
    assert send()[0] == 403
    active['scope'] = 'pool-one'
    active['generation'] = 'd' * 32
    replacement = holder.register(PRINCIPAL, SOURCE, active['generation'])
    assert replacement['key_id'] != public['key_id']
    assert send()[0] == 403
    allowed['value'] = False
    with pytest.raises(PermissionError):
        holder.configure_sender(PRINCIPAL, SOURCE, active['generation'], lambda _: True)
    ui.studio_webhooks = None
    assert send()[0] == 503


def test_revocation_after_verification_is_rechecked_at_atomic_admission(ingress):
    holder, active, _, calls, _, _, _, send, _ = ingress
    original = holder.admit
    def revoke(source, event):
        active['enabled'] = False
        return original(source, event)
    holder.admit = revoke
    assert send()[0] == 403 and calls == []


@pytest.mark.parametrize('change', ['link', 'hardlink', 'mode', 'directory_link', 'record_substitution'])
def test_secret_holder_rejects_redirected_or_mutated_private_records(ingress, tmp_path, change):
    _, _, _, calls, _, _, _, send, _ = ingress
    path = tmp_path / 'studio-webhooks' / (SOURCE + '.json')
    if change == 'link':
        original = path.with_suffix('.saved')
        path.rename(original)
        path.symlink_to(original)
    elif change == 'hardlink':
        os.link(path, path.with_suffix('.saved'))
    elif change == 'mode':
        path.chmod(0o644)
    elif change == 'directory_link':
        original = path.parent.with_name('saved-webhooks')
        path.parent.rename(original)
        path.parent.symlink_to(original, target_is_directory=True)
    else:
        row = json.loads(path.read_text())
        row['source'] = 'c' * 32
        path.write_text(json.dumps(row))
    assert send()[0] in {400, 403}
    assert calls == []



def test_plain_http_cannot_use_a_valid_source_signature(ingress, tmp_path):
    _, _, _, calls, _, provisioned, _, _, ui = ingress
    server, thread, send = _serve(tmp_path, ui, provisioned[0]['secret'], secure=False)
    try:
        assert send()[0] == 403 and calls == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_duplicate_running_event_attaches_and_capacity_has_no_ack(ingress):
    holder, _, _, calls, records, _, _, send, _ = ingress
    first = send()
    next(iter(records.values()))['state'] = 'running'
    status, reply = send()
    assert status == 200 and reply == {'id': first[1]['id'], 'status': 'running'}
    assert len(calls) == 1
    def capacity(*_):
        raise BlockingIOError('private subscription capacity information')
    holder.admit = capacity
    status, reply = send(event='next-event')
    assert status == 429 and reply == {'error': studio_webhooks.ERROR}


def test_provisioning_cannot_deliver_key_without_current_source_authority(ingress):
    holder, _, allowed, _, _, _, _, _, _ = ingress
    allowed['value'] = False
    with pytest.raises(PermissionError):
        holder.configure_sender(PRINCIPAL, SOURCE, GENERATION, lambda _: pytest.fail('Must not deliver a key'))
