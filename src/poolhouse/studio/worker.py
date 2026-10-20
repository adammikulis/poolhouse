"""Execute fixed Studio operations in the managed compute interpreter."""
from __future__ import annotations

import json
import random
import shutil
import sys
from pathlib import Path
from typing import Any

from poolhouse import optional
from poolhouse.client import Client, Request
from poolhouse.decide.cases import case_from
from poolhouse.decide.pointer import PointerDecider
from poolhouse.files import write_json
from poolhouse.kvtype import DEFAULT_KV_BITS
from poolhouse.serve import broker_wire, gpu
from poolhouse.studio.adapters import response_schema
from poolhouse.studio.decision_models import source as decision_source
from poolhouse.studio.decisions import assess, parse_record, route
from poolhouse.studio.resources import (
    ADAPTER_FILES,
    checkpoint_base,
    materialize_checkpoint,
    training_base,
)


def rows_of(value: dict) -> list[dict]:
    path = Path(value['path'])
    files = sorted(path.rglob('*.jsonl')) if path.is_dir() else [path]
    rows = []
    for file in files:
        for line in file.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError('Dataset rows must be objects')
                rows.append(row)
    if not rows:
        raise ValueError('Dataset has no examples')
    return rows


def dataset(out: Path, name: str, rows: list[dict]) -> dict:
    target = out / name
    target.mkdir()
    (target / 'data.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    return {'kind': 'dataset', 'path': str(target), 'rows': len(rows)}


def _decision(kind: str, parameters: dict, inputs: dict, out: Path) -> dict:
    cases = [case_from(row) for row in rows_of(inputs['dataset'])]
    if kind == 'train.decision':
        trainer = optional.module('poolhouse.train.decider')
        settings = trainer.Settings(base=Path(inputs['model']['path']), **{**parameters, 'wait_s': None})
        result = trainer.train(cases, out / 'decision', settings)
        return {'model': {'kind': 'model', 'path': str(result.out), 'decision': True},
                'metrics': result.metrics, 'checkpoint': {'kind': 'artifact', 'path': str(result.out)}}
    with gpu.hold('Studio decision evaluation', wait_s=None):
        model = PointerDecider(Path(inputs['model']['path']))
        decisions = model.decide_many([case.request() for case in cases])
    accuracy = sum(answer.choice == case.label for answer, case in zip(decisions, cases, strict=True)) / len(cases)
    metrics = {'accuracy': accuracy, 'examples': len(cases)}
    return {'metrics': metrics, 'report': metrics}


def _predict_record(kind: str, parameters: dict, inputs: dict, out: Path) -> dict:
    resolved, identity = decision_source(inputs['model'], out)
    def predict(question, state, options):
        with gpu.hold('Studio record decision', wait_s=None):
            return PointerDecider(resolved, download=False).decide(question, state, options)
    return assess(kind, parameters, inputs['record'], predict, model_digest=identity['model_digest'])


def _train(kind: str, parameters: dict, inputs: dict, out: Path) -> dict:
    recipe = kind.removeprefix('train.')
    config = {k: v for k, v in parameters.items() if k not in ('mode', 'base')}
    if recipe == 'tool-calls':
        if not inputs.get('model'):
            raise ValueError('Select an authorized base model for fine-tuning')
        config['base'] = str(training_base(inputs['model'], parameters))
    recipes = optional.module('poolhouse.train.recipes')
    config = recipes.validate(recipe, config)
    smoke = parameters.get('mode') == 'smoke'
    with gpu.hold('Studio ' + recipe, wait_s=None):
        result = optional.module('poolhouse.train.run').run(
            recipe, config, Path(inputs['dataset']['path']), out / 'training', dry=smoke)
    outputs = {'metrics': result}
    if not smoke and result.get('checkpoint'):
        outputs['checkpoint'] = {'kind': 'artifact', 'path': result['checkpoint']}
        model = materialize_checkpoint(Path(result['checkpoint']), out / 'training', out / 'model')
        outputs['model'] = {'kind': 'model', 'path': str(model), 'recipe': recipe}
    return outputs


def _checkpoint_evaluate(inputs: dict, parameters: dict) -> dict:
    recipes = optional.module('poolhouse.train.recipes')
    trained = bool(inputs['model'].get('recipe')) or (Path(inputs['model']['path']) / 'state.json').is_file()
    base = checkpoint_base(inputs['model']) if trained else None
    with gpu.hold('Studio checkpoint evaluation', wait_s=None):
        if trained:
            settings = {'base_override': base} if base is not None else {}
            built = recipes.load(inputs['model']['path'], data=Path(inputs['dataset']['path']), **settings)
        else:
            path = Path(inputs['model']['path'])
            if not path.is_dir():
                raise ValueError('Supervised holdout evaluation requires a local model checkpoint directory')
            recipe = parameters.get('recipe', 'tool-calls')
            framework = parameters.get('framework') or inputs['model'].get('framework') or 'torch'
            config = {'framework': framework}
            if recipe == 'tool-calls':
                config.update(base=str(path), lora=framework == 'mlx')
            built = recipes.build(recipe, config, Path(inputs['dataset']['path']))
        batches = built.eval_batches or built.batches
        if batches is None:
            raise ValueError('Evaluation dataset produced no batches')
        step = built.step or optional.module('poolhouse.train.step').step_for(built.model, built.optimizer, built.loss)
        examples = int(built.config.get('holdout_rows') or built.config.get('rows') or 1)
        batch_count = (examples + int(built.config['batch_size']) - 1) // int(built.config['batch_size'])
        loss = sum(float(step.eval_loss(batches(index))) for index in range(batch_count)) / batch_count
        metrics = {'loss': loss, 'examples': examples, 'batches': batch_count}
        if built.predict is not None and built.config.get('recipe') == 'classify-text':
            rows = rows_of(inputs['dataset'])
            metrics['accuracy'] = sum(built.predict(row['text'])['label'] == row['label'] for row in rows) / len(rows)
            metrics['examples'] = len(rows)
    return {'metrics': metrics, 'report': metrics}


def _native_generate(inputs: dict, parameters: dict, structured: bool) -> dict:
    schema = response_schema(parameters.get('json_schema')) if structured else None
    base = checkpoint_base(inputs['model'], framework='mlx')
    if inputs['model'].get('recipe') != 'tool-calls':
        raise ValueError('Trained generation requires an MLX conversation model')
    with gpu.hold('Studio trained model generation', wait_s=None):
        built = optional.module('poolhouse.train.recipes').load(inputs['model']['path'], base_override=base)
        if built.config.get('framework') != 'mlx' or built.config.get('recipe') != 'tool-calls':
            raise ValueError('Trained generation requires an MLX conversation model')
        tokenizer = optional.module('transformers').AutoTokenizer.from_pretrained(
            built.config['base'], local_files_only=True, trust_remote_code=False)
        prompt = tokenizer.apply_chat_template([{'role': 'user', 'content': inputs['prompt']}],
                                               tokenize=False, add_generation_prompt=True)
        sampler = optional.module('mlx_lm.sample_utils').make_sampler(temp=parameters.get('temperature', .7))
        text = optional.module('mlx_lm').generate(built.model, tokenizer, prompt=prompt,
                        max_tokens=parameters.get('max_tokens', 512), sampler=sampler,
                        kv_bits=DEFAULT_KV_BITS, quantized_kv_start=0)
    if structured:
        response = json.loads(text)
        errors = optional.module('poolhouse.agent.schema').validate(response, schema)
        if errors:
            raise ValueError('Structured response did not match its schema: ' + '; '.join(errors))
        return {'text': text, 'response': response}
    return {'text': text, 'response': {'text': text, 'tool_calls': []}}


def _generate(inputs: dict, parameters: dict, evaluate: bool = False, structured: bool = False) -> dict:
    schema = response_schema(parameters.get('json_schema')) if structured else None
    grant = broker_wire.lease('studio', [inputs['model']['path']], reason='Studio inference')
    try:
        client = Client(grant.base_url, model=grant.model, request=Request(temperature=parameters.get('temperature', 0))) if evaluate else Client(grant.base_url, model=grant.model)
        if evaluate:
            rows = rows_of(inputs['dataset'])
            metrics = optional.module('poolhouse.train.tools.evaluate').evaluate(client, rows)
            return {'metrics': metrics, 'report': metrics}
        extra = {}
        if structured:
            extra['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'response', 'strict': True, 'schema': schema}}
        reply = client.chat([{'role': 'user', 'content': inputs['prompt']}],
                            temperature=parameters.get('temperature', 0.7), max_tokens=parameters.get('max_tokens', 512), **extra)
        if structured:
            response = json.loads(reply.content or '')
            errors = optional.module('poolhouse.agent.schema').validate(response, schema)
            if errors:
                raise ValueError('Structured response did not match its schema: ' + '; '.join(errors))
            return {'text': reply.content, 'response': response}
        return {'text': reply.content, 'response': {'text': reply.content, 'tool_calls': reply.tool_calls}}
    finally:
        broker_wire.release(grant.lease)


def _prepare_data(kind: str, parameters: dict, inputs: dict, out: Path) -> dict:
    if kind == 'data.synthesize':
        source = rows_of(inputs['dataset'])
        tools = source[0].get('tools', [])
        grant = broker_wire.lease('studio', [inputs['model']['path']], reason='Studio data synthesis')
        try:
            client = Client(grant.base_url, model=grant.model)
            rows = optional.module('poolhouse.train.tools.synthesise').synthesise(
                tools, ask=lambda prompt: client.chat([{'role': 'user', 'content': prompt}]).content or '')
        finally:
            broker_wire.release(grant.lease)
        target = out / 'synthesized'
        optional.module('poolhouse.train.tools.dataset').write_dataset(target, rows, base=inputs['model']['path'])
        return {'dataset': {'kind': 'dataset', 'path': str(target), 'rows': len(rows)}}
    if kind == 'data.harvest':
        source = inputs.get('traces') or inputs.get('dataset')
        if not source:
            raise ValueError('Select authorized benchmark traces')
        rows = optional.module('poolhouse.train.tools.from_bench').from_bench(rows_of(source))
        return {'dataset': dataset(out, 'harvested', rows)}
    if kind == 'gym.recordings':
        trajectory, reviews = inputs.get('trajectory'), inputs.get('reviews')
        if not trajectory or not reviews:
            raise ValueError('Select a trajectory and its explicit reviews')
        target = out / 'reviewed.jsonl'
        count = optional.module('poolhouse.gym.recordings').export_reviewed(trajectory['path'], reviews['path'], target)
        return {'dataset': {'kind': 'dataset', 'path': str(target), 'rows': count}}
    if kind.startswith('gym.'):
        return _gym(kind, parameters, inputs, out)
    raise ValueError('Unknown data operation')


def _gym(kind: str, parameters: dict, inputs: dict, out: Path) -> dict:
    if kind == 'gym.train':
        rows = rows_of(inputs['dataset'])
        environment = parameters.get('environment') or rows[0].get('environment')
        if not environment:
            raise ValueError('Select the Gym environment for this recording')
        result = optional.module('poolhouse.gym.training').train(environment,
                    timesteps=parameters.get('timesteps', 2048), seed=parameters.get('seed', 0))
        source = Path(result['checkpoint'])
        target = out / 'policy.zip'
        shutil.copyfile(source, target)
        public = {k: v for k, v in result.items() if k != 'checkpoint'}
        write_json(out / 'policy-manifest.json', public)
        return {'model': {'kind': 'model', 'path': str(target), 'gym': True}, 'metrics': public}
    if kind == 'gym.evaluate':
        model = inputs['model']
        if not model.get('gym'):
            raise ValueError('Select a trained Gym policy')
        training = optional.module('poolhouse.gym.training')
        original, manifest = training.matching_policy(model['path'], out.parent.parent)
        metrics = training.evaluate(manifest['environment'], original,
                    config=manifest.get('config'), episodes=parameters.get('episodes', 5))
        return {'metrics': metrics, 'report': metrics}
    raise ValueError('Unknown Gym operation')


def _export(model: dict, format: str, out: Path) -> dict:
    source = Path(model['path'])
    if not source.is_dir() or not model.get('recipe'):
        raise ValueError('Export requires a checkpoint-producing training result')
    if format == 'adapter':
        source = source / 'adapter'
        allowed = ADAPTER_FILES
    elif format == 'checkpoint':
        checkpoints = optional.module('poolhouse.train.checkpoint')
        checkpoint = source if checkpoints.is_valid(source) else checkpoints.find_latest(source)
        if checkpoint is None:
            raise ValueError('The training result has no completed checkpoint')
        source = Path(checkpoint)
        allowed = {'model.safetensors', 'state.json'}
    else:
        raise ValueError('Unsupported export format')
    target = out / 'export'
    target.mkdir()
    copied = []
    for name in sorted(allowed):
        path = source / name
        if not path.exists():
            continue
        info = path.lstat()
        if path.is_symlink() or not path.is_file() or info.st_nlink != 1:
            raise ValueError('Export files must be regular owned training artifacts')
        shutil.copyfile(path, target / name)
        copied.append(name)
    if not any(name.endswith('.safetensors') for name in copied):
        raise ValueError('This training result has no weights in the selected format')
    write_json(target / 'export.json', {'format': format, 'files': copied})
    return {'artifact': {'kind': 'artifact', 'path': str(target)}}


def execute(node: dict, inputs: dict, out: Path) -> dict[str, Any]:
    kind = node['type']
    parameters = node.get('parameters', {})
    if kind == 'decision.route':
        return route(parameters, inputs['decision'])
    if kind == 'stream.record':
        return {'record': inputs['record'] if 'record' in inputs else parse_record(parameters.get('sample', '{}'))}
    if kind in ('decision.predict', 'guard.injection'):
        return _predict_record(kind, parameters, inputs, out)
    if kind in ('train.decision', 'evaluate.decision'):
        return _decision(kind, parameters, inputs, out)
    if kind.startswith('train.'):
        return _train(kind, parameters, inputs, out)
    if kind.startswith('gym.') or kind in ('data.synthesize', 'data.harvest'):
        return _prepare_data(kind, parameters, inputs, out)
    if kind == 'data.validate':
        rows = rows_of(inputs['dataset'])
        return {'dataset': inputs['dataset'], 'report': {'examples': len(rows), 'valid': True}}
    if kind == 'data.split':
        rows = rows_of(inputs['dataset'])
        random.Random(parameters.get('seed', 0)).shuffle(rows)  # noqa: S311 - reproducible data split
        cut = int(len(rows) * parameters.get('fraction', .8))
        if not 0 < cut < len(rows):
            raise ValueError('Split needs examples in both partitions')
        return {'train': dataset(out, 'train', rows[:cut]), 'test': dataset(out, 'test', rows[cut:])}
    if kind == 'evaluate' and (inputs['model'].get('recipe') or (Path(inputs['model']['path']) / 'state.json').is_file()
                               or parameters.get('suite', 'holdout') == 'holdout'):
        return _checkpoint_evaluate(inputs, parameters)
    if kind in ('generate', 'generate.structured') and (inputs['model'].get('recipe') or (Path(inputs['model']['path']) / 'state.json').is_file()):
        return _native_generate(inputs, parameters, structured=kind == 'generate.structured')
    if kind in ('generate', 'generate.structured', 'evaluate'):
        return _generate(inputs, parameters, evaluate=kind == 'evaluate', structured=kind == 'generate.structured')
    if kind == 'compare':
        base, trained = inputs['base'], inputs['trained']
        delta = {k: trained[k] - v for k, v in base.items()
                 if isinstance(v, (int, float)) and isinstance(trained.get(k), (int, float))}
        report = {'base': base, 'trained': trained, 'delta': delta}
        return {'metrics': delta, 'report': report}
    if kind == 'chart':
        return {'report': {'metrics': inputs['metrics']}}
    if kind == 'report':
        path = out / 'report.json'
        write_json(path, inputs['report'])
        return {'artifact': {'kind': 'artifact', 'path': str(path), 'media_type': 'application/json'}}
    if kind == 'export':
        return _export(inputs['model'], parameters.get('format', 'checkpoint'), out)
    raise ValueError(f'Node {kind} requires an installed execution integration')


def run() -> int:
    request = json.loads(Path(sys.argv[1]).read_text())
    out = Path(request['output_dir'])
    outputs = execute(request['node'], request['inputs'], out)
    write_json(out / 'result.json', {'outputs': outputs})
    return 0


if __name__ == '__main__':
    raise SystemExit(run())
