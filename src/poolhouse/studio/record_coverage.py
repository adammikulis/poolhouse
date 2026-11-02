"""Server validation of the direct and indirect controls required by record generation."""
from __future__ import annotations

from .record_context import generation_parameters, literal_fields


def _parameters(node: dict) -> dict:
    values = node.get('parameters', {})
    if not isinstance(values, dict):
        raise ValueError('Parameters must be an object')
    return values


def _origin(node_id: str, nodes: dict, incoming: dict) -> tuple[str, str]:
    seen = set()
    while node_id not in seen:
        seen.add(node_id)
        source = incoming.get((node_id, 'record'))
        if source is None:
            raise ValueError('Connect the same original record to both guards and generation')
        parent, port = source['node'], source['port']
        if nodes[parent]['type'] != 'guard.injection' or port != 'record':
            return parent, port
        node_id = parent
    raise ValueError('Record coverage cannot contain a cycle')


def coverage_errors(graph: dict) -> list[dict]:
    nodes = {node['id']: node for node in graph['nodes']}
    incoming = {(edge['target']['node'], edge['target']['port']): edge['source'] for edge in graph['edges']}
    errors = []
    for node in nodes.values():
        if node['type'] == 'guard.injection':
            try:
                literal_fields(_parameters(node).get('input_fields', '[]'))
            except (ValueError, TypeError) as error:
                errors.append({'node': node['id'], 'code': 'parameter', 'message': str(error)})
        if node['type'] != 'generate.record':
            continue
        try:
            controls = generation_parameters(_parameters(node))
            origin = _origin(node['id'], nodes, incoming)
            modes, guards, seen_controls = set(), set(), set()
            for connection in graph.get('controls', []):
                if connection['target']['node'] != node['id']:
                    continue
                source = connection['source']
                guard = nodes[source['node']]
                if guard['type'] != 'guard.injection':
                    continue
                identity = (guard['id'], source['port'])
                if identity in seen_controls:
                    raise ValueError('A guard control is duplicated')
                seen_controls.add(identity)
                values = _parameters(guard)
                mode = values.get('mode', 'indirect')
                expected = controls['question_field'] if mode == 'direct' else controls['sources_field']
                fields = literal_fields(values.get('input_fields', '[]'))
                if (source['port'] != 'allow' or mode not in ('direct', 'indirect')
                        or values.get('trusted_task', '') != controls['trusted_task']
                        or (fields and fields != [expected]) or _origin(guard['id'], nodes, incoming) != origin):
                    raise ValueError('Guard controls must assess the same original record, task and selected question/source fields')
                guards.add(guard['id'])
                modes.add(mode)
            if modes != {'direct', 'indirect'} or len(guards) < 2:
                raise ValueError('Connect distinct direct and indirect guard Allow controls before record generation')
        except (ValueError, TypeError, KeyError) as error:
            errors.append({'node': node['id'], 'code': 'guard_coverage', 'message': str(error)})
    return errors
