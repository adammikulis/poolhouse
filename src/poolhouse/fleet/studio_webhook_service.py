"""Bind authenticated webhook producers to one current subscription inbox."""
from __future__ import annotations

from poolhouse.studio.store import digest

from .producer_runtime import start as start_producers
from .producer_setup import Setup
from .studio_webhook_setup import LocalSender
from .studio_webhooks import PRODUCER_PROTOCOL, PROTOCOL, WebhookSources


def create(ui, service):
    def source_row(ident):
        with service.store.lock:
            row = service.store.graph.get_doc('studio.source:' + ident)
        if not row or row.get('kind') != 'webhook' or row['scope'] != service.authority._context()['scope']:
            raise PermissionError('No current webhook source.')
        return row

    def current(ident):
        source = source_row(ident)
        protocol = {None: PROTOCOL, 'local-synthetic': PROTOCOL,
                    'software-producer': PRODUCER_PROTOCOL}[source.get('provider')]
        if source.get('provider') and not ui.studio_webhook_setup.ready(ident, source['generation']):
            raise PermissionError('This software sender is no longer authorized.')
        with service.store.lock:
            binding = service.store.graph.get_doc('studio.webhook-subscription:' + ident)
            if not binding:
                return {'scope': source['scope'], 'generation': source['generation'], 'enabled': False,
                        'subscription': '', 'protocol': protocol}
            sub = service.store.get('subscription', binding['subscription'], source['scope'])
            enabled = (binding == {'scope': source['scope'], 'generation': source['generation'],
                                   'subscription': sub['id'], 'grant': sub.get('grant')}
                       and sub['source_id'] == ident and sub['state'] == 'active'
                       and service.authorized(sub, sub['service_principal']))
            return {'scope': source['scope'], 'generation': source['generation'], 'enabled': enabled,
                    'subscription': sub['id'], 'protocol': protocol}

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
            if context['protocol'] == PRODUCER_PROTOCOL:
                admission = getattr(ui, 'studio_producer_admission', None)
                if admission is None:
                    raise PermissionError('This software producer is not configured.')
                result = admission.admit(sub, event)
            else:
                result = service.ingest(sub, event)
            if current(ident) != context:
                raise PermissionError('Webhook source authority changed before admission committed.')
        if result == {'rejected': True}:
            raise BlockingIOError('The previous record is still being processed.')
        return result

    ui.studio_webhooks = WebhookSources(service.authority.root, current=current, admit=admit, configure=configure)
    local = LocalSender(ui, service)
    ui.studio_webhook_setup = Setup(ui, service, local, lambda: start_producers(ui, service, local))
    service.webhook_setup = ui.studio_webhook_setup
    ui.studio_webhooks.provider_current = ui.studio_webhook_setup.current
    service.sources.webhook_current = ui.studio_webhook_setup.ready
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
