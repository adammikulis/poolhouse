"""Resolve a decision model from fixed pins or explicit server producer lineage."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from poolhouse.decide.pins import STRANDS_V19
from poolhouse.decide.sources import FORMAT, Source, valid_temperature
from poolhouse.files import write_json, writing

from .decision_inventory import RESOURCE_ID, _digest, _hash, _json, _member, _pinned
from .model_content import open_model_file
from .resources import base_fingerprint, model_fingerprint

ASSETS = ('head.safetensors', 'lora/adapter_model.safetensors', 'lora/adapter_config.json')
ADAPTER_FIELDS = frozenset({'peft_type', 'task_type', 'r', 'lora_alpha', 'lora_dropout', 'target_modules',
                            'bias', 'fan_in_fan_out', 'inference_mode', 'use_rslora', 'use_dora', 'lora_bias'})


def _clean(value: dict) -> dict:
    targets = value.get('target_modules')
    if not isinstance(targets, list) or not targets or len(targets) > 256 or any(not isinstance(name, str) or not re.fullmatch('[A-Za-z0-9_.]+', name) or len(name) > 200 for name in targets):
        raise ValueError('Decision adapters require named local target modules')
    for name, low, high in (('r', 1, 4096), ('lora_alpha', 0.000001, 1_000_000), ('lora_dropout', 0, 1)):
        item = value.get(name)
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not low <= item <= high:
            raise ValueError('Decision adapter controls are invalid')
    return {key: item for key, item in value.items() if key in ADAPTER_FIELDS}


def _copy(source: Path, target: Path, expected: str) -> None:
    if target.is_symlink() or (target.exists() and target.stat().st_nlink != 1):
        raise ValueError('Decision runtime files must be owned regular files')
    member = _member(source)
    member.update(size=source.stat().st_size, blob_digest=expected)
    with writing(target) as pending, pending.open('wb') as destination, open_model_file(member) as stream:
        shutil.copyfileobj(stream, destination)


def _adapter(output_dir: Path, adapter: dict, weights: Path, expected: str) -> Path:
    adapter = _clean(adapter)
    output_dir.mkdir(parents=True, exist_ok=True)
    adapter_dir = output_dir / 'decision-adapter'
    if any(path.is_symlink() for path in (adapter_dir, *adapter_dir.parents)):
        raise ValueError('Decision runtime output cannot contain links')
    adapter_dir.mkdir(exist_ok=True)
    for name in ('adapter_config.json', 'adapter_model.safetensors'):
        target = adapter_dir / name
        if target.is_symlink() or (target.exists() and target.stat().st_nlink != 1):
            raise ValueError('Decision runtime files must be owned regular files')
    write_json(adapter_dir / 'adapter_config.json', _clean(adapter))
    _copy(weights, adapter_dir / 'adapter_model.safetensors', expected)
    return adapter_dir


def _runtime_assets(output_dir, adapter, assets):
    (weights, weights_digest), (head, head_digest) = assets
    directory = _adapter(output_dir, adapter, weights, weights_digest)
    owned_head = output_dir / 'decision-head.safetensors'
    _copy(head, owned_head, head_digest)
    return owned_head, directory


def source(model: dict, output_dir: Path | None) -> tuple[Source | None, dict]:
    """Return an internal Source; callers must already authorize this model and base.

    This function grants no authority and never uses serialized model or tokenizer locators.
    """
    if model.get('id') == RESOURCE_ID or model.get('fixed_source') == 'staged-strands-v19':
        paths, cfg, base, tokenizer = _pinned(model)
        head = paths['head.safetensors']
        adapter = _json(paths['lora/adapter_config.json'])
        weights = paths['lora/adapter_model.safetensors']
        weights_digest = next(pin.sha256 for pin in STRANDS_V19.files if pin.filename == 'lora/adapter_model.safetensors')
        head_digest = next(pin.sha256 for pin in STRANDS_V19.files if pin.filename == 'head.safetensors')
        temperature = cfg.get('temperature_by_kind', {}).get('choice', cfg.get('temperature', 1))
        model_digest = _digest([[pin.repo, pin.revision, pin.filename, pin.sha256, pin.size] for pin in STRANDS_V19.files])
        base_digest = _digest([[pin.filename, pin.sha256, pin.size] for pin in STRANDS_V19.base_files])
        details = {}
    else:
        base_handle, expected = model.get('base'), model.get('base_digest')
        if not model.get('decision') or not isinstance(base_handle, dict) or not re.fullmatch('[0-9a-f]{64}', str(expected)):
            raise ValueError('A trained decision model requires its authorized producer base')
        actual = model_fingerprint(base_handle) if base_handle.get('id') else base_fingerprint(base_handle)
        if actual != expected:
            raise ValueError('The trained decision base content changed')
        root = Path(model['path'])
        cfg = _json(root / 'decider.json')
        if cfg.get('format') != FORMAT or set(cfg.get('sha256', {})) != set(ASSETS):
            raise ValueError('The decision model has an unsupported asset manifest')
        for name in ASSETS:
            if _hash(root / name) != cfg['sha256'][name]:
                raise ValueError('The decision model asset content changed')
        base = tokenizer = Path(base_handle['path'])
        head, weights = root / ASSETS[0], root / ASSETS[1]
        head_digest, weights_digest = cfg['sha256'][ASSETS[0]], cfg['sha256'][ASSETS[1]]
        adapter = _json(root / ASSETS[2])
        temperature = cfg.get('temperature', 1)
        details = {'temperature_by_kind': cfg.get('temperature_by_kind', {}),
                   'question_kinds': cfg.get('question_kinds', {})}
        if not isinstance(details['temperature_by_kind'], dict) or not isinstance(details['question_kinds'], dict):
            raise ValueError('Decision calibration tables must be objects')
        if any(not isinstance(key, str) or not isinstance(value, str) or len(key) > 300 or len(value) > 200 for key, value in details['question_kinds'].items()):
            raise ValueError('Decision calibration question keys are invalid')
        for value in details['temperature_by_kind'].values():
            valid_temperature(value)
        model_digest = _digest([cfg['sha256'], actual, cfg])
        base_digest = actual
    dim, dtype = cfg.get('pointer_dim'), cfg.get('dtype', 'bfloat16')
    if type(dim) is not int or not 1 <= dim <= 8192 or dtype not in ('float32', 'float16', 'bfloat16'):
        raise ValueError('The decision model has unsupported pointer dimensions or dtype')
    temperature = valid_temperature(temperature)
    if not isinstance(adapter, dict) or adapter.get('peft_type') != 'LORA' or adapter.get('task_type') not in (None, 'FEATURE_EXTRACTION', 'CAUSAL_LM'):
        raise ValueError('The decision model requires a supported LoRA adapter')
    identity = {'model_digest': model_digest, 'base_digest': base_digest,
                'calibration_digest': _digest([temperature, details]), 'score_semantics': 'uncalibrated'}
    _clean(adapter)
    if output_dir is None:
        return None, identity
    head, adapter_dir = _runtime_assets(output_dir, adapter, ((weights, weights_digest), (head, head_digest)))
    return Source(str(model.get('id', 'saved-decision')), base, tokenizer, head, adapter_dir,
                  dim, temperature, dtype, details), identity
