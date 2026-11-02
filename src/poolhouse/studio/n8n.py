"""Inert translation of exported n8n workflow JSON into Studio drafts."""
from __future__ import annotations

import json
import math
import re
import uuid

from poolhouse.studio.contracts import MAX_DEPTH, MAX_NODES

MAX_BYTES = 2_000_000
MAX_CONNECTIONS = 10_000
_CHANNEL = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
_TYPE = re.compile(r'^[A-Za-z0-9@_./-]{1,160}$')
_FLAGS = {'disabled', 'executeOnce', 'alwaysOutputData', 'retryOnFail', 'continueOnFail', 'onError'}


def _object(entries):
    result = {}
    for key, value in entries:
        if key in result or key in {'__proto__', 'prototype', 'constructor'}:
            raise ValueError('Duplicate or reserved JSON field.')
        result[key] = value
    return result


def _constant(_value):
    raise ValueError('Workflow values must be finite JSON.')


def _parse(text):
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Choose n8n JSON smaller than 2 MB.')
    try:
        value = json.loads(text, object_pairs_hook=_object, parse_constant=_constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('Paste one n8n workflow JSON object.') from exc
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > MAX_DEPTH:
            raise ValueError('The n8n JSON is nested too deeply.')
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError('Workflow values must be finite JSON.')
        children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else []
        pending.extend((child, depth + 1) for child in children)
    if not isinstance(value, dict) or not isinstance(value.get('nodes'), list) or not isinstance(value.get('connections'), dict):
        raise ValueError('Choose a workflow object with nodes and connections.')
    if not 1 <= len(value['nodes']) <= MAX_NODES:
        raise ValueError('The n8n workflow has an unsupported number of nodes.')
    return value


def _label(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or any(ord(c) < 32 for c in value):
        raise ValueError('Workflow and node names need short visible text.')
    return value


def _nodes(raw):
    names, identifiers = {}, set()
    for source in raw:
        if not isinstance(source, dict):
            raise ValueError('n8n nodes must be objects.')
        name = _label(source.get('name'))
        kind = source.get('type')
        if name in names or not isinstance(kind, str) or not _TYPE.fullmatch(kind):
            raise ValueError('n8n nodes need unique names and supported type identifiers.')
        ident = source.get('id')
        if ident is not None:
            if not isinstance(ident, str) or ident in identifiers:
                raise ValueError('n8n node identifiers must be unique strings.')
            identifiers.add(ident)
        version = source.get('typeVersion')
        if type(version) not in {int, float} or version <= 0 or version > 10000:
            raise ValueError('n8n nodes need finite positive type versions.')
        position = source.get('position')
        if (not isinstance(position, list) or len(position) != 2
                or any(type(v) not in {int, float} or abs(v) > 1_000_000 for v in position)):
            raise ValueError('n8n node positions must be bounded coordinates.')
        if not isinstance(source.get('parameters', {}), dict):
            raise ValueError('n8n parameters must be objects.')
        names[name] = {'source': source, 'id': uuid.uuid4().hex, 'inputs': set(), 'outputs': set()}
    return names


def _port(channel, index):
    if not isinstance(channel, str) or not _CHANNEL.fullmatch(channel) or type(index) is not int or not 0 <= index < 128:
        raise ValueError('Unsupported n8n connection type or index.')
    return f'{channel}_{index}'


def _connections(raw, names):
    edges, successors, seen = [], {name: set() for name in names}, set()
    for name, channels in raw.items():
        if name not in names or not isinstance(channels, dict):
            raise ValueError('An n8n connection references an unknown node.')
        for channel, outputs in channels.items():
            if not isinstance(outputs, list) or len(outputs) > 128:
                raise ValueError('Unsupported n8n connection outputs.')
            for index, targets in enumerate(outputs):
                output = _port(channel, index)
                if not isinstance(targets, list):
                    raise ValueError('n8n connection targets must be lists.')
                for target in targets:
                    if not isinstance(target, dict) or set(target) != {'node', 'type', 'index'} or not isinstance(target['node'], str) or target['node'] not in names:
                        raise ValueError('An n8n connection target is missing or ambiguous.')
                    incoming = _port(target['type'], target['index'])
                    names[name]['outputs'].add(output)
                    names[target['node']]['inputs'].add(incoming)
                    if len(names[name]['outputs']) > 128 or len(names[target['node']]['inputs']) > 128:
                        raise ValueError('An n8n node has too many connection ports.')
                    edge = (name, output, target['node'], incoming)
                    if edge in seen:
                        raise ValueError('An n8n connection is repeated.')
                    seen.add(edge)
                    edges.append(edge)
                    successors[name].add(target['node'])
                    if len(edges) > MAX_CONNECTIONS:
                        raise ValueError('The n8n workflow has too many connections.')
    incoming = dict.fromkeys(names, 0)
    for targets in successors.values():
        for target in targets:
            incoming[target] += 1
    ready = [name for name, count in incoming.items() if not count]
    count = 0
    while ready:
        count += 1
        for target in successors[ready.pop()]:
            incoming[target] -= 1
            if not incoming[target]:
                ready.append(target)
    if count != len(names):
        raise ValueError('Replace n8n loops with a supported finite workflow before importing.')
    return edges


def _translation(row):
    source = row['source']
    parameters = source.get('parameters', {})
    options = [key for key in _FLAGS if key in source and not
               (source[key] is None or (source[key] == 'stopWorkflow' if key == 'onError' else source[key] is False))]
    if options or row['inputs']:
        return None, 'Replace this node: its execution options or input semantics are not supported.'
    if (source['typeVersion'] == 1 and source['type'] == 'n8n-nodes-base.manualTrigger'
            and not parameters and row['outputs'] <= {'main_0'}):
        return {'type': 'stream.record', 'parameters': {'sample': '{}'}}, 'Finite empty record on Run; no live subscription.'
    if source['typeVersion'] == 1 and source['type'] == '@n8n/n8n-nodes-langchain.lmChatOllama':
        model = parameters.get('model', '')
        if (set(parameters) <= {'model', 'options'} and parameters.get('options', {}) == {}
                and isinstance(model, str) and not model.startswith('=') and '{{' not in model
                and row['outputs'] <= {'ai_languageModel_0'}):
            return {'type': 'model', 'parameters': {}}, 'Choose an installed Poolhouse model. n8n provider settings and credentials are omitted.'
    return None, 'Replace with supported Poolhouse nodes and reconnect. n8n code, expressions and operation settings are not executed.'


def preview(text: str) -> dict:
    """Return a sanitized translation preview without storing or executing content."""
    raw = _parse(text)
    names = _nodes(raw['nodes'])
    edges = _connections(raw['connections'], names)
    graph = {'nodes': [], 'edges': [], 'controls': [], 'groups': {},
             'layout': {'nodes': {}, 'viewport': {'x': 0, 'y': 0, 'zoom': 1}}}
    reports = []
    origin = [min(row['source']['position'][axis] for row in names.values()) for axis in (0, 1)]
    ports = {}
    for name, row in names.items():
        converted, message = _translation(row)
        kind = converted['type'] if converted else 'import.unsupported'
        parameters = converted['parameters'] if converted else {
            'source_type': row['source']['type'], 'source_version': row['source']['typeVersion'], 'reason': message,
            'inputs': sorted(row['inputs'] or {'main_0'}), 'outputs': sorted(row['outputs'] or {'main_0'})}
        graph['nodes'].append({'id': row['id'], 'type': kind, 'version': 1,
                               'name': name, 'parameters': parameters, 'bindings': {}})
        graph['layout']['nodes'][row['id']] = dict(zip(('x', 'y'),
            (20 + row['source']['position'][axis] - origin[axis] for axis in (0, 1)), strict=True))
        ports[name] = {'main_0': 'record'} if kind == 'stream.record' else {'ai_languageModel_0': 'model'} if kind == 'model' else {}
        reports.append({'node': row['id'], 'name': name, 'status': 'translated' if converted else 'blocked', 'message': message})
    for source, output, target, incoming in edges:
        graph['edges'].append({'source': {'node': names[source]['id'], 'port': ports[source].get(output, output)},
                               'target': {'node': names[target]['id'], 'port': incoming}})
    return {'title': _label(raw.get('name', 'Imported n8n workflow')), 'graph': graph, 'nodes': reports,
            'warnings': ['Credentials, endpoints, pinned data, execution history and unrecognized parameters are omitted. '
                         'Imported content never starts a source or executes automatically.']}
