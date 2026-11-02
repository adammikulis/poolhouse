"""Explicit preview-row selections, distinct from full immutable dataset imports."""
from __future__ import annotations

import copy
import hashlib
import json
import re

from poolhouse.graph.guard import MAX_BODY

from .store import digest

FIELDS = {'repo', 'config', 'split', 'expected_preview_digest', 'row_indexes', 'columns', 'name', 'idempotency_key'}
HEX = re.compile('[0-9a-f]{64}')


def preview_digest(repo, config, split, preview):
    return digest({'repo': repo, 'config': config, 'split': split,
                   'columns': preview['columns'], 'rows': preview['rows']})


def checked_body(body):
    if not isinstance(body, dict) or set(body) != FIELDS:
        raise ValueError('Choose the dataset preview, rows and columns to import.')
    for key in ('repo', 'config', 'split', 'name', 'idempotency_key'):
        if not isinstance(body[key], str) or not body[key].strip() or len(body[key]) > 256:
            raise ValueError('Dataset selection fields must be nonempty text.')
    if not isinstance(body['expected_preview_digest'], str) or not HEX.fullmatch(body['expected_preview_digest']):
        raise ValueError('Refresh the dataset preview before importing rows.')
    indexes = body['row_indexes']
    if (not isinstance(indexes, list) or not indexes or any(type(index) is not int or index < 0 for index in indexes)
            or len(set(indexes)) != len(indexes)):
        raise ValueError('Select each displayed preview row once.')
    columns = body['columns']
    if (not isinstance(columns, dict) or not columns
            or any(not isinstance(key, str) or not key or not isinstance(value, str) or not value.strip()
                   or len(value) > 256 or any(ord(char) < 32 for char in value) for key, value in columns.items())
            or len(set(columns.values())) != len(columns)):
        raise ValueError('Choose columns with distinct output names.')
    return copy.deepcopy(body)


def selected(body, preview):
    body = checked_body(body)
    actual = preview_digest(body['repo'], body['config'], body['split'], preview)
    if actual != body['expected_preview_digest']:
        raise ValueError('The preview changed. Review the current rows before importing.')
    columns = preview['columns']
    names = [column['name'] for column in columns]
    if len(set(names)) != len(names) or not set(body['columns']) <= set(names):
        raise ValueError('Choose columns present in the displayed preview.')
    if any(index >= len(preview['rows']) for index in body['row_indexes']):
        raise ValueError('Choose rows present in the displayed preview.')
    rows = []
    for index in body['row_indexes']:
        row = preview['rows'][index]
        if not isinstance(row, dict) or not set(body['columns']) <= set(row):
            raise ValueError('A selected row is missing a selected column.')
        rows.append({body['columns'][name]: copy.deepcopy(row[name]) for name in sorted(body['columns'])})
    data = b''.join(json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                               allow_nan=False).encode() + b'\n' for row in rows)
    if len(data) > MAX_BODY:
        raise ValueError('Select fewer preview rows to keep the dataset within the record budget.')
    schema = [{'name': body['columns'][name], 'type': copy.deepcopy(columns[names.index(name)]['type'])}
              for name in sorted(body['columns'])]
    manifest = {'version': 1, 'rows': len(rows), 'columns': schema,
                'file': {'name': 'dataset.jsonl', 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()},
                'source': {'kind': 'huggingface-preview', 'repo': body['repo'], 'config': body['config'],
                           'split': body['split'], 'preview_digest': actual,
                           'row_indexes': body['row_indexes'], 'columns': body['columns']}}
    return data, manifest
