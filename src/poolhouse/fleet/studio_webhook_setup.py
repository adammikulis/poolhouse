"""Fixed software-owned synthetic sender; no browser or package receives a credential."""
from __future__ import annotations

import hashlib
import hmac
import json
import ssl
import time
from http.client import IncompleteRead
from urllib.parse import urlsplit

from poolhouse import http
from poolhouse.http import ONCE, Options, ServerError
from poolhouse.studio.decision_templates import example_record, sample_sets
from poolhouse.studio.store import Conflict, digest

from . import studio_epoch, tls
from .studio_webhooks import HEADERS, PREFIX, _frame

PROVIDER = 'local-synthetic'
UNKNOWN = 'This test webhook has an unresolved outcome. It will not be sent again.'


def samples():
    return [{'id': group['id'] + ':' + sample['id'], 'label': group['label'] + ' · ' + sample['label']}
            for group in sample_sets() for sample in group['samples']]


class _OwnContext(ssl.SSLContext):
    expected_der: bytes = b''

    def wrap_socket(self, *args, **kwargs):
        connection = super().wrap_socket(*args, **kwargs)
        if not self.expected_der or not hmac.compare_digest(connection.getpeercert(binary_form=True), self.expected_der):
            connection.close()
            raise ssl.SSLError('The local webhook certificate changed.')
        return connection


class LocalSender:
    """An immutable registry binding permits only one fixed local HTTPS destination."""

    def __init__(self, ui, service):
        self.ui, self.service, self.holder = ui, service, ui.studio_webhooks
        self.holder.provider_current = self.current
        service.webhook_setup = self

    def endpoint(self):
        context = self.service.authority._context()
        identity = getattr(self.ui, 'studio_identity', lambda: None)()
        endpoint = getattr(self.ui, 'studio_endpoint', lambda: None)()
        target = urlsplit(endpoint or '')
        if (not identity or identity.fingerprint != context['device'] or target.scheme != 'https'
                or target.hostname != '127.0.0.1' or not target.port or target.path or target.query
                or target.fragment or target.username or target.password):
            raise PermissionError('The built-in sender needs this existing secure app listener.')
        tls.server_context(identity)
        return context, identity, target.port

    def current(self, source, generation):
        context, _, _ = self.endpoint()
        record = self.service.store.graph.get_doc('studio.webhook-provider:' + source)
        return bool(record and record == {'source': source, 'generation': generation, 'provider': PROVIDER,
                    'scope': context['scope'], 'pool': context['pool'], 'device': context['device'],
                    'epoch': studio_epoch.generation(self.service.authority.root, context['pool'])})

    def ready(self, source, generation):
        self.holder.provision_registered(source, generation, self._proof,
                                        scope=self.service.authority._context()['scope'])
        return True

    def providers(self):
        try:
            self.endpoint()
            available, requirements = True, []
        except (ValueError, OSError, PermissionError):
            available, requirements = False, ['This app needs its existing secure listener.']
        return [{'id': PROVIDER, 'label': 'Built-in test sender (local synthetic)',
                 'available': available, 'requirements': requirements}]

    def describe(self, row):
        if row.get('provider') != PROVIDER:
            return row
        try:
            available = self.ready(row['id'], row['generation'])
        except (ValueError, OSError, PermissionError):
            available = False
        return {**row, 'available': available, 'test_supported': available, 'samples': samples(),
                'default_sample_id': samples()[0]['id'],
                'requirements': [] if available else ['The configured software sender is unavailable.']}

    def register(self, principal, body):
        service = self.service
        service.authority._owner(principal)
        if (set(body) != {'kind', 'provider', 'label', 'idempotency_key'} or body['kind'] != 'webhook'
                or body['provider'] != PROVIDER or not isinstance(body['label'], str) or not body['label'].strip()):
            raise ValueError('Choose a registered software sender and name the source.')
        epoch = service.authority.selection_epoch(principal, create=True)
        context, _, _ = self.endpoint()
        generation = digest({'scope': principal['scope'], 'principal': principal['id'], 'key': body['idempotency_key'],
                             'kind': 'webhook', 'provider': PROVIDER})[:32]
        with service.store.lock, studio_epoch.changing(service.authority.root) as (_, epochs), service.store.graph.transaction():
            actual = service.authority._owner(principal)
            if actual != context or epochs['pools'][context['pool']]['epoch'] != epoch:
                raise PermissionError('Source selection was revoked.')
            row = service.store.create('source', principal, {'kind': 'webhook', 'label': body['label'],
                'provider': PROVIDER, 'selection_epoch': epoch, 'generation': generation,
                'available': True, 'formats': ['json'], 'requirements': []}, body['idempotency_key'])
            key = 'studio.webhook-provider:' + row['id']
            capability = {'source': row['id'], 'generation': generation, 'provider': PROVIDER,
                          'scope': context['scope'], 'pool': context['pool'], 'device': context['device'], 'epoch': epoch}
            prior = service.store.graph.get_doc(key)
            if prior and prior != capability:
                raise Conflict('This source registration belongs to another authority generation.')
            service.store.graph.put_doc(key, capability)
            self.holder.register(principal, row['id'], generation)
            self.holder.provision_registered(row['id'], generation, self._proof, scope=context['scope'])
            if service.authority._owner(principal) != context:
                raise PermissionError('Source authority changed.')
            return self.describe(row)

    def _proof(self, credential):
        return (set(credential) == {'source', 'generation', 'secret'} and bool(credential['secret'])
                and self.current(credential['source'], credential['generation']))

    def active(self, principal, sub):
        self.service.authority._owner(principal)
        sub = self.service.store.get('subscription', sub['id'], principal['scope'])
        row = self.service.store.get('source', sub['source_id'], principal['scope'])
        if (not self.ready(row['id'], row['generation']) or sub['state'] != 'active'
                or not self.service.authorized(sub, sub['service_principal'])):
            raise PermissionError('Start this current source before sending a test webhook.')
        return sub, row

    def receipt(self, intent):
        service = self.service
        inbox = service.store.graph.get_doc('studio.inbox:' + digest({'subscription': intent['subscription'],
            'source': intent['source'], 'generation': intent['generation'], 'id': intent['event_id']}))
        if not inbox or inbox.get('wire_digest') != intent['wire_digest']:
            return None
        row = service.store.get('stream-event', inbox['event'], intent['scope'])
        if (row['source'], row['subscription'], row['generation'], row['source_event']) != (
                intent['source'], intent['subscription'], intent['generation'], intent['event_id']):
            raise PermissionError('The webhook receipt does not match this sender intent.')
        return {'id': row['id'], 'status': row['state']}

    def pending(self, sub):
        graph = self.service.store.graph
        pointer = graph.get_doc('studio.webhook-pending:' + sub['id'])
        if not pointer:
            return None
        intent = graph.get_doc(pointer['key'])
        if not intent or intent['subscription'] != sub['id'] or intent['scope'] != sub['scope'] or intent['source'] != sub['source_id']:
            raise PermissionError('The pending webhook needs reconciliation.')
        return {'idempotency_key': intent['request_key'], 'sample_id': intent['sample']}

    def _request_key(self, principal, sub, body):
        key = 'studio.webhook-test:' + digest({'subscription': sub['id'], 'principal': principal['id'], 'key': body['idempotency_key']})
        pending_key = 'studio.webhook-pending:' + sub['id']
        pointer = self.service.store.graph.get_doc(pending_key)
        if pointer and pointer != {'key': key}:
            pending = self.service.store.graph.get_doc(pointer['key'])
            if (not pending or pending.get('request_key') != body['idempotency_key']
                    or pending.get('scope') != sub['scope'] or pending.get('subscription') != sub['id']):
                raise Conflict(UNKNOWN)
            key = pointer['key']
        return key, pending_key

    def send(self, principal, sub, body):
        if set(body) != {'sample_id', 'idempotency_key'} or not isinstance(body['idempotency_key'], str) or not body['idempotency_key']:
            raise ValueError('Select a registered sample and request identity.')
        if body['sample_id'] not in {item['id'] for item in samples()}:
            raise ValueError('Select a registered synthetic sample.')
        group, sample = body['sample_id'].split(':', 1)
        raw = json.dumps(example_record(group, sample), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        with self.service.store.lock, self.service.store.graph.transaction():
            sub, source = self.active(principal, sub)
            key, pending_key = self._request_key(principal, sub, body)
            binding = {'scope': sub['scope'], 'subscription': sub['id'], 'source': source['id'],
                       'generation': source['generation'], 'sample': body['sample_id'], 'wire_digest': hashlib.sha256(raw).hexdigest()}
            prior = self.service.store.graph.get_doc(key)
            if prior:
                if any(prior.get(name) != value for name, value in binding.items()):
                    raise Conflict('This test request already names another sample.')
                receipt = self.receipt(prior)
                if receipt:
                    self.service.store.graph.delete_doc(pending_key)
                    return receipt
                if prior.get('state') == 'rejected':
                    raise BlockingIOError('This test webhook was not accepted. Start a new explicit test when ready.')
                raise Conflict(UNKNOWN)
            intent = {**binding, 'event_id': digest({'request': key, 'binding': binding}),
                      'timestamp': str(int(time.time())), 'raw': raw.decode(), 'state': 'intent',
                      'request_key': body['idempotency_key']}
            self.service.store.graph.put_doc(key, intent)
            self.service.store.graph.put_doc(pending_key, {'key': key})
        credential = []
        self.holder.provision_registered(source['id'], source['generation'], lambda value: credential.append(value) is None, scope=principal['scope'])
        self.active(principal, sub)
        response = None
        try:
            response = self._send(intent, credential[0], lambda: self.active(principal, sub))
        except (OSError, IncompleteRead, ValueError):
            pass  # Exact inbox evidence, never a transport guess, confirms delivery.
        finally:
            credential.clear()
        with self.service.store.lock:
            self.active(principal, sub)
            receipt = self.receipt(intent)
            if response and response[0] in {400, 403, 409, 429} and not receipt:
                intent['state'] = 'rejected'
                self.service.store.graph.put_doc(key, intent)
                self.service.store.graph.delete_doc(pending_key)
                raise BlockingIOError('This test webhook was not accepted. Start a new explicit test when ready.')
            if receipt and (response is None or response == (200, receipt)):
                self.service.store.graph.delete_doc(pending_key)
                return receipt
        raise Conflict(UNKNOWN)

    def _send(self, intent, credential, authorize):
        _, identity, port = self.endpoint()
        context = tls.pinned_context(identity.beacon, who=identity, context_type=_OwnContext)
        if not isinstance(context, _OwnContext):
            raise TypeError('The local sender requires its fixed TLS context.')
        context.expected_der = identity.der
        context.load_cert_chain(str(identity.certfile), str(identity.keyfile))
        endpoint = f'https://127.0.0.1:{port}' + PREFIX + intent['source']
        raw = intent['raw'].encode()
        signature = hmac.new(credential['secret'].encode(), _frame(intent['source'], intent['generation'],
                             intent['event_id'], intent['timestamp'], raw), hashlib.sha256).hexdigest()
        headers = {HEADERS['event']: intent['event_id'], HEADERS['time']: intent['timestamp'],
                   HEADERS['generation']: intent['generation'], HEADERS['signature']: signature,
                   'Content-Type': 'application/json', 'Content-Length': str(len(raw)), 'Authorization': ''}
        contacted = False
        def guard(url):
            nonlocal contacted
            authorize()
            _, actual, actual_port = self.endpoint()
            if contacted or url != endpoint or actual.der != identity.der or actual_port != port:
                raise PermissionError('The built-in sender cannot redirect or change devices.')
            contacted = True
            return url
        options = Options(headers=headers, token='', retry=ONCE, timeout=10, guard=guard, error_body_limit=4096, tls_context=context)
        try:
            with http.open_stream(endpoint, method='POST', data=raw, options=options) as response:
                raw_reply = response.read(4097)
                if len(raw_reply) > 4096:
                    raise ValueError('The webhook reply is too large.')
                return response.status, json.loads(raw_reply)
        except ServerError as exc:
            if exc.status in {400, 403, 409, 429}:
                return exc.status, {}
            raise OSError('The local webhook outcome is uncertain.') from None
