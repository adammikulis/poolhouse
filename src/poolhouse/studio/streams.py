"""Durable ordered subscriptions composed from finite authorized workflow runs."""
from __future__ import annotations

import copy
import threading

from .store import Conflict, digest
from .stream_store import StreamStore

TERMINAL = {'done', 'failed', 'cancelled'}


class Streams:
    """Only trusted source, authority and resource callbacks cross the service boundary."""

    def __init__(self, engine, authority, *, sources, resources) -> None:
        self.engine, self.authority = engine, authority
        self.store = StreamStore(engine.store)
        self.sources, self.resources = sources, resources
        self._stop = threading.Event()
        self._thread = None

    def start_scheduler(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name='studio-streams', daemon=True)
            self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join()
        self.sources.close()

    def create(self, principal: dict, configuration: dict, key: str) -> dict:
        allowed = {'workflow', 'source_id', 'input_node', 'output_nodes', 'destinations'}
        if set(configuration) != allowed or configuration['destinations'] != [{'kind': 'live'}]:
            raise ValueError('Select a workflow, source and local live output.')
        workflow = configuration['workflow']
        self.engine.prepare(principal, workflow, configuration['output_nodes'])
        source = self.store.get('source', configuration['source_id'], principal['scope'])
        if source['owner'] != principal['id'] and not principal.get('owner'):
            raise PermissionError('This source belongs to another session.')
        return self.store.create('subscription', principal, configuration, key)

    def activate(self, principal: dict, ident: str, request: dict) -> dict:
        with self.store.lock:
            sub = self.store.get('subscription', ident, principal['scope'])
            command = self._command(sub, principal, 'start', request)
            if self.store.graph.get_doc(command):
                self.authority.get(sub['service_principal'])
                return sub
            if request['revision'] != sub['revision']:
                raise Conflict('This subscription changed elsewhere.')
            if sub['state'] == 'cancelled':
                raise ValueError('A cancelled subscription cannot be restarted.')
            if sub.get('inflight'):
                raise ValueError('Resolve the admitted event before changing execution authority.')
            graph = self.engine.prepare(principal, sub['workflow'], sub['output_nodes'])
            inputs = [node for node in graph['nodes'] if node['id'] == sub['input_node'] and node['type'] == 'stream.record']
            if len(inputs) != 1:
                raise ValueError('Select a stream record input in this workflow.')
            source = self.store.get('source', sub['source_id'], principal['scope'])
            manifest = {'workflow': {**sub['workflow'], 'graph': graph}, 'source': source,
                        'destinations': sub['destinations'], 'resources': self.resources(principal, graph)}
            grant = self.authority.mint(principal, ident, manifest, request['idempotency_key'])
            sub.update(grant=grant['id'], service_principal=self.authority.principal(grant),
                       resolved_workflow=manifest['workflow'], state='active', reason=None,
                       revision=sub['revision'] + 1)
            with self.store.graph.transaction():
                self.store.graph.put_doc(command, {'request': digest(request)})
                return self.store.put_subscription(sub)

    def _command(self, sub: dict, principal: dict, action: str, request: dict) -> str:
        self.authority._owner(principal)
        if not isinstance(request.get('idempotency_key'), str) or not request['idempotency_key']:
            raise ValueError('An action identity is required.')
        command = 'studio.stream-command:' + digest({'subscription': sub['id'], 'principal': principal['id'],
                                                     'action': action, 'key': request['idempotency_key']})
        prior = self.store.graph.get_doc(command)
        if prior and prior['request'] != digest(request):
            raise Conflict('This action identity already names another request.')
        return command

    def authorized(self, sub: dict, principal: dict) -> bool:
        grant = self.authority.get(principal)
        return (grant['subscription'] == sub['id'] and grant['id'] == sub.get('grant')
                and grant['manifest']['workflow'] == sub['resolved_workflow']
                and grant['manifest']['source'] == self.store.get('source', sub['source_id'], sub['scope'])
                and grant['manifest']['destinations'] == sub['destinations'])

    def ingest(self, subscription: dict, incoming: dict) -> dict:
        return self.store.admit(subscription, incoming, subscription['service_principal'], self.authorized)

    def pause(self, principal: dict, ident: str, request: dict) -> dict:
        with self.store.lock:
            sub = self.store.get('subscription', ident, principal['scope'])
            command = self._command(sub, principal, 'pause', request)
            if self.store.graph.get_doc(command):
                return sub
            if request['revision'] != sub['revision']:
                raise Conflict('This subscription changed elsewhere.')
            sub.update(state='paused', revision=sub['revision'] + 1)
            with self.store.graph.transaction():
                self.store.graph.put_doc(command, {'request': digest(request)})
                return self.store.put_subscription(sub)

    def advance(self, subscription: dict) -> None:
        with self.store.lock:
            sub = self.store.get('subscription', subscription['id'], subscription['scope'])
        principal = sub['service_principal']
        if not self.authorized(sub, principal):
            raise PermissionError('Subscription authority is no longer available.')
        if sub.get('inflight'):
            self._event(sub, principal)
        elif sub['state'] == 'active':
            result = self.sources.poll(sub, authorize=lambda: self._intake_current(sub, principal))
            if result:
                if result['event'] is None:
                    with self.store.lock, self.store.graph.transaction():
                        current = self.store.get('subscription', sub['id'], sub['scope'])
                        if not self._intake_current(sub, principal):
                            raise PermissionError('Source authority changed.')
                        current['cursor'] = result['cursor']
                        self.store.put_subscription(current)
                else:
                    self.ingest(sub, {**result['event'], 'cursor': result['cursor']})

    def _intake_current(self, expected: dict, principal: dict) -> bool:
        with self.store.lock:
            current = self.store.get('subscription', expected['id'], expected['scope'])
            return (current['state'] == 'active' and not current.get('inflight')
                    and all(current.get(name) == expected.get(name) for name in ('revision', 'cursor', 'source_id', 'grant'))
                    and self.authorized(current, principal))

    def _event(self, sub: dict, principal: dict) -> None:
        with self.store.lock:
            event = self.store.get('stream-event', sub['inflight'], sub['scope'])
        if event['principal'] != principal:
            raise PermissionError('An admitted event cannot change execution identity.')
        if event['state'] == 'queued':
            # The fixed event identity attaches on restart; engine creates run+event atomically.
            run = self.engine.submit_event(principal, sub['workflow'], event['envelope'])
            event.update(run_id=run['id'], state='running')
            self.store.store.put('stream-event', event)
        if event['state'] == 'running':
            run = self.store.get('run', event['run_id'], sub['scope'])
            if run['principal'] != principal or run.get('event') != event['envelope']:
                raise PermissionError('Event run identity requires reconciliation.')
            if run['state'] in TERMINAL | {'interrupted'}:
                event['state'] = run['state']
                event['result'] = {'outputs': [{'node': node, 'port': port, 'value': copy.deepcopy(value)}
                                                for node, state in run['nodes'].items() if state['state'] == 'done'
                                                for port, value in state['outputs'].items()
                                                if not sub['output_nodes'] or node in sub['output_nodes']]}
                with self.store.lock, self.store.graph.transaction():
                    if not self.authorized(sub, principal):
                        raise PermissionError('Output authority changed.')
                    self.store.store.put('stream-event', event)
                    current = self.store.get('subscription', sub['id'], sub['scope'])
                    if current.get('inflight') != event['id'] or current.get('service_principal') != event['principal']:
                        raise Conflict('The admitted event slot changed during completion.')
                    if event['state'] == 'interrupted':
                        current.update(state='blocked', reason='An event outcome needs reconciliation.')
                    else:
                        current['inflight'] = None
                    self.store.put_subscription(current)

    def _loop(self) -> None:
        while not self._stop.wait(0.25):
            for sub in self.store.active():
                try:
                    self.advance(sub)
                except (ValueError, OSError, RuntimeError, KeyError) as exc:
                    with self.store.lock:
                        current = self.store.get('subscription', sub['id'], sub['scope'])
                        current.update(state='blocked', reason=str(exc))
                        self.store.put_subscription(current)
