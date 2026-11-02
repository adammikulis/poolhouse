"""Bounded literal record transformations and typed conditions; data never grants authority."""
from __future__ import annotations

import copy
import json
import math
import operator

from .record_context import MAX_TEXT

MAX_FIELDS = 256
MAX_DEPTH = 16


def _json(value, depth=0):
    if depth > MAX_DEPTH:
        raise ValueError('Record data exceeds the supported JSON depth')
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError('Record object keys must be literal strings')
        for child in value.values():
            _json(child, depth + 1)
    elif type(value) is list:
        for child in value:
            _json(child, depth + 1)
    elif type(value) not in (str, int, float, bool, type(None)):
        raise ValueError('Use finite JSON record data')
    elif type(value) is float and not math.isfinite(value):
        raise ValueError('Use finite JSON record numbers')
    if depth == 0 and len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')) > MAX_TEXT:
        raise ValueError('Record data exceeds the supported JSON size')


def _field(value):
    if type(value) is not str or not value or len(value.encode('utf-8')) > MAX_FIELDS:
        raise ValueError('Select a bounded nonempty literal field name')
    return value


def _keys(value, names):
    if type(value) is not dict or set(value) != set(names):
        raise ValueError('Use the exact declared record controls')


def parameters(kind: str, values: dict) -> dict:
    """Check every control before record evaluation or downstream branch selection."""
    _json(values)
    if kind == 'record.map':
        _keys(values, ('keep_other', 'assignments'))
        rows = values['assignments']
        if type(values['keep_other']) is not bool or type(rows) is not list or len(rows) > MAX_FIELDS:
            raise ValueError('Use a boolean record projection and bounded assignments')
        targets = set()
        for row in rows:
            _keys(row, ('target', 'source'))
            target = _field(row['target'])
            if target in targets:
                raise ValueError('Assign each target field once')
            targets.add(target)
            source = row['source']
            if type(source) is not dict or set(source) not in ({'field'}, {'value'}):
                raise ValueError('Select exactly one original field or literal JSON value')
            if 'field' in source:
                _field(source['field'])
    elif kind == 'record.if':
        if type(values) is not dict or type(values.get('operator')) is not str:
            raise ValueError('Select a declared record comparison')
        name = values['operator']
        _keys(values, ('field', 'operator') if name == 'exists' else ('field', 'operator', 'value'))
        _field(values['field'])
        if name not in {'exists', 'equals', 'not_equals', 'lt', 'lte', 'gt', 'gte'}:
            raise ValueError('Select a declared record comparison')
        if name in {'lt', 'lte', 'gt', 'gte'} and type(values['value']) not in (int, float):
            raise ValueError('Ordered comparisons require a finite number')
    else:
        raise ValueError('Unknown fixed record operation')
    return copy.deepcopy(values)


def _equal(left, right):
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return set(left) == set(right) and all(_equal(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right, strict=True))
    return left == right


def _map(controls, record):
    assignments = controls['assignments']
    if any('field' in row['source'] and row['source']['field'] not in record for row in assignments):
        raise ValueError('A selected original record field is missing')
    output = copy.deepcopy(record) if controls['keep_other'] else {}
    for row in assignments:
        source = row['source']
        output[row['target']] = copy.deepcopy(record[source['field']] if 'field' in source else source['value'])
    _json(output)
    return {'record': output}


def _condition(controls, record):
    field, name = controls['field'], controls['operator']
    if name == 'exists':
        return field in record
    if field not in record:
        raise ValueError('A selected comparison field is missing')
    left, right = record[field], controls['value']
    if name in {'equals', 'not_equals'}:
        equal = _equal(left, right)
        return equal if name == 'equals' else not equal
    if type(left) not in (int, float):
        raise ValueError('Ordered comparisons require a finite number')
    return {'lt': operator.lt, 'lte': operator.le, 'gt': operator.gt, 'gte': operator.ge}[name](left, right)


def execute(kind: str, values: dict, record: dict) -> dict:
    """Return a copied record and exactly one typed branch, without interpreting payload controls."""
    controls = parameters(kind, values)
    if type(record) is not dict:
        raise ValueError('A record must be a JSON object')
    _json(record)
    if kind == 'record.map':
        return _map(controls, record)
    allowed = _condition(controls, record)
    outcome = 'true' if allowed else 'false'
    return {'record': copy.deepcopy(record),
            'true': {'allow': allowed, 'reason': outcome}, 'false': {'allow': not allowed, 'reason': outcome}}
