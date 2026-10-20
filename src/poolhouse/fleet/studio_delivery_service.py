"""Bind registered webhook output intents to actual finite event runs and authority."""
from __future__ import annotations

import copy

from poolhouse.studio.outbox import Outbox, body_digest
from poolhouse.studio.store import Conflict
from poolhouse.studio.stream_delivery import deliver

from .studio_destinations import create as create_registry


def create(ui, service):
    registry = create_registry(ui, service)
    service.destinations = lambda principal, selected: destinations(registry, principal, selected)
    service.destinations_current = lambda principal, selected: all(
        item == {'kind': 'live'} or registry.snapshot(principal, item['destination']['id']) == item['destination']
        for item in selected)
    service.delivery_current = lambda row: current(service, row)
    service.delivery_owner = lambda principal, row: owner(service, principal, row)
    service.delivery_settled = lambda principal, row: settled(service, principal, row)
    service.outbox = Outbox(service.engine.store, authorize=service.delivery_current, send=deliver)


def destinations(registry, principal, selected):
    if not isinstance(selected, list) or not selected:
        raise ValueError('Select at least one live output or registered destination.')
    result = []
    for item in selected:
        if item == {'kind': 'live'}:
            resolved = item
        elif isinstance(item, dict) and set(item) == {'kind', 'id', 'revision', 'outputs'} and item['kind'] == 'webhook':
            snapshot = registry.snapshot(principal, item['id'])
            if type(item['revision']) is not int or item['revision'] != snapshot['revision']:
                raise Conflict('This destination changed elsewhere. Reload its current revision.')
            resolved = {'kind': 'webhook', 'destination': snapshot, 'outputs': copy.deepcopy(item['outputs'])}
        else:
            raise ValueError('Select a registered destination; records cannot supply delivery URLs.')
        if any(previous['kind'] == resolved['kind'] and (resolved['kind'] == 'live'
                or previous['destination']['id'] == resolved['destination']['id']) for previous in result):
            raise ValueError('Select each destination only once.')
        result.append(copy.deepcopy(resolved))
    return result


def _event_run(service, row, principal):
    event = service.store.get('stream-event', row['event'], row['scope'])
    sub = service.store.get('subscription', row['subscription'], row['scope'])
    if (event['subscription'] != sub['id'] or event['principal'] != row['principal']
            or row['id'] not in event.get('deliveries', [])):
        raise PermissionError('The historical delivery identity changed.')
    run = service.store.get('run', event['run_id'], row['scope'])
    if run['principal'] != event['principal'] or run.get('event') != event['envelope'] or run['state'] != 'done':
        raise PermissionError('The delivery does not name a completed authorized event run.')
    for node in run['graph']['nodes']:
        values = {edge['target']['port']: run['nodes'][edge['source']['node']]['outputs'].get(edge['source']['port'])
                  for edge in run['graph']['edges'] if edge['target']['node'] == node['id']}
        event_input = run['event']['input_node']
        if node['type'] == 'stream.record' and node['id'] in run['graph'].get('variants', {}).get(event_input, [event_input]):
            values['record'] = run['event']['payload']
        if service.engine.authorize(principal, node, values) is not True:
            raise PermissionError('A delivery resource is no longer authorized.')
    target = event.get('delivery_targets', {}).get(row['id'])
    if not target or target['destination'] != row['destination']:
        raise PermissionError('The declared delivery projection changed.')
    body = service._delivery_body(run, event, target['outputs'])
    if row['envelope'] != {'delivery_id': row['id'], 'request_digest': body_digest(body), 'body': body}:
        raise PermissionError('The delivery content changed from its declared run outputs.')
    return sub, event


def current(service, row):
    with service.store.lock:
        sub = service.store.get('subscription', row['subscription'], row['scope'])
        if (sub.get('inflight') != row['event'] or sub.get('service_principal') != row['principal']
                or sub['state'] == 'cancelled' or not service.authorized(sub, row['principal'])):
            return False
        event = service.store.get('stream-event', row['event'], row['scope'])
        pinned = event.get('delivery_targets', {}).get(row['id'])
        grant = service.authority.get(row['principal'])
        if (pinned not in grant['manifest']['destinations'] or pinned not in sub['destinations']
                or not service.destination_registry.current(row['principal'], row['destination'])):
            return False
        _event_run(service, row, row['principal'])
        return True


def owner(service, principal, row):
    service.authority._owner(principal)
    if principal['scope'] != row['scope']:
        return False
    with service.store.lock:
        _event_run(service, row, principal)
        service.authority._owner(principal)
        return True


def settled(service, principal, row):
    with service.store.lock, service.store.graph.transaction():
        if not owner(service, principal, row):
            raise PermissionError('Current authority for this historical delivery is required.')
        sub, event = _event_run(service, row, principal)
        rows = [service.store.get('delivery', ident, row['scope']) for ident in event['deliveries']]
        if any(item['event'] != event['id'] or item['principal'] != event['principal'] for item in rows):
            raise PermissionError('A historical delivery binding changed.')
        event['delivery_states'] = [{'id': item['id'], 'state': item['state']} for item in rows]
        if all(item['state'] in {'done', 'skipped'} for item in rows):
            event['state'] = 'done'
            if sub.get('inflight') == event['id']:
                sub['inflight'] = None
                if sub['state'] != 'cancelled':
                    sub.update(state='paused', reason='Delivery reconciled. Resume to admit new records.')
        service.store.store.put('stream-event', event)
        service.store.put_subscription(sub)
