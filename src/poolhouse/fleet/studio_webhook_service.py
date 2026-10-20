"""Bind authenticated webhook producers to one current subscription inbox."""
from __future__ import annotations

from poolhouse.studio.store import digest

from .studio_webhooks import WebhookSources


def create(ui, service):
    def source_row(ident):
        with service.store.lock:
            row = service.store.graph.get_doc('studio.source:' + ident)
        if not row or row.get('kind') != 'webhook' or row['scope'] != service.authority._context()['scope']:
            raise PermissionError('No current webhook source.')
        return row

    def current(ident):
        source = source_row(ident)
        with service.store.lock:
            binding = service.store.graph.get_doc('studio.webhook-subscription:' + ident)
            if not binding:
                return {'scope': source['scope'], 'generation': source['generation'], 'enabled': False, 'subscription': ''}
            sub = service.store.get('subscription', binding['subscription'], source['scope'])
            enabled = (binding == {'scope': source['scope'], 'generation': source['generation'],
                                   'subscription': sub['id'], 'grant': sub.get('grant')}
                       and sub['source_id'] == ident and sub['state'] == 'active'
                       and service.authorized(sub, sub['service_principal']))
            return {'scope': source['scope'], 'generation': source['generation'], 'enabled': enabled, 'subscription': sub['id']}

    def configure(principal, ident, generation):
        service.authority._owner(principal)
        source = source_row(ident)
        return source['scope'] == principal['scope'] and source['generation'] == generation

    def admit(ident, event):
        with service.store.lock, service.store.graph.transaction():
            context = current(ident)
            if not context['enabled'] or context['generation'] != event['generation']:
                raise PermissionError('Webhook source authority changed.')
            sub = service.store.get('subscription', context['subscription'], context['scope'])
            result = service.ingest(sub, event)
            if current(ident) != context:
                raise PermissionError('Webhook source authority changed before admission committed.')
            return result

    ui.studio_webhooks = WebhookSources(service.authority.root, current=current, admit=admit, configure=configure)
    return ui.studio_webhooks


def register(service, principal: dict, configuration: dict) -> dict:
    service.authority._owner(principal)
    if (set(configuration) != {'kind', 'label', 'idempotency_key'} or configuration['kind'] != 'webhook'
            or not isinstance(configuration['label'], str) or not configuration['label'].strip()):
        raise ValueError('Name the webhook source.')
    generation = digest({'scope': principal['scope'], 'principal': principal['id'],
                         'key': configuration['idempotency_key'], 'kind': 'webhook'})[:32]
    source = service.store.create('source', principal, {'kind': 'webhook', 'label': configuration['label'],
                                  'generation': generation, 'available': False, 'formats': ['json'],
                                  'requirements': ['An authorized software connector must configure the sender.']},
                                  configuration['idempotency_key'])
    return source
