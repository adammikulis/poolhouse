"""Authenticated configuration and observation of durable Studio subscriptions."""
from __future__ import annotations

import urllib.parse

from poolhouse.studio.decision_templates import example_record, sample_sets
from poolhouse.studio.store import digest
from poolhouse.studio.stream_sources import bind

from .files import safe_relpath
from .request_fields import object_body
from .studio_webhook_service import register as register_webhook


def public_source(row: dict) -> dict:
    return {key: value for key, value in row.items() if key in {
        'id', 'kind', 'label', 'origin', 'formats', 'available', 'requirements', 'samples', 'default_sample_id', 'template_id'}}


def public_subscription(row: dict, service) -> dict:
    return {key: row[key] for key in ('id', 'revision', 'state', 'reason', 'source_id', 'input_node', 'output_nodes') if key in row} | {
        'workflow_id': row['workflow']['id'], 'workflow_revision': row['workflow']['revision'],
        'counts': row.get('counts', {}), 'cursor': row['next_sequence'] - 1,
        'has_grant': bool(row.get('grant')), 'can_resume': can_resume(row, service)}


def can_resume(row, service) -> bool:
    try:
        return bool(row.get('service_principal') and row['state'] == 'paused'
                    and service.authorized(row, row['service_principal']))
    except (ValueError, OSError, RuntimeError, KeyError):
        return False


def checked_owner(service, principal):
    service.authority._owner(principal)


def sources(handler, service, principal) -> bool:
    checked_owner(service, principal)
    if handler.method == 'GET':
        for sample in sample_sets():
            service.store.create('source', principal, {**sample, 'template_id': sample['id'], 'generation': 'examples-v1'}, 'example:' + sample['id'])
        handler.send(200, {'sources': [public_source(row) for row in service.store.indexed('source', principal['scope'])]})
    elif handler.method == 'POST':
        body = object_body(handler)
        if body.get('kind') == 'webhook':
            row = register_webhook(service, principal, body)
            handler.ui.studio_webhooks.register(principal, row['id'], row['generation'])
            handler.send(201, {'source': public_source(row)})
            return True
        if set(body) != {'kind', 'resource_id', 'idempotency_key'} or body['kind'] != 'watched_file':
            raise ValueError('Select an uploaded file or choose an original file in the app.')
        path = safe_relpath(handler.ui.runner.files_root, body['resource_id'])
        epoch = service.authority.selection_epoch(principal, create=True)
        binding = bind(path, 'folder' if path.is_dir() else 'file', authorize=lambda: service.authority.selection_epoch(principal) == epoch)
        binding['id'] = digest({'scope': principal['scope'], 'key': body['idempotency_key'], 'epoch': epoch})[:32]
        selected = service.create_managed_source(principal, binding, body['idempotency_key'], epoch)
        row = service.store.get('source', selected['id'], principal['scope'])
        handler.send(201, {'source': public_source(row)})
    else:
        return False
    return True


def subscriptions(handler, service, principal, parts: list[str]) -> bool:
    checked_owner(service, principal)
    if not parts:
        if handler.method == 'GET':
            handler.send(200, {'subscriptions': [public_subscription(row, service) for row in service.store.indexed('subscription', principal['scope'])]})
        elif handler.method == 'POST':
            body = object_body(handler)
            expected = {'workflow_id', 'revision', 'source_id', 'input_node', 'output_nodes', 'destinations', 'idempotency_key'}
            if set(body) != expected:
                raise ValueError('Select a workflow revision, source, input and outputs.')
            workflow = service.store.get('workflow', body['workflow_id'], principal['scope'])
            if workflow['revision'] != body['revision']:
                raise ValueError('Reload the current workflow revision.')
            config = {key: body[key] for key in ('source_id', 'input_node', 'output_nodes', 'destinations')}
            row = service.create(principal, {**config, 'workflow': workflow}, body['idempotency_key'])
            handler.send(201, {'subscription': public_subscription(row, service)})
        else:
            return False
        return True
    sub = service.store.get('subscription', parts[0], principal['scope'])
    if len(parts) == 1 and handler.method == 'GET':
        handler.send(200, {'subscription': public_subscription(sub, service)})
        return True
    if len(parts) != 2:
        return False
    action = parts[1]
    if action in {'start', 'renew', 'resume', 'pause', 'cancel'} and handler.method == 'POST':
        body = object_body(handler)
        if set(body) != {'revision', 'idempotency_key'}:
            raise ValueError('A subscription revision and action identity are required.')
        operation = {'pause': service.pause, 'resume': service.resume, 'cancel': service.cancel}.get(action, service.activate)
        row = operation(principal, sub['id'], body)
        handler.send(200, {'subscription': public_subscription(row, service)})
        return True
    if action == 'preview' and handler.method == 'GET':
        return preview(handler, service, principal, sub)
    if action == 'events':
        if handler.method == 'POST':
            body = object_body(handler)
            if set(body) != {'sample_id', 'idempotency_key'}:
                raise ValueError('Select a registered synthetic sample.')
            source = service.store.get('source', sub['source_id'], principal['scope'])
            if source['kind'] != 'examples':
                raise ValueError('This source does not supply synthetic samples.')
            event = service.ingest(sub, {'id': body['idempotency_key'], 'generation': source['generation'],
                                        'payload': example_record(source['template_id'], body['sample_id'])})
            handler.send(202, {'event': public_event(event)})
        elif handler.method == 'GET':
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
            page = service.read_events(sub, int(query.get('after', ['0'])[0]),
                                      limit=int(query.get('limit', ['50'])[0]), tail=query.get('tail') == ['1'])
            handler.send(200, {'events': [public_event(row) for row in page['events']], 'next_cursor': page['next_cursor']})
        else:
            return False
        return True
    return False


def public_event(row: dict) -> dict:
    return {key: value for key, value in row.items() if key in {'id', 'sequence', 'state', 'run_id', 'result', 'error', 'received_at'}}


def route(handler, service, principal) -> bool:
    path = urllib.parse.urlsplit(handler.path).path
    if path == '/ui/studio/sources':
        return sources(handler, service, principal)
    prefix = '/ui/studio/subscriptions'
    if path == prefix or path.startswith(prefix + '/'):
        return subscriptions(handler, service, principal, path[len(prefix):].strip('/').split('/') if path != prefix else [])
    return False


def preview(handler, service, principal, sub) -> bool:
    source = service.store.get('source', sub['source_id'], principal['scope'])
    if source['kind'] != 'examples':
        raise ValueError('Preview is available for registered synthetic samples.')
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    handler.send(200, {'record': example_record(source['template_id'], query.get('sample_id', ['sample'])[0])})
    return True
