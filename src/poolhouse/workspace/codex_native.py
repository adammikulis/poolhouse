"""Bounded parent provenance from private native Codex session headers."""
from __future__ import annotations

import datetime
import json
import os
import re
import stat
from pathlib import Path

from poolhouse import home

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
MAX_FILES = 2048
MAX_HEADER = 65536


def _owned(path: Path, directory: bool = False) -> bool:
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    return kind(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o022


def parent(event: dict) -> str:
    """The actual child header's native parent id, or a refusal when provenance is unavailable."""
    identifier = event.get('agent_id')
    if not isinstance(identifier, str) or not UUID.fullmatch(identifier):
        raise ValueError('native subagent identifier must be a UUID')
    root = Path(os.environ.get('CODEX_HOME') or home.user_home() / '.codex')
    sessions = root / 'sessions'
    if not _owned(root, True) or not _owned(sessions, True):
        raise ValueError('native Codex session directories must be owned and not writable by others')
    today = datetime.datetime.now(datetime.UTC).date()
    dates = [today, today - datetime.timedelta(days=1)]
    transcript = event.get('transcript_path')
    if isinstance(transcript, str):
        where = Path(transcript)
        try:
            parts = where.relative_to(sessions).parts
            if len(parts) == 4:
                dates.append(datetime.date(int(parts[0]), int(parts[1]), int(parts[2])))
        except (ValueError, TypeError):
            pass
    for date in dict.fromkeys(dates):
        folder = sessions / f'{date.year:04d}' / f'{date.month:02d}' / f'{date.day:02d}'
        try:
            if not all(_owned(path, True) for path in (folder.parent.parent, folder.parent, folder)):
                continue
            with os.scandir(folder) as files:
                for count, entry in enumerate(files):
                    if count >= MAX_FILES:
                        raise ValueError('native session folder exceeds the bounded lookup')
                    if not entry.name.startswith('rollout-') or not entry.name.endswith(f'-{identifier}.jsonl'):
                        continue
                    path = Path(entry.path)
                    if not _owned(path):
                        raise ValueError('native session header must be an owned regular file')
                    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                    with os.fdopen(descriptor, 'rb') as stream:
                        info = os.fstat(stream.fileno())
                        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
                            raise ValueError('native session header ownership changed')
                        text = stream.readline(MAX_HEADER + 1)
                    if len(text) > MAX_HEADER:
                        raise ValueError('native session header exceeds 64 KiB')
                    row = json.loads(text)
                    payload = row.get('payload', {})
                    if row.get('type') != 'session_meta' or payload.get('id') != identifier:
                        raise ValueError('native session header does not match the actual subagent')
                    source = payload.get('source', {}).get('subagent', {}).get('thread_spawn', {})
                    parent_id = source.get('parent_thread_id')
                    if not isinstance(parent_id, str) or not UUID.fullmatch(parent_id) or parent_id == identifier:
                        raise ValueError('native subagent header has no distinct parent')
                    return parent_id
        except FileNotFoundError:
            continue
    raise ValueError('native subagent parent provenance is not available')


def belongs(event: dict, identifier: str) -> None:
    """Refuse an ambient child whose native parent chain does not reach the event's root."""
    root = event.get('session_id')
    cursor = identifier
    for _ in range(8):
        cursor = parent({**event, 'agent_id': cursor})
        if cursor == root:
            return
    raise ValueError('native child lineage does not reach the event session within eight parents')
