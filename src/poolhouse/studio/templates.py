"""Built-in composable Studio workflows."""
from __future__ import annotations

from copy import deepcopy

from poolhouse.studio.decision_templates import templates as decision_templates
from poolhouse.studio.rag_template import template as rag_template


def _graph(name: str, types: list[tuple[str, str]], connections: list[tuple[str, str, str, str]]) -> dict:
    return {'id': name, 'name': name.replace('-', ' ').title(), 'schema_version': 1,
            'nodes': [{'id': key, 'type': kind, 'version': 1, 'parameters': {}, 'bindings': {}}
                      for key, kind in types],
            'edges': [{'source': {'node': a, 'port': b}, 'target': {'node': c, 'port': d}}
                      for a, b, c, d in connections],
            'groups': {}, 'layout': {'nodes': {key: {'x': i * 270, 'y': 100}
                                               for i, (key, _) in enumerate(types)}}}


def templates() -> list[dict]:
    """Return editable lifecycle templates with unresolved local resource bindings."""
    training = _graph('fine-tuning-and-evaluation',
                      [('data', 'dataset'), ('validate', 'data.validate'), ('split', 'data.split'),
                       ('model', 'model'), ('train', 'train.tool-calls'), ('evaluate', 'evaluate'), ('chart', 'chart'), ('export', 'export'), ('report', 'report')],
                      [('data', 'dataset', 'validate', 'dataset'), ('validate', 'dataset', 'split', 'dataset'),
                       ('split', 'train', 'train', 'dataset'), ('model', 'model', 'train', 'model'), ('split', 'test', 'evaluate', 'dataset'),
                       ('train', 'model', 'evaluate', 'model'), ('evaluate', 'metrics', 'chart', 'metrics'),
                       ('train', 'model', 'export', 'model'), ('chart', 'report', 'report', 'report')])
    inference = _graph('inference', [('model', 'model'), ('prompt', 'prompt'), ('generate', 'generate')],
                       [('model', 'model', 'generate', 'model'), ('prompt', 'prompt', 'generate', 'prompt')])
    comparison = _graph('base-versus-trained',
                        [('data', 'dataset'), ('base', 'model'), ('trained', 'model'),
                         ('base-eval', 'evaluate'), ('trained-eval', 'evaluate'), ('compare', 'compare')],
                        [('data', 'dataset', 'base-eval', 'dataset'), ('data', 'dataset', 'trained-eval', 'dataset'),
                         ('base', 'model', 'base-eval', 'model'), ('trained', 'model', 'trained-eval', 'model'),
                         ('base-eval', 'metrics', 'compare', 'base'), ('trained-eval', 'metrics', 'compare', 'trained')])
    for node in comparison['nodes']:
        if node['type'] == 'evaluate':
            node['parameters'] = {'suite': 'holdout', 'recipe': 'tool-calls', 'framework': 'mlx'}
    for node in training['nodes']:
        if node['id'] == 'train':
            node['parameters'] = {'framework': 'mlx', 'lora': True}
        elif node['id'] == 'export':
            node['parameters'] = {'format': 'adapter'}
    tools = _graph('inference-and-tools', [('model', 'model'), ('prompt', 'prompt'),
                   ('generate', 'generate.structured'), ('tool', 'tools.call')],
                   [('model', 'model', 'generate', 'model'), ('prompt', 'prompt', 'generate', 'prompt'),
                    ('generate', 'response', 'tool', 'arguments')])
    for node in tools['nodes']:
        if node['id'] == 'tool':
            node['parameters'] = {'tool': 'serve_status'}
        elif node['id'] == 'prompt':
            node['parameters'] = {'template': 'Return an empty JSON object.'}
        elif node['id'] == 'generate':
            node['parameters'] = {'json_schema': '{"type":"object","properties":{},"additionalProperties":false}'}
    gym = _graph('gym-training-and-evaluation', [('train', 'gym.train'),
                 ('evaluate', 'gym.evaluate'), ('chart', 'chart')],
                 [('train', 'model', 'evaluate', 'model'), ('evaluate', 'metrics', 'chart', 'metrics')])
    gym['nodes'][0]['parameters'] = {'environment': 'warehouse', 'timesteps': 2048, 'seed': 0}
    sweep = _graph('benchmark-sweep', [('data', 'dataset'), ('model', 'model'),
                   ('evaluate', 'evaluate'), ('chart', 'chart')],
                   [('data', 'dataset', 'evaluate', 'dataset'), ('model', 'model', 'evaluate', 'model'),
                    ('evaluate', 'metrics', 'chart', 'metrics')])
    sweep['nodes'][2]['parameters'] = {'suite': 'tool-calls'}
    sweep['nodes'][2]['sweep'] = {'temperature': [0, 0.7]}
    return deepcopy([training, comparison, sweep, inference, tools, gym]) + decision_templates() + [rag_template()]

