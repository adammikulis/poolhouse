"""Fixed execution adapters for Studio nodes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from poolhouse import hub, optional
from poolhouse.credentials import child_environment
from poolhouse.files import write_json
from poolhouse.home import machine_id
from poolhouse.serve.python_requirements import missing_mlx

from . import record_ops
from .catalog import node_spec
from .decision_models import source as decision_source
from .decisions import parameters as decision_parameters, parse_record, route
from .record_context import generation_context, generation_parameters
from .resources import model_resource, training_base


def needs_compute(node: dict) -> bool:
    kind = node["type"]
    return (kind.startswith(("train.", "gym.")) or kind in {"data.synthesize", "data.harvest", "decision.predict", "guard.injection"}
            or (kind == "evaluate" and node.get("parameters", {}).get("suite", "holdout") == "holdout")
)


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


def _parameter_errors(nodes: list[dict]) -> list[dict]:
    schema_errors = []
    for node in nodes:
        if node['type'] in {'record.map', 'record.if'}:
            try:
                record_ops.parameters(node['type'], node.get('parameters', {}).get('controls'))
            except (ValueError, TypeError) as error:
                schema_errors.append({'node': node['id'], 'code': 'parameter', 'message': str(error)})
        if node['type'] in ('decision.predict', 'guard.injection', 'decision.route'):
            try:
                decision_parameters(node['type'], node.get('parameters', {}))
            except (ValueError, TypeError, AttributeError) as error:
                schema_errors.append({'node': node['id'], 'code': 'parameter', 'message': str(error)})
        if node['type'] == 'generate.record':
            try:
                generation_parameters(node.get('parameters', {}))
            except (ValueError, TypeError) as error:
                schema_errors.append({'node': node['id'], 'code': 'parameter', 'message': str(error)})
        if node['type'] == 'generate.structured':
            try:
                response_schema(node.get('parameters', {}).get('json_schema'))
            except (ValueError, TypeError, AttributeError) as error:
                schema_errors.append({'node': node['id'], 'code': 'parameter', 'message': str(error)})
    return schema_errors


class Adapters:
    """Prepare fixed workers and collect their declared structured outputs."""

    def __init__(self, runner: Any, *, process_environment: Any = child_environment, tool_call: Any = governed_tool, model_resolver: Any = None) -> None:
        self.runner = runner
        self.process_environment = process_environment
        self.tool_call = tool_call
        self.model_resolver = model_resolver or (lambda binding, context: model_resource(binding, hub.discover()))

    def dependency_fingerprint(self, node: dict) -> str:
        kind = node['type']
        required = needs_compute(node) or kind == 'evaluate.decision'
        native = required or kind in {'generate', 'generate.structured', 'generate.record', 'evaluate', 'export'}
        if not native:
            return ''
        environment = self.runner.environment
        if environment is None or not environment.exists:
            if required:
                raise OSError('Install Training essentials in Libraries to run this node')
            return 'unavailable'
        evidence = environment.dependency_metadata()
        if evidence['error'] and required:
            raise OSError(evidence['error'])
        companion = evidence['companion'] or 'unavailable'
        packages = evidence['packages']
        names = {'numpy', 'safetensors', 'mlx', 'mlx-lm', 'torch', 'transformers',
                 'tokenizers', 'peft', 'huggingface-hub', 'mlx-vlm'}
        if kind.startswith('gym.'):
            names |= {'gymnasium', 'stable-baselines3', 'metadrive-simulator', 'rware', 'sumo-rl', 'pyflyt'}
        parameters = node.get('parameters', {})
        required_names = set()
        if kind.startswith('train.'):
            framework = parameters.get('framework') or ('mlx' if 'mlx' in packages else 'torch')
            required_names = {framework}
            if kind == 'train.tool-calls':
                required_names |= {'mlx-lm'} if framework == 'mlx' else {'transformers'}
            if kind == 'train.decision':
                required_names = {'torch', 'transformers', 'peft'}
        elif kind in ('evaluate.decision', 'decision.predict', 'guard.injection'):
            required_names = {'torch', 'transformers', 'peft'}
        elif kind.startswith('gym.'):
            required_names = {'gymnasium', 'stable-baselines3'}
        missing = {name for name in required_names if not packages.get(name)}
        if missing:
            raise OSError('Install the required compute libraries: ' + ', '.join(sorted(missing)))
        payload = {'companion': companion,
                   'versions': {name: packages.get(name) or 'unavailable' for name in sorted(names)}}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def cacheable_outputs(self, node: dict, outputs: dict) -> bool:
        if node['type'] == 'data.hf-preview':
            return False
        return node['type'] != 'guard.injection' or outputs.get('signal', {}).get('coverage') == 'complete'

    def availability(self, specs: list[dict]) -> dict[str, list[str]]:
        nodes = [{'id': spec['type'], 'type': spec['type'], 'parameters': {}}
                 for spec in specs]
        issues = self.validate(nodes)
        return {node['type']: [issue['message'] for issue in issues if issue['node'] == node['id']]
                for node in nodes}

    def validate_resources(self, graph: dict, context: dict | None = None) -> list[dict]:
        errors, resources = [], {}
        for node in graph['nodes']:
            if node['type'] == 'model':
                try:
                    resources[node['id']] = self.model_resolver(node.get('bindings', {}).get('model', {}), context or {})
                except (ValueError, OSError) as error:
                    errors.append({'node': node['id'], 'code': 'resource', 'message': str(error)})
        for edge in graph['edges']:
            resource = resources.get(edge['source']['node'])
            if not resource or 'studio_type' not in resource:
                continue
            target = next(node for node in graph['nodes'] if node['id'] == edge['target']['node'])
            port = next(port for port in node_spec(target['type'], target.get('version', 1))['inputs']
                        if port['name'] == edge['target']['port'])
            if resource['studio_type'] not in port.get('accepted_types', [port['type']]):
                errors.append({'node': target['id'], 'code': 'resource',
                               'message': 'The selected checkpoint type is incompatible with this model input'})
        for node in graph['nodes']:
            producer = next((edge['source']['node'] for edge in graph['edges']
                             if edge['target'] == {'node': node['id'], 'port': 'model'}), None)
            if (producer in resources and _mlx_serving(node, resources[producer])
                    and not node.get('execution', {}).get('certificate')
                    and node.get('execution', {}).get('machine', machine_id()) == machine_id()):
                environment = self.runner.environment
                missing = missing_mlx(environment.installed()) if environment and environment.exists else ['managed runtime']
                if missing:
                    errors.append({'node': node['id'], 'code': 'dependency',
                                   'message': 'Install MLX model serving in Libraries: ' + ', '.join(missing)})
        for node in graph['nodes']:
            if node['type'] != 'train.tool-calls':
                continue
            source = next((edge['source']['node'] for edge in graph['edges']
                           if edge['target'] == {'node': node['id'], 'port': 'model'}), None)
            try:
                if source not in resources:
                    raise ValueError('Select an installed library base model for fine-tuning')
                training_base(resources[source], node.get('parameters', {}))
            except (ValueError, OSError) as error:
                errors.append({'node': node['id'], 'code': 'resource', 'message': str(error)})
        for node in graph['nodes']:
            if node['type'] in ('decision.predict', 'guard.injection'):
                producer = next((edge['source']['node'] for edge in graph['edges']
                                 if edge['target'] == {'node': node['id'], 'port': 'model'}), None)
                if producer in resources:
                    try:
                        decision_source(resources[producer], None)
                    except (ValueError, OSError, TypeError, KeyError) as error:
                        errors.append({'node': node['id'], 'code': 'resource', 'message': str(error)})
            if node['type'] == 'decision.route':
                producer = next((edge['source']['node'] for edge in graph['edges']
                                 if edge['target'] == {'node': node['id'], 'port': 'decision'}), None)
                prediction = next((item for item in graph['nodes'] if item['id'] == producer and item['type'] == 'decision.predict'), None)
                if prediction:
                    try:
                        options = decision_parameters('decision.predict', prediction.get('parameters', {}))['options']
                        if decision_parameters('decision.route', node.get('parameters', {}))['label'] not in options:
                            raise ValueError('Select a label declared by the decision')
                    except (ValueError, TypeError) as error:
                        errors.append({'node': node['id'], 'code': 'parameter', 'message': str(error)})
        return errors

    def validate(self, nodes: list[dict]) -> list[dict]:
        schema_errors = _parameter_errors(nodes)
        workers = [node for node in nodes if node['type'] not in ('dataset', 'model', 'prompt', 'tools.call', 'stream.record', 'decision.route', 'record.map', 'record.if', 'data.hf-preview')]
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
        compute_error = ''
        if any(needs_compute(node) for node in workers):
            try:
                environment.require_compute()
            except (OSError, RuntimeError, ValueError) as error:
                compute_error = str(error)
        for node in workers:
            if compute_error and needs_compute(node):
                errors.append({'node': node['id'], 'code': 'dependency', 'message': compute_error})
            kind, parameters = node['type'], node.get('parameters', {})
            required = set()
            if kind.startswith('gym.'):
                required = {'gymnasium', 'stable-baselines3'}
            elif kind in ('train.decision', 'evaluate.decision', 'decision.predict', 'guard.injection'):
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
        if kind in {'record.map', 'record.if'}:
            return record_ops.execute(kind, node['parameters']['controls'], inputs['record'])
        if kind == 'decision.route':
            return route(node.get('parameters', {}), inputs['decision'])
        if kind == 'stream.record':
            return {'record': inputs['record'] if 'record' in inputs else parse_record(node.get('parameters', {}).get('sample', '{}'))}
        if kind == 'data.hf-preview':
            importer = getattr(self.runner, 'studio_dataset_import', None)
            if importer is None:
                raise ValueError('Dataset imports are unavailable')
            return importer(node, context)
        if kind in ('dataset', 'model'):
            binding = context['bindings'].get(kind, {})
            if binding.get('kind') != kind or not binding.get('id'):
                raise ValueError(f"Select a {kind} resource")
            if kind == 'model':
                return {'model': self.model_resolver(binding, context)}
            else:
                return {'dataset': self._dataset_resource(binding, context)}
        if kind == 'prompt':
            return {'prompt': node.get('parameters', {}).get('template', '')}
        return None

    def _dataset_resource(self, binding, context):
        resolver = getattr(self.runner, 'studio_dataset_resource', None)
        resource = resolver(binding, context) if resolver else None
        if resource is not None:
            return resource
        if binding.get('id', '').startswith('datasets/huggingface/'):
            raise ValueError('This imported dataset is unavailable')
        path = confined(Path(context['files_root']), binding['id'])
        return {'kind': 'dataset', 'path': str(path), 'id': binding['id']}

    def supports_authorized(self, node: dict) -> bool:
        return node['type'] == 'tools.call'

    def execute_authorized(self, node: dict, inputs: dict, context: dict) -> dict:
        if self.tool_call is None:
            raise ValueError('Select an authorized tool integration')
        return {'result': self.tool_call(node.get('parameters', {}).get('tool', ''), inputs['arguments'], context)}

    def prepare(self, node: dict, inputs: dict, context: dict) -> dict:
        if node['type'] == 'generate.record':
            generation_context(node.get('parameters', {}), inputs['record'])
        environment = self.runner.environment
        if environment is None or not environment.exists:
            raise ValueError("Install the managed compute runtime before running this node")
        environment.require_current_runtime()
        if _mlx_serving(node, inputs.get('model', {})):
            missing = missing_mlx(environment.installed())
            if missing:
                raise ValueError('Install MLX model serving in Libraries: ' + ', '.join(missing))
        if needs_compute(node) or inputs.get("model", {}).get("recipe"):
            environment.require_compute()
        inputs = dict(inputs)
        if node['type'] == 'train.tool-calls' and inputs.get('model'):
            current = self.model_resolver(inputs['model'], context)
            if current['path'] != inputs['model']['path']:
                raise ValueError('The selected base model changed; validate the workflow again')
            training_base(current, node.get('parameters', {}))
            inputs['model'] = current
        for port, binding in context.get('bindings', {}).items():
            if port not in inputs and binding.get('kind') == 'dataset':
                inputs[port] = self._dataset_resource(binding, context)
        out = Path(context['output_dir'])
        out.mkdir(parents=True, exist_ok=True)
        request = out / 'request.json'
        content = {'node': node, 'inputs': inputs, 'output_dir': str(out)}
        if context.get('resident_decision') is True:
            content['execution'] = {'kind': 'resident_decision'}
        write_json(request, content)
        return {'argv': [str(environment.python), '-B', '-I', '-m', 'poolhouse.studio.worker', str(request)],
                'cwd': str(out), 'env': self.process_environment()}

    def collect(self, node: dict, job: Any, context: dict) -> dict:
        result = Path(context['output_dir']) / 'result.json'
        if not result.is_file():
            raise ValueError('The worker did not produce a structured result')
        value = json.loads(result.read_text())
        if not isinstance(value, dict) or not isinstance(value.get('outputs'), dict):
            raise ValueError('Invalid worker result')
        return value['outputs']


def _mlx_serving(node: dict, model: dict) -> bool:
    if model.get('format') != 'mlx' or model.get('recipe'):
        return False
    return node['type'] in {'generate', 'generate.structured', 'generate.record', 'data.synthesize'} or (
        node['type'] == 'evaluate' and node.get('parameters', {}).get('suite', 'holdout') != 'holdout')
