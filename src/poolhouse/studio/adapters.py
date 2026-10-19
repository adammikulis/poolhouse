"""Fixed execution adapters for Studio nodes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from poolhouse import hub
from poolhouse.credentials import child_environment
from poolhouse.files import write_json


def confined(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root) or Path(relative).is_absolute():
        raise ValueError("Resource must belong to the selected project")
    return path


class Adapters:
    """Prepare fixed workers and collect their declared structured outputs."""

    def __init__(self, runner: Any, *, process_environment: Any = child_environment, tool_call: Any = None) -> None:
        self.runner = runner
        self.process_environment = process_environment
        self.tool_call = tool_call

    def immediate(self, node: dict, inputs: dict, context: dict) -> dict | None:
        kind = node['type']
        if kind in ('dataset', 'model'):
            binding = context['bindings'].get(kind, {})
            if binding.get('kind') != kind or not binding.get('id'):
                raise ValueError(f"Select a {kind} resource")
            if kind == 'model':
                if Path(binding['id']).is_absolute() or '..' in Path(binding['id']).parts:
                    raise ValueError('Select a model from the local model library')
                found = hub.located(binding['id'])
                if not found:
                    raise ValueError('The selected model is not installed')
                path = Path(found).resolve(strict=True)
            else:
                path = confined(Path(context['files_root']), binding['id'])
            return {kind: {'kind': kind, 'path': str(path), 'id': binding['id']}}
        if kind == 'prompt':
            return {'prompt': node.get('parameters', {}).get('template', '')}
        return None

    def supports_authorized(self, node: dict) -> bool:
        return node['type'] == 'tools.call'

    def execute_authorized(self, node: dict, inputs: dict, context: dict) -> dict:
        if self.tool_call is None:
            raise ValueError('Select an authorized tool integration')
        return {'result': self.tool_call(node.get('parameters', {}).get('tool', ''), inputs['arguments'], context)}

    def prepare(self, node: dict, inputs: dict, context: dict) -> dict:
        environment = self.runner.environment
        if environment is None or not environment.exists:
            raise ValueError("Install the managed compute runtime before running this node")
        environment.require_current_runtime()
        inputs = dict(inputs)
        for port, binding in context.get('bindings', {}).items():
            if port not in inputs and binding.get('kind') == 'dataset':
                path = confined(Path(context['files_root']), binding['id'])
                inputs[port] = {'kind': 'dataset', 'path': str(path)}
        out = Path(context['output_dir'])
        out.mkdir(parents=True, exist_ok=True)
        request = out / 'request.json'
        write_json(request, {'node': node, 'inputs': inputs, 'output_dir': str(out)})
        return {'argv': [str(environment.python), '-I', '-m', 'poolhouse.studio.worker', str(request)],
                'cwd': str(out), 'env': self.process_environment()}

    def collect(self, node: dict, job: Any, context: dict) -> dict:
        result = Path(context['output_dir']) / 'result.json'
        if not result.is_file():
            raise ValueError('The worker did not produce a structured result')
        value = json.loads(result.read_text())
        if not isinstance(value, dict) or not isinstance(value.get('outputs'), dict):
            raise ValueError('Invalid worker result')
        return value['outputs']
