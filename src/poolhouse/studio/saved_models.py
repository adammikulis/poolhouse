"""Resolve completed checkpoint references under the current caller's authority."""
from __future__ import annotations

import copy
import hashlib
import stat
from pathlib import Path

from .catalog import is_model_type, node_spec
from .engine import artifact_fingerprints
from .resources import base_fingerprint
from .store import digest

PREFIX = 'studio-output:'


class SavedModels:
    """Discover persisted producer outputs without accepting paths or worker grants."""

    def __init__(self, store, files_root: Path, authorize):
        self.store, self.files_root, self.authorize = store, files_root.absolute(), authorize

    @staticmethod
    def reference(run: dict, node: dict, port: str) -> str:
        return PREFIX + digest({'run': run['id'], 'node': node['id'], 'port': port})

    def _access(self, principal: dict, run: dict, node: dict, seen: frozenset[str]):
        if not principal.get('owner') and run['principal']['id'] != principal['id']:
            raise PermissionError('This checkpoint belongs to another session')
        state = run['nodes'][node['id']]
        if state['state'] != 'done' or node.get('parameters', {}).get('mode') == 'smoke':
            raise ValueError('Select a completed checkpoint-producing run')
        inputs = {edge['target']['port']: run['nodes'][edge['source']['node']]['outputs'][edge['source']['port']]
                  for edge in run['graph']['edges'] if edge['target']['node'] == node['id']}
        if not self.authorize(principal, node, inputs):
            raise PermissionError('This checkpoint is no longer authorized')
        self.check_inputs(principal, inputs, seen)
        if node['type'] == 'train.tool-calls' and state['outputs'].get('model', {}).get('base') != inputs.get('model'):
            raise ValueError('The checkpoint base does not match its producer input')
        if state.get('output_digest') != digest(state['outputs']):
            raise ValueError('The checkpoint output identity changed')
        return state

    def _output(self, principal, run, node, port, seen):
        state = self._access(principal, run, node, seen)
        expected = state
        visited = {run['id']}
        while state.get('reused_from'):
            source = state['reused_from']
            if source in visited:
                raise ValueError('Checkpoint lineage contains a cycle')
            visited.add(source)
            run = self.store.get('run', source, principal['scope'])
            node = next(item for item in run['graph']['nodes'] if item['id'] == node['id'])
            state = self._access(principal, run, node, seen)
            if state.get('fingerprint') != expected.get('fingerprint') or state['outputs'] != expected['outputs']:
                raise ValueError('Checkpoint lineage does not match its producer')
        handle = state['outputs'].get(port)
        if not isinstance(handle, dict) or handle.get('kind') != 'model' or handle.get('id'):
            raise ValueError('Select a declared trained checkpoint output')
        owned = self.files_root / 'studio' / run['id'] / hashlib.sha256(node['id'].encode()).hexdigest()[:24]
        path = Path(handle.get('path', '')).absolute()
        if '..' in path.parts or not path.is_relative_to(owned):
            raise ValueError('The checkpoint does not belong to its producer')
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError('A saved checkpoint cannot contain directory links')
        if path.is_dir():
            base_fingerprint(handle)
        else:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('A saved checkpoint must be a regular owned artifact')
        actual = artifact_fingerprints(handle)
        if not actual or any(state.get('artifacts', {}).get(name) != checksum for name, checksum in actual.items()):
            raise ValueError('The checkpoint or its base content changed')
        return copy.deepcopy(handle)

    def resolve(self, principal: dict, ident: str, seen: frozenset[str] = frozenset()) -> dict:
        if not isinstance(ident, str) or not ident.startswith(PREFIX) or len(ident) != len(PREFIX) + 64:
            raise ValueError('Select a saved Studio checkpoint')
        if ident in seen:
            raise ValueError('Checkpoint resource references contain a cycle')
        seen = seen | {ident}
        for run in self.store.records('run', principal['scope']):
            for node in run['graph']['nodes']:
                if node['type'] not in {'train.tool-calls', 'train.classify-text', 'train.text-lm', 'train.decision', 'gym.train'}:
                    continue
                for port in node_spec(node['type'], node.get('version', 1))['outputs']:
                    if is_model_type(port['type']) and self.reference(run, node, port['name']) == ident:
                        handle = self._output(principal, run, node, port['name'], seen)
                        return {**handle, 'studio_reference': ident, 'studio_type': port['type']}
        raise ValueError('The saved checkpoint is unavailable')

    def check_inputs(self, principal: dict, inputs: dict, seen: frozenset[str] = frozenset()) -> None:
        for handle in inputs.values():
            if isinstance(handle, dict) and 'studio_reference' in handle:
                expected = self.resolve(principal, handle['studio_reference'], seen)
                if handle != expected:
                    raise ValueError('The saved checkpoint handle changed')

    def list(self, principal: dict) -> list[dict]:
        rows = []
        for run in self.store.records('run', principal['scope']):
            for node in run['graph']['nodes']:
                if not node['type'].startswith('train.') and node['type'] != 'gym.train':
                    continue
                for port in node_spec(node['type'], node.get('version', 1))['outputs']:
                    if not is_model_type(port['type']):
                        continue
                    ident = self.reference(run, node, port['name'])
                    try:
                        self.resolve(principal, ident)
                    except (ValueError, PermissionError, OSError, KeyError):
                        continue
                    rows.append({'id': ident, 'title': f"{run.get('title', 'Studio run')} · {node['id']} · {run['id'][:8]}",
                                 'type': port['type'], 'source': 'studio'})
        return rows
