"""Bind subscription authority to the actual daemon and current resource holders."""
from __future__ import annotations

import hashlib
import ssl
import time
from pathlib import Path

from poolhouse.graph.guard import MAX_BODY
from poolhouse.studio.engine import artifact_fingerprints
from poolhouse.studio.resources import model_resource
from poolhouse.studio.saved_models import PREFIX
from poolhouse.studio.store import digest
from poolhouse.studio.stream_sources import WatchedSource
from poolhouse.studio.streams import Streams

from . import studio_epoch, tls
from .discovery import key_path, load_pool_key
from .pool_roster import Pool
from .studio_authority import StudioAuthority
from .studio_delivery_service import create as create_delivery
from .studio_watch_registry import create_native_picks
from .studio_webhook_service import create as create_webhooks


def create(ui, engine, saved, scope):
    """No public graph or session flag supplies device identity or a grant."""
    def origin(principal):
        callback = ui._studio_origins.get(principal.get('id'))
        return callback() if callback else ''

    def current():
        identity = getattr(ui, 'studio_identity', lambda: None)()
        key = load_pool_key(ui.pool_key_path)
        if identity is None or key is None:
            raise PermissionError('Join this device to a pool before starting live workflows.')
        if ssl.PEM_cert_to_DER_cert(identity.certfile.read_text()) != identity.der:
            raise PermissionError('The current device certificate changed.')
        tls.server_context(identity)  # Load and match the existing private key; never create an identity.
        group = hashlib.sha256(key).hexdigest()
        active = any(member.key == key and roster.is_active(identity.fingerprint)
                     for member, roster in Pool(ui.pool_key_path).rosters())
        return {'pool': group, 'device': identity.fingerprint, 'scope': scope(ui), 'active': active}

    authority = StudioAuthority(key_path(ui.pool_key_path).parent, current=current, owner=origin,
                                resources=lambda grant, node, inputs: resources_current(ui, saved, grant, node, inputs))
    service = Streams(engine, authority, sources=Sources(engine.store),
                      resources=lambda principal, graph: pin_resources(saved, principal, graph))
    ui.studio_authority, ui.studio_streams = authority, service
    create_webhooks(ui, service)
    create_delivery(ui, service)

    def selection_epoch(principal, create=False):
        return authority.selection_epoch(principal, create=create)

    def interactive_current(principal):
        try:
            authority._owner(principal)
            return True
        except (ValueError, OSError, RuntimeError):
            return False

    def create_source(principal, binding, request_id, expected_epoch):
        return save_source(authority, service, principal, {'binding': binding, 'key': request_id,
                                                          'epoch': expected_epoch, 'origin': 'native'})

    def create_managed(principal, binding, request_id, expected_epoch):
        return save_source(authority, service, principal, {'binding': binding, 'key': request_id,
                                                          'epoch': expected_epoch, 'origin': 'managed'})

    service.create_managed_source = create_managed
    ui.studio_native_picks = create_native_picks(engine.store, selection_epoch=selection_epoch,
                                               interactive_current=interactive_current, create_source=create_source)
    return service


def save_source(authority, service, principal, selection):
    binding, expected = selection['binding'], selection['epoch']
    with service.store.lock, studio_epoch.changing(authority.root) as (_, epochs), service.store.graph.transaction():
        context = authority._owner(principal)
        anchor = epochs['pools'].get(context['pool'])
        if not anchor or anchor['epoch'] != expected:
            raise PermissionError('Source selection was revoked.')
        row = service.store.create('source', principal, {'kind': 'watched_file', 'origin': selection['origin'],
                                   'label': Path(binding['path']).name, 'binding': binding,
                                   'selection_epoch': expected, 'generation': binding['id'], 'available': True,
                                   'formats': ['csv', 'jsonl'], 'requirements': []}, selection['key'])
        return {'id': row['id'], 'title': row['label']}


def pin_resources(saved, principal: dict, graph: dict) -> list[dict]:
    result = []
    def add(proof):
        if not any(item['digest'] == proof['digest'] for item in result):
            result.append(proof)
            for dependency in proof.get('dependencies', []):
                add(dependency)
    for node in graph['nodes']:
        for binding in node.get('bindings', {}).values():
            if binding.get('kind') != 'model':
                continue
            if binding.get('id', '').startswith(PREFIX):
                add(saved.pin(principal, binding['id']))
            else:
                handle = model_resource(binding)
                proof = {'kind': 'installed_model', 'binding': binding, 'handle': handle,
                         'artifacts': artifact_fingerprints(handle)}
                add({**proof, 'digest': digest(proof)})
    return result


def resources_current(ui, saved, grant: dict, node, inputs) -> bool:
    """Exact pinned resources remain independently revocable after browser expiry."""
    if 'id' in grant:
        sub = ui.studio.store.get('subscription', grant['subscription'], grant['scope'])
        if sub.get('grant') != grant['id'] or sub['state'] == 'cancelled':
            return False
    source = grant['manifest']['source']
    if source.get('selection_epoch') and studio_epoch.generation(key_path(ui.pool_key_path).parent, grant['pool']) != source['selection_epoch']:
        return False
    registry = getattr(ui, 'studio_destinations', None)
    for destination in grant['manifest']['destinations']:
        if destination.get('kind') == 'webhook' and (registry is None or not registry.current(
                {'scope': grant['scope']}, destination['destination'])):
            return False
    proofs = grant['manifest']['resources']
    for proof in proofs:
        if proof['kind'] == 'installed_model':
            handle = model_resource(proof['binding'])
            if handle != proof['handle'] or artifact_fingerprints(handle) != proof['artifacts']:
                return False
    principal = StudioAuthority.principal(grant) if 'id' in grant else {'kind': 'studio_delegation', 'scope': grant['scope']}
    additional = getattr(ui, 'studio_authorize', None)
    def proof_current(caller, proof, producer, values):
        if proof not in proofs:
            return False
        if 'id' in grant and ui.studio_authority.get(caller)['id'] != grant['id']:
            return False
        return additional(caller, producer, values) is True if additional else True
    for proof in proofs:
        if proof['kind'] == 'saved_model':
            saved.resolve_granted(principal, proof, authorize=proof_current)
    return not additional or additional(principal, node, inputs) is True


class Sources:
    """Keep only active descriptor scanners; source state remains transactional."""

    def __init__(self, store):
        self.store, self.watchers = store, {}

    def bind(self, sub: dict, grant: dict) -> None:
        source = self.store.get('source', sub['source_id'], sub['scope'])
        if source['kind'] != 'webhook':
            return
        if source.get('provider') and not self.webhook_current(source['id'], source['generation']):
            raise PermissionError('This software sender is unavailable.')
        key = 'studio.webhook-subscription:' + source['id']
        previous = self.store.graph.get_doc(key)
        if previous and previous['subscription'] != sub['id']:
            raise ValueError('A webhook source belongs to one subscription.')
        self.store.graph.put_doc(key, {'scope': sub['scope'], 'subscription': sub['id'],
                                      'grant': grant['id'], 'generation': source['generation']})

    def poll(self, sub: dict, *, authorize):
        source = self.store.get('source', sub['source_id'], sub['scope'])
        if source['kind'] != 'watched_file':
            return None
        key = sub['id']
        if key not in self.watchers:
            self.watchers[key] = WatchedSource(source['binding'])
        return self.watchers[key].poll(sub.get('cursor'), authorize=authorize,
                                      max_record_bytes=MAX_BODY, deadline=time.monotonic() + 0.25)

    def release(self, subscription):
        watcher = self.watchers.pop(subscription, None)
        if watcher:
            watcher.close()

    def close(self):
        for watcher in self.watchers.values():
            watcher.close()
        self.watchers.clear()
