"""Retained native launch evidence for the bytes and parser a resident started with."""
from __future__ import annotations

import hashlib
import json
import marshal
import os
import re
import stat
import subprocess
from dataclasses import dataclass, fields, is_dataclass
from functools import lru_cache
from pathlib import Path
from types import CodeType, FunctionType

import psutil

from poolhouse import home
from poolhouse.client import chat, families, settings
from poolhouse.hub.modelfile import scan_gguf
from poolhouse.serve.build_platform import verify_libraries
from poolhouse.serve.preflight import shard_names
from poolhouse.serve.process import cmdline_digest, started_at


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _identity(path: Path) -> tuple:
    real = path.resolve(strict=True)
    info = real.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('A startup component is not a regular file')
    return (str(real), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_uid, info.st_mode, info.st_nlink)


@lru_cache(maxsize=256)
def _hash(identity: tuple) -> str:
    path = Path(identity[0])
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_ino != identity[2] or info.st_dev != identity[1]:
            raise ValueError('A startup component changed before hashing')
        digest = hashlib.sha256()
        while block := os.read(descriptor, 1 << 20):
            digest.update(block)
        if _identity(path) != identity:
            raise ValueError('A startup component changed while hashing')
        return digest.hexdigest()
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class File:
    role: str
    path: str
    identity: tuple
    sha256: str

    def current(self) -> bool:
        return _identity(Path(self.path)) == self.identity

    def said(self) -> dict:
        return {'path': self.identity[0], 'sha256': self.sha256}


def _file(role: str, path: Path) -> File:
    path = home.expand(path).absolute()
    identity = _identity(path)
    return File(role, str(path), identity, _hash(identity))


def _code(value: CodeType, module: str):
    constants = tuple(_code(item, module) if isinstance(item, CodeType) else item for item in value.co_consts)
    return value.replace(co_filename=module, co_consts=constants)


def _value(value):
    if isinstance(value, FunctionType):
        return {'code': marshal.dumps(_code(value.__code__, value.__module__)).hex(),
                'defaults': _value(value.__defaults__), 'keywords': _value(value.__kwdefaults__)}
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _value(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, (tuple, list)):
        return [_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _value(item) for key, item in value.items()}
    if isinstance(value, re.Pattern):
        return [value.pattern, value.flags]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return {'type': type(value).__module__ + '.' + type(value).__qualname__}


def parser_identity() -> str:
    """The loaded local Client normalizer, defaults and family configuration digest."""
    pieces = []
    for module in (chat, families, settings):
        objects = list(vars(module).values())
        for value in tuple(objects):
            if isinstance(value, type) and value.__module__ == module.__name__:
                objects.extend(vars(value).values())
        objects = [value.__func__ if isinstance(value, (staticmethod, classmethod)) else value for value in objects]
        code = [_value(value) for value in objects
                if isinstance(value, FunctionType) and value.__module__ == module.__name__]
        constants = {name: _value(value) for name, value in vars(module).items()
                     if name.isupper() or name in ('_OPEN', '_CLOSE')}
        pieces.append((module.__name__, code, constants))
    return _digest(pieces)


def _libraries(root: Path) -> tuple[str, ...]:
    names = tuple(sorted(item.name for item in root.iterdir()
                         if item.name.endswith(('.dylib', '.dll', '.metal', '.metallib'))
                         or '.so' in item.name))
    if len(names) > 256:
        raise ValueError('The runtime library inventory exceeds its bounds')
    return names


def _models(role: str, value) -> list[File]:
    path = home.expand(str(value))
    first = _file(role, path)
    names = shard_names(path.name)
    if len(names) > 128:
        raise ValueError('The model shard inventory exceeds its bounds')
    descriptor = os.open(first.identity[0], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        held = os.fstat(descriptor)
        if (not stat.S_ISREG(held.st_mode) or held.st_ino != first.identity[2]
                or held.st_dev != first.identity[1]):
            raise ValueError('The model changed before metadata was read')
        if os.pread(descriptor, 4, 0) == b'GGUF':
            meta = scan_gguf(Path(f'/dev/fd/{descriptor}'), want=lambda key: key == 'split.count').values
            if int(meta.get('split.count', 1)) != len(names):
                raise ValueError('The loaded model shard names are unresolved')
        if not first.current():
            raise ValueError('The model changed while metadata was read')
    finally:
        os.close(descriptor)
    return [_file(role, path.parent / name) for name in names]


@dataclass(frozen=True)
class Capture:
    files: tuple[File, ...]
    libraries: tuple[str, ...]
    root: Path
    parser: str
    settings: str
    argv: tuple[str, ...]

    def current(self) -> bool:
        return (_libraries(self.root) == self.libraries and parser_identity() == self.parser
                and all(item.current() for item in self.files))

    def bind(self, process, port: int, backend: str):
        try:
            if self.current():
                return Receipt(self, process, port, backend, started_at(process.pid), cmdline_digest(process.pid))
        except (OSError, ValueError):
            pass
        return None


def _native(binary: Path) -> bool:
    descriptor = os.open(binary.resolve(strict=True), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return False
        return os.read(descriptor, 4) in (b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe',
                                          b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe',
                                          b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca')
    finally:
        os.close(descriptor)


def _effective(spec, binary: Path, argv: list[str]) -> dict:
    if Path(argv[0]).resolve(strict=True) != binary.resolve(strict=True):
        raise ValueError('The launched executable differs from its captured runtime')
    aliases = {'model': ('-m', '--model'), 'draft': ('-md', '--model-draft'),
               'mmproj': ('--mmproj',), 'chat_template_file': ('--chat-template-file',),
               'lookup_static': ('--lookup-cache-static',)}
    for field, flags in aliases.items():
        matches = [index for index, value in enumerate(argv) if value.split('=', 1)[0] in flags]
        wanted = getattr(spec, field)
        if len(matches) != bool(wanted):
            raise ValueError('The effective component flags are ambiguous')
        if wanted:
            index = matches[0]
            value = argv[index].split('=', 1)[1] if '=' in argv[index] else argv[index + 1]
            if home.expand(value).resolve(strict=True) != home.expand(str(wanted)).resolve(strict=True):
                raise ValueError('The effective component differs from the captured input')
    for flags, wanted in ((('-c', '--ctx-size'), int(spec.context)),
                          (('-np', '--parallel'), max(1, int(spec.parallel or 1)))):
        matches = [index for index, value in enumerate(argv) if value.split('=', 1)[0] in flags]
        if len(matches) != 1:
            raise ValueError('The effective context or parallel setting is ambiguous')
        index = matches[0]
        value = argv[index].split('=', 1)[1] if '=' in argv[index] else argv[index + 1]
        if int(value) != wanted or wanted <= 0:
            raise ValueError('The effective context or parallel setting is unresolved')
    kwargs = [index for index, value in enumerate(argv) if value.split('=', 1)[0] == '--chat-template-kwargs']
    if len(kwargs) > 1 or argv.count('--jinja') + argv.count('--no-jinja') != 1:
        raise ValueError('The effective template settings are ambiguous')
    if ('--jinja' in argv) != bool(spec.jinja):
        raise ValueError('The effective template setting differs from its capture')
    return {'context': int(spec.context), 'parallel': max(1, int(spec.parallel or 1)),
            'jinja': bool(spec.jinja), 'template_kwargs': _digest([argv[index:index + 2] for index in kwargs])}


def current(evidence):
    """Keep evidence only while its exact pre-spawn components remain unchanged."""
    try:
        return evidence if evidence is not None and evidence.current() else None
    except (OSError, ValueError):
        return None


def capture(spec, binary: Path, argv: list[str], *, environment=None) -> Capture | None:
    """Snapshot effective native inputs; unresolved components leave readiness unverified."""
    try:
        if not _native(binary):
            return None
        environment = dict(environment or {})
        if any(key.startswith(('GGML_', 'LLAMA_', 'DYLD_', 'LD_'))
               and key != 'LLAMA_SERVER_SLOTS_DEBUG' for key in environment):
            return None
        options = _effective(spec, binary, argv)
        root = binary.resolve(strict=True).parent
        libraries = _libraries(root)
        verify_libraries(root, {binary.resolve().name, *(name for name in libraries
                         if name.endswith('.dylib') or '.so' in name)}, environment)
        files = [_file('binary', binary), *(_file('library', root / name) for name in libraries)]
        for role, value in (('model', spec.model), ('draft', spec.draft), ('projector', spec.mmproj)):
            if value:
                files.extend(_models(role, value))
        for role, value in (('template', spec.chat_template_file), ('lookup', spec.lookup_static)):
            if value:
                files.append(_file(role, Path(value)))
        for module in (chat, families, settings):
            files.append(_file('parser-source', Path(module.__file__)))
        redacted = ['***' if index and argv[index - 1] == '--api-key' else value
                    for index, value in enumerate(argv)]
        options['effective_argv'] = _digest(redacted)
        result = Capture(tuple(files), libraries, root, parser_identity(), json.dumps(options, sort_keys=True), tuple(argv))
        return result if result.current() else None
    except (OSError, ValueError, TypeError, IndexError, subprocess.SubprocessError):
        return None


@dataclass(frozen=True)
class Receipt:
    capture: Capture
    process: object
    port: int
    backend: str
    started: float | None
    cmdline: str | None

    def current(self) -> bool:
        process = self.process
        if (process.poll() is not None or self.started is None or not self.cmdline
                or started_at(process.pid) != self.started or cmdline_digest(process.pid) != self.cmdline):
            return False
        actual = psutil.Process(process.pid)
        if (Path(actual.exe()).resolve() != Path(self.capture.files[0].identity[0])
                or actual.cmdline() != list(self.capture.argv)):
            return False
        connections = actual.net_connections(kind='tcp')
        return any(row.status == psutil.CONN_LISTEN and row.laddr.port == self.port
                   and row.laddr.ip in ('127.0.0.1', '::1') for row in connections)

    def said(self) -> dict | None:
        try:
            if not self.current() or not self.capture.current():
                return None
            artifacts = {}
            for item in self.capture.files:
                if item.role not in ('binary', 'library', 'parser-source'):
                    artifacts.setdefault(item.role, []).append(item.said())
            result = {'port': self.port, 'pid': self.process.pid, 'started': self.started,
                      'cmdline': self.cmdline, 'backend': self.backend, 'artifacts': artifacts,
                      'runtime': {'binary': self.capture.files[0].said(),
                                  'inventory': [item.said() for item in self.capture.files
                                                if item.role in ('library', 'parser-source')],
                                  'parser': self.capture.parser},
                      'settings': json.loads(self.capture.settings)}
            result['id'] = _digest(result)
            return result if self.current() and self.capture.current() else None
        except (OSError, ValueError, psutil.Error):
            return None


def retained(info) -> dict | None:
    """Only the original in-memory launch holder can report current startup evidence."""
    proof = getattr(info, 'startup', None)
    if (getattr(info, 'adopted', True) or not isinstance(proof, Receipt)
            or info.process is not proof.process or info.pid != proof.process.pid or info.port != proof.port):
        return None
    return proof.said()
