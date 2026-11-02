"""Descriptor-confined original-file records; callers alone persist cursors and admit execution."""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import os
import stat
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from poolhouse.json_objects import unique_object

from .store import digest


class SourceChanged(ValueError):
    """A watched anchor or consumed prefix changed; review the source before resetting it."""


def _allowed(authorize):
    if not authorize():
        raise PermissionError('The watched source is no longer authorized')


def _identity(info):
    return [info.st_dev, info.st_ino]


@contextmanager
def _directory(path):
    if not path.is_absolute() or '..' in path.parts:
        raise SourceChanged('The source directory is invalid')
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in path.parts[1:]:
            following = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        yield descriptor
    finally:
        os.close(descriptor)


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_ctime_ns, info.st_mtime_ns, info.st_size, info.st_nlink)


def _regular(descriptor):
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SourceChanged('A watched file must be regular and have one link')
    return info


def bind(path, mode, *, authorize):
    """Bind the actual native selection; this metadata must remain server-owned."""
    _allowed(authorize)
    path = Path(path).absolute()
    if not isinstance(mode, str) or mode not in {'file', 'folder'} or path.name in {'', '.', '..'}:
        raise ValueError('Choose a regular CSV/JSONL file or a folder')
    if mode == 'file' and path.suffix.lower() not in {'.jsonl', '.csv'}:
        raise ValueError('Choose a CSV or JSONL file')
    with _directory(path.parent) as parent:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        descriptor = os.open(path.name, flags | (os.O_DIRECTORY if mode == 'folder' else 0), dir_fd=parent)
        try:
            info = os.fstat(descriptor) if mode == 'folder' else _regular(descriptor)
            binding = {'version': 1, 'id': uuid.uuid4().hex, 'path': str(path), 'mode': mode,
                       'parent': _identity(os.fstat(parent)), 'directory': _identity(info) if mode == 'folder' else None}
        finally:
            os.close(descriptor)
    _allowed(authorize)
    return binding


def _empty(binding):
    return {'version': 1, 'binding': binding['id'], 'files': {}}


def _cursor(binding, value):
    value = copy.deepcopy(value) if value is not None else _empty(binding)
    if (not isinstance(value, dict) or set(value) != {'version', 'binding', 'files'}
            or type(value['version']) is not int or value['version'] != 1 or value['binding'] != binding['id']
            or not isinstance(value['files'], dict)):
        raise SourceChanged('Invalid watched source cursor')
    for name, row in value['files'].items():
        if (not isinstance(name, str) or Path(name).name != name or name in {'', '.', '..'}
                or not isinstance(row, dict) or set(row) != {'identity', 'generation', 'offset', 'prefix', 'header', 'pending'}
                or not isinstance(row['identity'], list) or len(row['identity']) != 2
                or any(type(item) is not int or item < 0 for item in row['identity'])
                or any(type(row[key]) is not int or row[key] < 0 for key in ('generation', 'offset'))
                or not isinstance(row['prefix'], str) or len(row['prefix']) != 64
                or any(char not in '0123456789abcdef' for char in row['prefix'])
                or (row['header'] is not None and (not isinstance(row['header'], list)
                    or any(not isinstance(item, str) or not item for item in row['header'])
                    or len(set(row['header'])) != len(row['header'])))
                or (row['pending'] is not None and (not isinstance(row['pending'], dict)
                    or set(row['pending']) != {'bytes', 'sha256'} or type(row['pending']['bytes']) is not int
                    or row['pending']['bytes'] <= 0 or not isinstance(row['pending']['sha256'], str)
                    or len(row['pending']['sha256']) != 64
                    or any(char not in '0123456789abcdef' for char in row['pending']['sha256'])))):
            raise SourceChanged('Invalid watched file cursor')
    return value


def _number(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError('Nonfinite JSON value')
    return value


def _invalid_constant(text):
    raise ValueError('Nonfinite JSON value')


@dataclass(frozen=True)
class _Admission:
    authorize: Callable[[], bool]
    maximum: int
    deadline: float


class _Record:
    def __init__(self, descriptor, row, maximum, csv_format):
        self.source = os.fdopen(descriptor, 'rb')
        self.row = copy.deepcopy(row)
        self.signature = _signature(_regular(descriptor))
        self.maximum, self.csv_format = maximum, csv_format
        self.prefix = hashlib.sha256()
        self.verified = 0
        self.buffer = bytearray()
        self.content = hashlib.sha256()
        self.size = 0
        self.quoted = False
        self.quote_end = False
        self.field_start = True
        self.oversize = False

    def close(self):
        self.source.close()

    def _boundary(self, data):
        if not self.csv_format:
            return data.endswith(b'\n')
        for char in data:
            if self.quoted:
                if char == 34:
                    self.quote_end = not self.quote_end
                    continue
                if not self.quote_end:
                    continue
                self.quoted, self.quote_end = False, False
            if char == 34 and self.field_start:
                self.quoted = True
                self.field_start = False
            elif char == 44:
                self.field_start = True
            elif char == 10:
                return True
            elif char != 13:
                self.field_start = False
        return False

    def read(self, deadline, authorize):
        while self.verified < self.row['offset']:
            _allowed(authorize)
            if time.monotonic() >= deadline:
                return False
            data = self.source.read(min(io.DEFAULT_BUFFER_SIZE, self.row['offset'] - self.verified))
            if not data:
                raise SourceChanged('The source truncated a consumed prefix')
            self.prefix.update(data)
            self.verified += len(data)
        if self.size == 0 and self.prefix.hexdigest() != self.row['prefix']:
            raise SourceChanged('The source modified an already consumed record')
        while time.monotonic() < deadline:
            _allowed(authorize)
            data = self.source.readline(io.DEFAULT_BUFFER_SIZE)
            if not data:
                return False
            self.size += len(data)
            self.prefix.update(data)
            self.content.update(data)
            if not self.oversize and self.size <= self.maximum:
                self.buffer.extend(data)
            else:
                self.oversize = True
                self.buffer.clear()
            if self._boundary(data):
                return True
        return False


class WatchedSource:
    """One fair, lazy scanner per active source; close it when the source stops."""

    def __init__(self, binding):
        if (not isinstance(binding, dict) or set(binding) != {'version', 'id', 'path', 'mode', 'parent', 'directory'}
                or type(binding['version']) is not int or binding['version'] != 1
                or not isinstance(binding['id'], str) or len(binding['id']) != 32
                or any(char not in '0123456789abcdef' for char in binding['id'])
                or not isinstance(binding['mode'], str) or binding['mode'] not in {'file', 'folder'} or not isinstance(binding['path'], str)
                or not Path(binding['path']).is_absolute() or '..' in Path(binding['path']).parts
                or not isinstance(binding['parent'], list) or len(binding['parent']) != 2
                or any(type(item) is not int or item < 0 for item in binding['parent'])
                or (binding['mode'] == 'folder' and (not isinstance(binding['directory'], list)
                    or len(binding['directory']) != 2 or any(type(item) is not int or item < 0 for item in binding['directory'])))
                or (binding['mode'] == 'file' and (binding['directory'] is not None
                    or Path(binding['path']).suffix.lower() not in {'.csv', '.jsonl'}))):
            raise SourceChanged('Invalid source binding')
        self.binding = copy.deepcopy(binding)
        self.scan = None
        self.readers = {}

    def close(self):
        if self.scan is not None:
            self.scan.close()
            self.scan = None
        for reader in self.readers.values():
            reader.close()
        self.readers.clear()

    def _generation(self, name, row):
        return digest({'binding': self.binding['id'], 'name': name, 'identity': row['identity'], 'sequence': row['generation']})

    def _event(self, name, row, start, end, checksum, **payload):
        generation = self._generation(name, row)
        return {'id': digest({'generation': generation, 'start': start, 'end': end, 'sha256': checksum}),
                'generation': generation, **payload}

    @staticmethod
    def _stable(reader, directory, name, authorize):
        _allowed(authorize)
        descriptor = os.fstat(reader.source.fileno())
        if descriptor.st_nlink > 1:
            raise SourceChanged('A watched file acquired another link during its read')
        try:
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return _signature(descriptor) == reader.signature == _signature(current)

    def _read(self, directory, name, cursor, admission):
        authorize, maximum, deadline = admission.authorize, admission.maximum, admission.deadline
        _allowed(authorize)
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return None
        try:
            info = _regular(descriptor)
            if name not in cursor['files']:
                # This identity anchors the logical stream, not its current inode.
                # Replacement files must prove the same consumed prefix below.
                cursor['files'][name] = {'identity': _identity(info), 'generation': 0,
                                        'offset': 0, 'prefix': hashlib.sha256(b'').hexdigest(),
                                        'header': None, 'pending': None}
            row = cursor['files'][name]
            if info.st_size < row['offset']:
                raise SourceChanged('The source truncated a consumed prefix')
            reader = self.readers.get(name)
            expected = {key: value for key, value in row.items() if key != 'pending'}
            if reader and ({key: value for key, value in reader.row.items() if key != 'pending'} != expected
                           or reader.maximum != maximum or reader.signature != _signature(info)):
                self.readers.pop(name).close()
                reader = None
            if reader is None:
                reader = _Record(descriptor, row, maximum, Path(name).suffix.lower() == '.csv')
                self.readers[name] = reader
                descriptor = None
            ready = reader.read(deadline, authorize)
            if not self._stable(reader, directory, name, authorize):
                self.readers.pop(name).close()
                return None
            if not ready:
                if reader.size:
                    row['pending'] = {'bytes': reader.size, 'sha256': reader.content.hexdigest()}
                elif reader.verified == row['offset']:
                    row['pending'] = None
                return None
            before = copy.deepcopy(row)
            start = row['offset']
            row.update(offset=start + reader.size, prefix=reader.prefix.hexdigest(), pending=None)
            event = self._decoded(name, row, reader, start)
            stable = self._stable(reader, directory, name, authorize)
            self.readers.pop(name).close()
            if not stable:
                cursor['files'][name] = before
                return None
            return event
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _decoded(self, name, row, reader, start):
        try:
            if reader.oversize:
                raise ValueError('The complete record exceeds the admitted event byte budget')
            text = bytes(reader.buffer).decode('utf-8-sig' if start == 0 else 'utf-8')
            if Path(name).suffix.lower() == '.jsonl':
                payload = json.loads(text, object_pairs_hook=unique_object, parse_float=_number, parse_constant=_invalid_constant)
                if not isinstance(payload, dict):
                    raise ValueError('A JSONL source record must be an object')
            else:
                csv.field_size_limit(max(csv.field_size_limit(), reader.maximum))
                cells = next(csv.reader(io.StringIO(text, newline=''), strict=True))
                if row['header'] is None:
                    if not cells or any(not cell for cell in cells) or len(set(cells)) != len(cells):
                        row['header'] = []
                        raise ValueError('CSV headers must be nonempty and unique')
                    row['header'] = cells
                    return None
                if not row['header'] or len(cells) != len(row['header']):
                    raise ValueError('The CSV record does not match its declared header')
                payload = dict(zip(row['header'], cells, strict=True))
            return self._event(name, row, start, row['offset'], reader.content.hexdigest(), payload=payload)
        except (ValueError, UnicodeError, csv.Error, StopIteration, RecursionError):
            if Path(name).suffix.lower() == '.csv' and row['header'] is None:
                row['header'] = []
            return self._event(name, row, start, row['offset'], reader.content.hexdigest(),
                               error='The complete source record is invalid or exceeds its admitted byte budget')

    def poll(self, cursor=None, *, authorize, max_record_bytes, deadline):
        """Return at most one complete record; the caller atomically commits inbox and cursor."""
        if type(max_record_bytes) is not int or max_record_bytes <= 0 or not math.isfinite(deadline):
            raise ValueError('Supply the admitted event budget and scheduler deadline')
        admission = _Admission(authorize, max_record_bytes, deadline)
        cursor = _cursor(self.binding, cursor)
        _allowed(authorize)
        path = Path(self.binding['path'])
        with _directory(path.parent) as parent:
            if _identity(os.fstat(parent)) != self.binding['parent']:
                raise SourceChanged('The watched parent directory changed')
            if self.binding['mode'] == 'file':
                event = self._read(parent, path.name, cursor, admission)
            else:
                descriptor = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    if _identity(os.fstat(descriptor)) != self.binding['directory']:
                        raise SourceChanged('The selected source folder changed')
                    if self.scan is None:
                        self.scan = os.scandir(descriptor)
                    event = None
                    while time.monotonic() < deadline:
                        _allowed(authorize)
                        entry = next(self.scan, None)
                        if entry is None:
                            self.scan.close()
                            self.scan = None
                            break
                        if Path(entry.name).suffix.lower() not in {'.csv', '.jsonl'} or entry.is_dir(follow_symlinks=False):
                            continue
                        event = self._read(descriptor, entry.name, cursor, admission)
                        if event is not None:
                            break
                finally:
                    os.close(descriptor)
        _allowed(authorize)
        return {'event': event, 'cursor': cursor}
