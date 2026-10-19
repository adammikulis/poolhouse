"""Server-owned installed model resolution for Studio execution."""
from __future__ import annotations

import json
from pathlib import Path

from poolhouse import hub
from poolhouse.contracts import recipes


def model_resource(binding: dict, installed: list | None = None) -> dict:
    """Resolve only the exact identifiers offered by the installed model library."""
    ident = binding.get('id') if binding.get('kind') == 'model' else None
    entries = hub.discover() if installed is None else installed
    entry = next((entry for entry in entries if entry.id == ident), None)
    if entry is None or not entry.is_complete or not entry.verified:
        raise ValueError('Select a complete, verified model from the local model library')
    path = entry.path.resolve(strict=True)
    return {'kind': 'model', 'id': entry.id, 'path': str(path), 'format': entry.format,
            'model_kind': entry.kind, 'framework': 'mlx' if entry.format == 'mlx' else 'torch'}


def training_base(resource: dict, parameters: dict) -> Path:
    """Check supported language-model weights before acquiring device compute."""
    path = Path(resource['path'])
    if resource.get('format') not in {'mlx', 'safetensors'} or resource.get('model_kind') != 'chat' or not path.is_dir():
        raise ValueError('Fine-tuning needs an installed chat model with MLX or safetensors weights')
    config = path / 'config.json'
    if not config.is_file() or not any(path.glob('*.safetensors')):
        raise ValueError('The selected base model has no complete configuration and safetensors weights')
    configuration = json.loads(config.read_text())
    if not isinstance(configuration, dict) or not configuration.get('model_type'):
        raise ValueError('The selected base model has no supported model configuration')
    spec = next(recipe for recipe in recipes() if recipe['id'] == 'tool-calls')
    size = spec['sizes'].get(parameters.get('size'), {})
    framework = parameters.get('framework') or size.get('framework') or spec['requires']['backend']
    expected = 'mlx' if resource['format'] == 'mlx' else 'torch'
    if framework != expected or (framework == 'torch' and configuration.get('quantization')):
        raise ValueError(f'Select {expected} training for this installed model format')
    if framework == 'mlx' and not parameters.get('lora', size.get('defaults', {}).get('lora', False)):
        raise ValueError('MLX conversation fine-tuning requires the LoRA adapter strategy')
    return path

