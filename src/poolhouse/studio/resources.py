"""Server-owned installed model resolution for Studio execution."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path

from poolhouse import hub
from poolhouse.contracts import recipes
from poolhouse.hub import hfstore


def model_resource(binding: dict, installed: list | None = None) -> dict:
    """Resolve only the exact identifiers offered by the installed model library."""
    ident = binding.get('id') if binding.get('kind') == 'model' else None
    entries = hub.discover() if installed is None else installed
    entry = next((entry for entry in entries if entry.id == ident), None)
    if entry is None or not entry.is_complete or not entry.verified:
        raise ValueError('Select a complete, verified model from the local model library')
    if any(path.is_symlink() for path in (entry.path, *entry.path.parents)):
        raise ValueError('The installed model directory cannot be a symbolic link')
    path = entry.path.resolve(strict=True)
    return {'kind': 'model', 'id': entry.id, 'path': str(path), 'format': entry.format,
            'model_kind': entry.kind, 'repo': getattr(entry, 'repo', ''), 'framework': 'mlx' if entry.format == 'mlx' else 'torch'}


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



def model_files(handle: dict) -> list[dict]:
    """Enumerate exact library content, including native same-repository cache blobs."""
    current = model_resource(handle)
    root = Path(current['path'])
    if str(root) != handle.get('path'):
        raise ValueError('The installed model identity or directory changed')
    approved = None
    if current.get('repo'):
        repository = hfstore.repo_root(current['repo'])
        if any(path.is_symlink() for path in (repository, *repository.parents)):
            raise ValueError('The native model cache directory cannot be a symbolic link')
        approved = repository.resolve(strict=True)
    members = []
    paths = sorted(root.rglob('*')) if root.is_dir() else [root]
    for path in paths:
        if path.is_symlink():
            snapshot = (approved / 'snapshots' / root.name) if approved is not None else None
            if snapshot != root or not re.fullmatch('[0-9a-f]{40}', root.name) or path.is_dir():
                raise ValueError('The model contains an unsupported symbolic link')
            target = Path(os.path.normpath(path.parent / path.readlink()))
            if target.parent != approved / 'blobs' or target.is_symlink() or target.resolve(strict=True) != target:
                raise ValueError('A cached model link must name its own repository blob')
            canonical = target
        else:
            canonical = path
        if canonical.is_dir():
            continue
        info = canonical.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('A model member must be a regular file')
        if any(parent.is_symlink() for parent in canonical.parents):
            raise ValueError('A model member directory cannot be a symbolic link')
        relative = path.relative_to(root).as_posix() if root.is_dir() else path.name
        members.append({'relative': relative, 'path': str(canonical), 'size': info.st_size,
                        'identity': _identity(info)})
    if not members:
        raise ValueError('The installed model has no regular files')
    return members


def _identity(info: os.stat_result) -> list[int]:
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


@contextmanager
def open_model_file(member: dict):
    """Open an enumerated member without following a replaced link or changed file."""
    path = Path(member['path'])
    if any(parent.is_symlink() for parent in path.parents):
        raise ValueError('The model member directory changed')
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or _identity(before) != member['identity']:
        raise ValueError('The model member changed before reading')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as source:
        if _identity(os.fstat(source.fileno())) != member['identity']:
            raise ValueError('The model member changed while opening')
        yield source
        if _identity(os.fstat(source.fileno())) != member['identity'] or _identity(path.lstat()) != member['identity']:
            raise ValueError('The model member changed while reading')


def model_fingerprint(handle: dict) -> str:
    """Bind a model identity to every approved member's relative name and bytes."""
    rows = []
    for member in model_files(handle):
        checksum = hashlib.sha256()
        with open_model_file(member) as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                checksum.update(chunk)
        rows.append([member['relative'], member['size'], checksum.hexdigest()])
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
