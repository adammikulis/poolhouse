"""Fixed coding-folder projection of one-use native selection proofs."""
from __future__ import annotations

import secrets
from types import SimpleNamespace

from poolhouse.studio.store import digest

from .native_picks import NativePicks

PURPOSE = 'coding-project'


class Picker:
    """The setup route owns principals; this adapter never accepts authority in request data."""

    def __init__(self, projects, *, epoch, current):
        self.projects, self.current = projects, current
        self.pending = {}
        records = SimpleNamespace(purpose=PURPOSE, lock=projects.lock,
                                  read=lambda ident: projects._read('native:' + ident),
                                  write=lambda row: projects._write('native:' + row['id'], row))
        self.picks = NativePicks(records, epoch=epoch, current=current, create_resource=self._select)

    def _select(self, principal, path, mode, ident):
        row = self.picks.records.read(ident)
        if row is None or row['principal'] != principal or row['state'] != 'completing' or mode != 'folder':
            raise PermissionError('This folder selection ended.')
        result = self.projects.select(path, ident, current=lambda: self.selection_current(row))
        return result['project']

    def request(self, principal):
        with self.projects.lock:
            previous = self.pending.get(principal['id'])
            if previous is not None:
                row = self.picks._get(previous['request_id'], previous['nonce'])
                if row['state'] in {'issued', 'choosing', 'completing'}:
                    return dict(previous)
            response = self.picks.request(principal, 'folder', secrets.token_hex(16))
            row = self.picks.records.read(response['request_id'])
            row['selection_revision'] = self.projects.registry.coding_revision(
                current=lambda: self.current(principal, row['epoch']))
            self.picks.records.write(row)
            self.pending[principal['id']] = response
            return response

    def _public(self, response):
        public = {'status': response['status'], 'purpose': PURPOSE}
        if 'source' in response:
            project = response['source']
            candidate = self.projects._read(project['id'])
            public['status'] = 'needs-project' if candidate and candidate['state'] != 'complete' else 'complete'
            public['project'] = candidate['project'] if candidate and candidate['state'] == 'complete' else project
        if 'claim' in response:
            public.update(mode='folder', claim=response['claim'])
        return public

    def claim(self, ident, nonce):
        return self._public(self.picks.begin(ident, nonce))

    @staticmethod
    def operation(row):
        return digest({'principal': row['principal'], 'request': row['id']})[:32]

    def selection_current(self, row):
        return self.projects.registry.coding_operation_current(
            self.operation(row), row['selection_revision'],
            current=lambda: self.current(row['principal'], row['epoch']))

    def _selected(self, project, row):
        return self.projects.registry.select_coding(
            project['id'], self.operation(row), row['selection_revision'],
            current=lambda: self.current(row['principal'], row['epoch']))

    def complete(self, ident, nonce, claim, path):
        response = self._public(self.picks.complete(ident, nonce, claim, path))
        if response['status'] == 'complete':
            selected = self._selected(response['project'], self.picks.records.read(ident))
            if selected['current'] is not True:
                return {'status': 'cancelled', 'purpose': PURPOSE}
        return response

    def cancel(self, ident, nonce, claim):
        return self._public(self.picks.cancel(ident, nonce, claim))

    def create(self, principal, candidate):
        """A candidate ID confers nothing: require its original still-current owner binding."""
        with self.projects.lock:
            held = self.pending.get(principal['id'])
            if held is None or held['request_id'] != candidate:
                raise PermissionError('Choose this folder again before creating a project.')
            row = self.picks._get(candidate, held['nonce'])
            if (row is None or row['state'] != 'complete' or row['principal'] != principal
                    or not self.current(principal, row['epoch'])):
                raise PermissionError('Choose this folder again before creating a project.')
            receipt = self.projects.registry.coding_operation(self.operation(row),
                current=lambda: self.current(principal, row['epoch']))
            if receipt is not None:
                project = self.projects.registry.get(receipt['id'])
                return {'status':'complete', 'purpose':PURPOSE, 'project':{'id':project.id, 'title':project.name},
                        'selected':receipt['current']}
            project = self.projects.create(candidate, current=lambda: self.selection_current(row))
            selected = self._selected(project, row)
            return {'status': 'complete', 'purpose': PURPOSE, 'project': project, 'selected':selected['current']}
