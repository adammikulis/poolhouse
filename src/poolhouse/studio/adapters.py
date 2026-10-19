"""Fixed execution adapters for Studio nodes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from poolhouse import hub, optional
from poolhouse.credentials import child_environment
from poolhouse.files import write_json


def confined(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root) or Path(relative).is_absolute():
        raise ValueError("Resource must belong to the selected project")
    return path


def response_schema(value: Any) -> dict:
    schema = json.loads(value) if isinstance(value, str) and value else value or {'type': 'object'}
    allowed = {'type', 'properties', 'required', 'additionalProperties', 'items', 'enum',
               'minimum', 'maximum', 'minItems', 'maxItems', 'title', 'description'}

    def check(spec: Any) -> None:
        if not isinstance(spec, dict) or set(spec) - allowed:
            raise ValueError('Use an inline response schema with supported type and property rules')
        kinds = spec.get('type', [])
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if not isinstance(kinds, list) or set(kinds) - {'object', 'array', 'string', 'number', 'integer', 'boolean', 'null'}:
            raise ValueError('Response schema has an unsupported value type')
        for child in spec.get('properties', {}).values():
            check(child)
        if 'items' in spec:
            check(spec['items'])
    check(schema)
    return schema


LOCAL_TOOLS = frozenset({'serve_status', 'jobs_status', 'models_on_disk'})


def tool_catalog() -> list[dict]:
    tools = optional.module('poolhouse.do').command_tools()
    read = optional.module('poolhouse.chatpolicy').READ
    return [schema for schema, _function in tools if schema['function']['name'] in LOCAL_TOOLS & read]


def governed_tool(name: str, arguments: dict, context: dict) -> Any:
    if name not in LOCAL_TOOLS or name not in optional.module('poolhouse.chatpolicy').READ:
        raise ValueError('This tool is not available to Studio workflows')
    if not context.get('principal'):
        raise ValueError('Tool execution requires the current authenticated session')
    mcp = optional.module('poolhouse.mcp')
    tools = optional.module('poolhouse.do').command_tools()
    for schema, function in tools:
        if schema['function']['name'] == name:
            mcp.checked(function, arguments)
            return function(**arguments)
    raise ValueError('The selected tool is not installed')


class Adapters:
    """Prepare fixed workers and collect their declared structured outputs."""

    def __init__(self, runner: Any, *, process_environment: Any = child_environment, tool_call: Any = governed_tool) -> None:
        self.runner = runner
        self.process_environment = process_environment
        self.tool_call = tool_call

    def validate(self, nodes: list[dict]) -> list[dict]:
        schema_errors = []
        for node in nodes:
            if node['type'] == 'generate.structured':
                try:
                    response_schema(node.get('parameters', {}).get('json_schema'))
                except (ValueError, TypeError, AttributeError) as error:
                    schema_errors.append({'node': node['id'], 'code': 'parameter', 'message': str(error)})
        workers = [node for node in nodes if node['type'] not in ('dataset', 'model', 'prompt', 'tools.call')]
        if not workers:
            return schema_errors
        environment = self.runner.environment
        if environment is None or not environment.exists:
            return schema_errors + [{'node': node['id'], 'code': 'dependency', 'message': 'Install the managed compute runtime to run this node'} for node in workers]
        try:
            environment.require_current_runtime()
            packages = environment.installed()
        except (OSError, RuntimeError, ValueError) as error:
            return [{'node': node['id'], 'code': 'dependency', 'message': str(error)} for node in workers]
        errors = schema_errors
        for node in workers:
            kind, parameters = node['type'], node.get('parameters', {})
            required = set()
            if kind.startswith('gym.'):
                required = {'gymnasium', 'stable-baselines3'}
            elif kind in ('train.decision', 'evaluate.decision'):
                required = {'torch', 'transformers', 'peft'}
            elif kind.startswith('train.'):
                framework = parameters.get('framework') or ('mlx' if 'mlx' in packages else 'torch')
                required = {'mlx', 'mlx-lm'} if framework == 'mlx' and kind == 'train.tool-calls' else {framework}
                if kind == 'train.tool-calls' and framework == 'torch':
                    required.add('transformers')
                    if parameters.get('lora'):
                        required.add('peft')
            missing = required - set(packages)
            if missing:
                errors.append({'node': node['id'], 'code': 'dependency',
                               'message': 'Install the required compute libraries: ' + ', '.join(sorted(missing))})
        return errors

    def immediate(self, node: dict, inputs: dict, context: dict) -> dict | None:
        kind = node['type']
        if kind in ('dataset', 'model'):
            binding = context['bindings'].get(kind, {})
            if binding.get('kind') != kind or not binding.get('id'):
                raise ValueError(f"Select a {kind} resource")
            if kind == 'model':
                if Path(binding['id']).is_absolute() or '..' in Path(binding['id']).parts or binding['id'].startswith(('~', 'hf:', 'http:','https:')):
                    raise ValueError('Select a model from the local model library')
                if '/' in binding['id'] and len(binding['id'].split('/')) != 2:
                    raise ValueError('Select an installed model identifier')
                found = hub.held_snapshot(binding['id']) if '/' in binding['id'] else hub.located(binding['id'])
                if not found:
                    raise ValueError('The selected model is not installed')
                path = Path(found).resolve(strict=True)
            else:
                path = confined(Path(context['files_root']), binding['id'])
            resource = {'kind': kind, 'path': str(path), 'id': binding['id']}
            if kind == 'model' and path.is_dir() and (path / 'config.json').is_file():
                configuration = json.loads((path / 'config.json').read_text())
                if configuration.get('quantization'):
                    resource['framework'] = 'mlx'
            return {kind: resource}
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
