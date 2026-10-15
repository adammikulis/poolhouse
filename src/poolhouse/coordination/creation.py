"""Durable evidence for owned creation and cleanup, including ambiguous outcomes."""

import json
import os
import re
import time
from pathlib import Path

from poolhouse.coordination.docker import Docker, Refused
from poolhouse.coordination.plan import Plan


class Unfinished(Refused):
    """Owned resources must remain while cleanup cannot be confirmed."""


class Creation:
    def __init__(self, root: Path, plan: Plan):
        self.plan = plan
        self.identifiers: dict[str, str] = {}
        self.pending: str = ''
        self.path = root / f'creation-{plan.identifier}.jsonl'
        self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        prepared = False
        try:
            self.record('prepared')
            prepared = True
        finally:
            if not prepared:
                os.close(self.fd)

    def record(self, phase: str) -> None:
        body = json.dumps({'owner': self.plan.owner, 'name': self.plan.name,
                           'nonce': self.plan.identifier, 'time': time.time(), 'phase': phase,
                           'known_ids': self.identifiers, 'unconfirmed_kind': self.pending}) + '\n'
        pending = body.encode('utf-8')
        while pending:
            written = os.write(self.fd, pending)
            if written <= 0:
                raise OSError('The creation evidence could not be saved.')
            pending = pending[written:]
        os.fsync(self.fd)

    def create(self, docker: Docker, kind: str, argv: list[str]) -> str:
        self.pending = kind
        self.record('creating')
        identifier = docker.call(argv).stdout.strip()
        if not re.fullmatch('[0-9a-f]{64}', identifier):
            raise Unfinished('The creation result did not provide an exact Docker resource identifier.')
        self.identifiers[kind] = identifier
        self.pending = ''
        self.record('created')
        return identifier

    def removed(self, kind: str) -> None:
        self.identifiers.pop(kind)
        self.record('removed')

    def close(self) -> None:
        try:
            self.record('cleanup_required' if self.identifiers or self.pending else 'cleaned')
        finally:
            os.close(self.fd)
