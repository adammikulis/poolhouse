"""Actor and execution views of reviewed outcomes, read from the ledger that recorded them."""

from collections import defaultdict

from poolhouse.board.taskspec import fingerprint
from poolhouse.reputation import economy


def _outcomes(team):
    """Each reviewed attempt of ``team`` (the ledger's standings) with the award its review earned, if it earned one."""
    result = []
    for row in team:
        awards = {item['completion']: item for item in row['evidence']}
        for item in row.get('contributions', []):
            award = awards.get(item['completion'])
            result.append({**item, 'provenance': item.get('provenance') or {}, 'source': 'taskboard',
                           'award': award['award'] if award else None, 'evidence_id': award['id'] if award else None})
    return result


def _rows(groups, kind, offset):
    result = []
    for identity, items in sorted(groups.items()):
        evidence = [item for item in items if item['outcome'] == 'accepted' and item['award']]
        aliases = sorted({item['provenance']['alias'] for item in items if item['provenance'].get('alias')})
        ordered = sorted(items, key=lambda item: -item['verified_at'])
        execution = items[0]['provenance']
        details = {key: execution.get(key) for key in
                   ('model', 'runtime', 'model_source', 'runtime_source', 'artifact', 'broker_runtime')} if kind == 'model-runtime' else {}
        result.append({'id': identity, 'dimension': kind, 'aliases': aliases, **details,
                       'members': sorted({item['agent'] for item in items}),
                       'verified_tasks': len(evidence), **economy.summary(evidence, items),
                       'evidence': ordered[offset:offset + 20], 'evidence_held': max(0, len(items) - offset - 20)})
    return result


def views(team, *, agent='', offset=0):
    """Derive reputation dimensions without recording scores or additional awards."""
    agents, models = defaultdict(list), defaultdict(list)
    for item in _outcomes(team):
        if agent and item['agent'] != agent:
            continue
        agents[item['agent']].append(item)
        provenance = item['provenance']
        artifact = provenance.get('artifact') or {}
        identity = fingerprint({'model': provenance.get('model'), 'runtime': provenance.get('runtime'),
                                'model_source': provenance.get('model_source', 'unknown'),
                                'artifact': artifact.get('id'),
                                'device': None if artifact.get('id') else provenance.get('device_id'),
                                'broker_runtime': provenance.get('broker_runtime')})
        models[identity].append(item)
    return {'agents': _rows(agents, 'agent', offset), 'models': _rows(models, 'model-runtime', offset),
            'award_policy': 'one-award-per-task; dimensions are overlapping views'}
