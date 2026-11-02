"""Fixed setup facade for synthetic senders and explicitly connected software."""
from __future__ import annotations

import copy
import re
import threading

from .producer_connections import PROVIDER
from .request_fields import object_body
from .studio_source_view import public_source
from .studio_webhook_setup import PROVIDER as LOCAL

_UNAVAILABLE = 'The connected software producer is unavailable.'
_FAILURES = (OSError, ValueError, RuntimeError, KeyError)
_ACTIONS = frozenset({'connect', 'rotate', 'revoke', 'connection-status'})


def _unavailable(row):
    return {**copy.deepcopy(row), 'available': False, 'test_supported': False, 'requirements': [_UNAVAILABLE],
            'connection': {'state': 'unavailable', 'producer': None, 'reason': _UNAVAILABLE,
                           'can_connect': False, 'can_rotate': False, 'can_revoke': False,
                           'can_check': False, 'pending': None}}


def _provider(value):
    fields = {'id', 'label', 'available', 'requirements', 'offers'}
    if (not isinstance(value, dict) or set(value) != fields or value['id'] != PROVIDER
            or not isinstance(value['label'], str) or type(value['available']) is not bool
            or not isinstance(value['requirements'], list)
            or any(not isinstance(item, str) for item in value['requirements'])
            or not isinstance(value['offers'], list)):
        raise ValueError('The software provider returned invalid metadata.')
    for offer in value['offers']:
        if (not isinstance(offer, dict) or set(offer) != {'id', 'revision', 'label', 'device_label', 'software_label', 'available', 'reason'}
                or not isinstance(offer['id'], str) or not re.fullmatch('[0-9a-f]{32}', offer['id'])
                or type(offer['revision']) is not int or offer['revision'] != 1
                or offer['available'] is not True or offer['reason'] != ''
                or any(not isinstance(offer[name], str) for name in ('label', 'device_label', 'software_label'))):
            raise ValueError('The software provider returned an invalid offer.')
    return copy.deepcopy(value)


class Setup:
    """Private metadata callers must already have passed the actual owner route guard.

    The fixed factory constructs infrastructure only and cleans partial resources before a
    definite failure. Later explicit metadata calls may retry that failure. A successful
    runtime is retained permanently; readiness callbacks never construct or replace it.
    """

    def __init__(self, ui, service, local, runtime_factory):
        self.ui, self.service, self.local = ui, service, local
        self.factory, self.lock = runtime_factory, threading.RLock()
        self.runtime, self.context, self.closed = None, None, False

    def _open(self):
        with self.lock:
            if self.closed:
                raise PermissionError('The software producer setup is closed.')

    def _runtime(self, *, create):
        if create and self.service.store.lock._is_owned():
            raise PermissionError('Software producer setup must run outside workflow admission.')
        with self.lock:
            self._open()
            if self.runtime is None:
                if not create:
                    return None
                context = copy.deepcopy(self.service.authority._context())
                # This factory owns deterministic cleanup on failure and never grants a source.
                runtime = self.factory()
                if runtime is None:
                    raise PermissionError('The installed software producer service is unavailable.')
                self.runtime, self.context = runtime, context
            if self.service.authority._context() != self.context:
                raise PermissionError('The original software producer context changed.')
            return self.runtime

    def _source(self, ident, scope=None):
        self._open()
        context = self.service.authority._context()
        if scope is not None and scope != context['scope']:
            raise PermissionError('This source belongs to another current scope.')
        return self.service.store.get('source', ident, context['scope'])

    def providers(self):
        self._open()
        values = self.local.providers()
        try:
            runtime = self._runtime(create=True)
            software = _provider(runtime.providers())
            self._runtime(create=False)
        except _FAILURES:
            software = {'id': PROVIDER, 'label': 'Connected software producer', 'available': False,
                        'requirements': ['The installed software producer service is unavailable.'], 'offers': []}
        return [*values, software]

    def describe(self, row):
        source = self._source(row['id'], row['scope'])
        if source.get('provider') != PROVIDER:
            return self.local.describe(source)
        try:
            result = self._runtime(create=True).describe(copy.deepcopy(source))
            if (not isinstance(result, dict) or type(result.get('available')) is not bool
                    or any(result.get(name) != source.get(name)
                    for name in ('id', 'scope', 'provider', 'generation', 'revision'))):
                raise ValueError('The configured software source changed.')
            self._runtime(create=False)
            return {**result, 'test_supported': False}
        except _FAILURES:
            return _unavailable(source)

    def register(self, principal, body):
        self.service.authority._owner(principal)
        if not isinstance(body, dict) or body.get('kind') != 'webhook':
            raise ValueError('Choose a registered software source.')
        if body.get('provider') == LOCAL:
            self._open()
            return self.local.register(principal, body)
        if body.get('provider') != PROVIDER:
            raise ValueError('Choose a registered software provider.')
        row = self._runtime(create=True).connections.register(principal, body)
        self.service.authority._owner(principal)
        return self.describe(row)

    def _ready(self, ident, generation, method):
        try:
            source = self._source(ident)
            if source.get('generation') != generation:
                return False
            if source.get('provider') == PROVIDER:
                runtime = self._runtime(create=False)
                return runtime is not None and getattr(runtime, method)(ident, generation) is True
            if source.get('provider') == LOCAL:
                return getattr(self.local, method)(ident, generation) is True
            return False
        except _FAILURES:
            return False

    def ready(self, ident, generation):
        return self._ready(ident, generation, 'ready')

    def current(self, ident, generation):
        return self._ready(ident, generation, 'current')

    def _subscription_source(self, sub):
        self._open()
        context = self.service.authority._context()
        if sub['scope'] != context['scope']:
            raise PermissionError('This subscription belongs to another scope.')
        actual = self.service.store.get('subscription', sub['id'], context['scope'])
        return actual, self._source(actual['source_id'], actual['scope'])

    def pending(self, sub):
        actual, source = self._subscription_source(sub)
        return self.local.pending(actual) if source.get('provider') == LOCAL else None

    def send(self, principal, sub, body):
        self.service.authority._owner(principal)
        actual, source = self._subscription_source(sub)
        if source.get('provider') != LOCAL:
            raise PermissionError('Real software records are sent only by their connected process.')
        return self.local.send(principal, actual, body)

    def preflight(self, source):
        actual = self._source(source['id'], source['scope'])
        if actual.get('provider') == PROVIDER:
            try:
                if self._runtime(create=True).available(actual) is not True:
                    raise PermissionError('The configured software producer is unavailable.')
                self._runtime(create=False)
            except _FAILURES:
                raise PermissionError('Connect the current software producer before starting this stream.') from None
            return True
        if not self.ready(actual['id'], actual['generation']):
            raise PermissionError('The configured software sender is unavailable.')
        return True

    def route(self, handler, service, principal, parts):
        if not ((len(parts) == 1 and handler.method == 'GET') or
                (len(parts) == 2 and handler.method == 'POST' and parts[1] in _ACTIONS)):
            return False
        if service is not self.service:
            raise PermissionError('This source belongs to another service.')
        service.authority._owner(principal)
        if not isinstance(parts[0], str) or not re.fullmatch('[0-9a-f]{32}', parts[0]):
            raise ValueError('Select a current source identity.')
        source = self._source(parts[0], principal['scope'])
        if handler.method == 'POST':
            if source.get('provider') != PROVIDER:
                raise ValueError('Select a connected software producer source.')
            body = object_body(handler)
            source = self._runtime(create=True).action(principal, source, parts[1], body)
            service.authority._owner(principal)
        handler.send(200, {'source': public_source(self.describe(source))})
        return True

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            runtime = self.runtime
        if runtime is not None:
            runtime.close()
