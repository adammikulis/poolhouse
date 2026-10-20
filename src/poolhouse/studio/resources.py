"""Server-owned installed model resolution for Studio execution."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from contextlib import contextmanager
from pathlib import Path

from poolhouse import hub, optional
from poolhouse.contracts import recipes
from poolhouse.files import writing
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
        shared_digest = None
        if path.is_symlink():
            snapshot = (approved / 'snapshots' / root.name) if approved is not None else None
            if snapshot != root or not re.fullmatch('[0-9a-f]{40}', root.name) or path.is_dir():
                raise ValueError('The model contains an unsupported symbolic link')
            target = Path(os.path.normpath(path.parent / path.readlink()))
            if target.parent != approved / 'blobs':
                raise ValueError('A cached model link must name its own repository blob')
            canonical = target
            shared_digest = None
            if target.is_symlink():
                canonical = Path(os.path.normpath(target.parent / target.readlink()))
                digest = canonical.name
                shared = approved.parent / 'blobs'
                if (not re.fullmatch('[0-9a-f]{64}', digest)
                        or canonical != shared / digest[:2] / digest
                        or canonical.is_symlink()):
                    raise ValueError('A shared cache blob link must name its content digest')
                shared_digest = digest
            if canonical.resolve(strict=True) != canonical:
                raise ValueError('A cached model blob cannot contain directory links')
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
                        'identity': _identity(info), **({'sha256': shared_digest} if shared_digest else {})})
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
        if member.get('sha256'):
            checked = _ContentReader(source)
            yield checked
            while checked.read(1024 * 1024):
                pass
            if checked.checksum.hexdigest() != member['sha256']:
                raise ValueError('The shared model blob does not match its content digest')
        else:
            yield source
        if _identity(os.fstat(source.fileno())) != member['identity'] or _identity(path.lstat()) != member['identity']:
            raise ValueError('The model member changed while reading')


class _ContentReader:
    """Hash sequential reads without a second pass over native model weights."""

    def __init__(self, source):
        self.source = source
        self.checksum = hashlib.sha256()

    def seek(self, offset, whence=0):
        current = self.source.tell()
        if whence != 0 or offset < current:
            raise ValueError('A verified model stream only supports forward absolute seeks')
        while current < offset:
            chunk = self.read(min(1024 * 1024, offset - current))
            if not chunk:
                raise ValueError('A model resume offset exceeds its declared content')
            current += len(chunk)
        return current

    def read(self, size=-1):
        data = self.source.read(size)
        self.checksum.update(data)
        return data


def model_fingerprint(handle: dict) -> str:
    """Bind a model identity to every approved member's relative name and bytes."""
    return _fingerprint(model_files(handle))


def base_fingerprint(handle: dict) -> str:
    """Hash an explicitly authorized staged base; this function grants no authority."""
    root = Path(handle['path'])
    if not root.is_dir() or any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError('A staged base must be a regular owned directory')
    members = []
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('A staged base cannot contain symbolic links or special files')
        members.append({'relative': path.relative_to(root).as_posix(), 'path': str(path),
                        'size': info.st_size, 'identity': _identity(info)})
    if not members:
        raise ValueError('A staged base has no regular files')
    return _fingerprint(members)


def _fingerprint(members: list[dict]) -> str:
    rows = []
    for member in members:
        checksum = hashlib.sha256()
        with open_model_file(member) as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                checksum.update(chunk)
        rows.append([member['relative'], member['size'], checksum.hexdigest()])
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


ADAPTER_FILES = frozenset({'adapter_config.json', 'adapter_model.safetensors', 'adapters.safetensors', 'config.json'})


def materialize_checkpoint(checkpoint: Path, training: Path, output: Path) -> Path:
    """Declare a complete, regular checkpoint without native recovery links or logs."""
    if checkpoint.is_symlink() or not checkpoint.resolve(strict=True).is_relative_to(training.resolve(strict=True)):
        raise ValueError('Checkpoint must belong to this training node')
    target = output / checkpoint.name
    if any(path.is_symlink() for path in (target, *target.parents) if path.is_relative_to(output)):
        raise ValueError('Checkpoint output directory cannot contain links')
    target.mkdir(parents=True, exist_ok=True)
    sources = [(checkpoint / name, target / name) for name in ('state.json', 'model.safetensors')]
    sources += [(training / 'adapter' / name, target / 'adapter' / name)
                for name in ADAPTER_FILES if (training / 'adapter' / name).exists()]
    for source, destination in sources:
        if any(path.is_symlink() for path in destination.parents if path.is_relative_to(output)):
            raise ValueError('Checkpoint output directory cannot contain links')
        linked = any(path.is_symlink() for path in source.parents if path.is_relative_to(training))
        if linked or source.is_symlink() or not source.is_file() or source.stat().st_nlink != 1:
            raise ValueError('Checkpoint files must be regular owned training artifacts')
        with writing(destination) as temporary:
            shutil.copyfile(source, temporary)
    return target


def checkpoint_base(model: dict, *, framework: str | None = None) -> Path | None:
    """Verify a server-supplied producer base; serialized checkpoint paths grant nothing."""
    state = Path(model['path']) / 'state.json'
    if state.is_symlink() or not state.is_file():
        raise ValueError('Use a complete materialized checkpoint for trained model reuse')
    recorded = json.loads(state.read_text()).get('config', {})
    recipe = recorded.get('recipe') if isinstance(recorded, dict) else None
    if recipe not in {'tool-calls', 'classify-text', 'text-lm'} or recipe != model.get('recipe'):
        raise ValueError('Checkpoint recipe metadata does not match its recorded state')
    if recipe != 'tool-calls':
        return None
    base, expected = model.get('base'), model.get('base_digest')
    if not isinstance(base, dict) or base.get('kind') != 'model' or not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('The checkpoint needs its authorized producer base model')
    actual = model_fingerprint(base) if base.get('id') else base_fingerprint(base)
    if actual != expected:
        raise ValueError('The checkpoint producer base content changed')
    return optional.module('poolhouse.train.recipes').validate_base(model['path'], base['path'], framework=framework)
