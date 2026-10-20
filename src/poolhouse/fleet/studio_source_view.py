"""Public source metadata never includes source capabilities or software credentials."""
from __future__ import annotations


def public_source(row: dict) -> dict:
    result = {key: value for key, value in row.items() if key in {
        'id', 'revision', 'kind', 'label', 'origin', 'formats', 'available', 'requirements',
        'samples', 'default_sample_id', 'template_id', 'provider', 'test_supported'}}
    connection = row.get('connection')
    if isinstance(connection, dict):
        result['connection'] = {key: value for key, value in connection.items() if key in {'state', 'reason'}}
        for key in ('can_connect', 'can_rotate', 'can_revoke', 'can_check'):
            result['connection'][key] = connection.get(key) is True
        producer = connection.get('producer')
        result['connection']['producer'] = ({key: value for key, value in producer.items()
            if key in {'id', 'label', 'device_label', 'software_label'}} if isinstance(producer, dict) else None)
        pending = connection.get('pending')
        result['connection']['pending'] = ({'idempotency_key': pending['idempotency_key']}
            if isinstance(pending, dict) and isinstance(pending.get('idempotency_key'), str) else None)
    return result
