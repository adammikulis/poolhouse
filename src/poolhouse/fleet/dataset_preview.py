"""Bounded structured samples from confined workspace data files."""
from __future__ import annotations

import codecs
import csv
import io
import json
import os
from pathlib import Path

from poolhouse.json_objects import unique_object

from .dataset_catalogue import _value
from .files import safe_relpath
from .studio_artifacts import _open

MAX_BYTES = 1_000_000


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _json(text):
    def constant(value):
        raise ValueError('Dataset numbers must be finite.')
    try:
        return _value(json.loads(text, object_pairs_hook=unique_object, parse_constant=constant))
    except (ValueError, RecursionError):
        raise ValueError('The selected sample contains invalid JSON records.') from None


def _records(text, suffix, truncated):
    if suffix == '.json':
        if truncated:
            return []
        rows = _json(text)
        return (rows if isinstance(rows, list) else [rows])[:10]
    if suffix == '.jsonl':
        lines = text.splitlines(keepends=True)
        if truncated and lines and not lines[-1].endswith(('\n', '\r')):
            lines.pop()
        rows = []
        for line in lines:
            if line.strip():
                rows.append(_json(line))
            if len(rows) == 10:
                break
        return rows
    if suffix == '.csv':
        stream = io.StringIO(text, newline='')
        reader = csv.reader(stream, strict=True)
        keys = next(reader, [])
        if truncated and stream.tell() == len(text) and not text.endswith(('\n', '\r')):
            return []
        if (not keys or len(keys) > 512 or len(set(keys)) != len(keys)
                or any(not key or len(key) > 256 for key in keys)):
            raise ValueError('CSV columns need distinct, nonempty names.')
        rows = []
        while len(rows) < 10:
            try:
                values = next(reader)
            except StopIteration:
                break
            except csv.Error as error:
                if truncated and stream.tell() == len(text) and (
                        not text.endswith(('\n', '\r')) or str(error) == 'unexpected end of data'):
                    break
                raise
            if truncated and stream.tell() == len(text) and not text.endswith(('\n', '\r')):
                break
            if not values:
                continue
            if len(values) != len(keys) or any(len(value) > 500000 for value in values):
                raise ValueError('A CSV sample record does not match its columns.')
            rows.append(dict(zip(keys, values, strict=True)))
            if len(rows) == 10:
                break
        return rows
    return []


def preview(path: str, files_root: Path) -> dict:
    """Return one current file sample without following links or loading media."""
    if not isinstance(path, str) or '%' in path or Path(path).parts[:1] in [('jobs',), ('studio',), ('studio-device-resources',)]:
        raise ValueError('Choose a local dataset from the library.')
    safe_relpath(files_root, path)
    if Path(path).as_posix() != path:
        raise ValueError('Choose an exact dataset filename from the library.')
    suffix = Path(path).suffix.lower()
    if suffix not in {'.json', '.jsonl', '.csv', '.txt'}:
        raise ValueError('Preview a JSON, JSONL, CSV or text dataset.')
    try:
        with _open(files_root, Path(path)) as source:
            before = os.fstat(source.fileno())
            raw = source.read(MAX_BYTES + 1)
            after = os.fstat(source.fileno())
            with _open(files_root, Path(path)) as check:
                named = os.fstat(check.fileno())
            if _identity(before) != _identity(after) or _identity(before) != _identity(named):
                raise ValueError('The local dataset changed while its sample was read.')
    except OSError:
        raise ValueError('The selected local dataset is unavailable.') from None
    truncated = len(raw) > MAX_BYTES
    try:
        text = codecs.getincrementaldecoder('utf-8')('strict').decode(raw[:MAX_BYTES], final=not truncated)
        rows = _records(text, suffix, truncated)
    except (UnicodeError, csv.Error):
        raise ValueError('The selected sample is not valid UTF-8 tabular data.') from None
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError('Dataset samples need one JSON object per record.')
    names = list(dict.fromkeys(key for row in rows for key in row))
    if len(names) > 512:
        raise ValueError('The dataset sample has too many fields.')
    columns = [{'name': key, 'type': 'sample'} for key in names]
    return {'path': path, 'format': suffix[1:], 'columns': columns, 'rows': rows, 'bytes': before.st_size,
            'truncated': truncated, 'sampled': True, 'text': text if not rows else ''}
