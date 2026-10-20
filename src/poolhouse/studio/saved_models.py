"""Resolve completed checkpoint references under the current caller's authority."""
from __future__ import annotations

import copy
import hashlib
import stat
from pathlib import Path

from .catalog import is_model_type, node_spec
from .contracts import MAX_DEPTH, MAX_NODES
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

    def _authorize(self, principal, run, node, inputs, seen):
        if not principal.get('owner') and run['principal']['id'] != principal['id']:
            raise PermissionError('This checkpoint belongs to another session')
        if not self.authorize(principal, node, inputs):
            raise PermissionError('This checkpoint is no longer authorized')
        self.check_inputs(principal, inputs, seen)

    def _access(self, principal: dict, run: dict, node: dict, seen: frozenset[str]):
        state = run['nodes'][node['id']]
        if state['state'] != 'done' or node.get('parameters', {}).get('mode') == 'smoke':
            raise ValueError('Select a completed checkpoint-producing run')
        inputs = {edge['target']['port']: run['nodes'][edge['source']['node']]['outputs'][edge['source']['port']]
                  for edge in run['graph']['edges'] if edge['target']['node'] == node['id']}
        self._authorize(principal, run, node, inputs, seen)
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
        try:
            source = self.store.get('model-reference', ident, principal['scope'])['source']
            run, node, port = self._source(principal, ident, source)
            handle = self._output(principal, run, node, port['name'], seen)
            return {**handle, 'studio_reference': ident, 'studio_type': port['type']}
        except KeyError as exc:
            raise ValueError('The saved checkpoint is unavailable') from exc

    def _source(self, principal, ident, source):
        if not isinstance(source, dict) or set(source) != {'run', 'node', 'port'}:
            raise ValueError('Invalid saved checkpoint source')
        run = self.store.get('run', source['run'], principal['scope'])
        node = next((item for item in run['graph']['nodes'] if item['id'] == source['node']), None)
        if node is None or node['type'] not in {
                'train.tool-calls', 'train.classify-text', 'train.text-lm', 'train.decision', 'gym.train'}:
            raise ValueError('Invalid saved checkpoint producer')
        port = next((item for item in node_spec(node['type'], node.get('version', 1))['outputs']
                     if item['name'] == source['port'] and is_model_type(item['type'])), None)
        if port is None or self.reference(run, node, port['name']) != ident:
            raise ValueError('The saved checkpoint reference changed')
        return run, node, port

    def register(self, run, node, port):
        """Index a server-owned producer tuple; resolution still checks current authority."""
        ident = self.reference(run, node, port)
        source = {'run': run['id'], 'node': node['id'], 'port': port}
        self._source({'scope': run['scope']}, ident, source)
        self.store.put('model-reference', {'id': ident, 'scope': run['scope'], 'source': source})
        return ident

    @staticmethod
    def _lineage_row(run, node, port):
        state = run['nodes'][node['id']]
        inputs = {edge['target']['port']: run['nodes'][edge['source']['node']]['outputs'][edge['source']['port']]
                  for edge in run['graph']['edges'] if edge['target']['node'] == node['id']}
        return {'run': run['id'], 'node': node['id'], 'port': port,
                'inputs_digest': digest(inputs), 'reused_from': state.get('reused_from'),
                'node_digest': digest(node), 'output_digest': state.get('output_digest'),
                'fingerprint': state.get('fingerprint'), 'artifacts': copy.deepcopy(state.get('artifacts', {}))}

    def pin(self, principal, ident, _seen=frozenset()):
        """Snapshot a currently authorized resource for an exact service grant."""
        if ident in _seen:
            raise ValueError('Checkpoint resource references contain a cycle')
        if len(_seen) >= MAX_DEPTH:
            raise ValueError('Checkpoint dependency graph is too large')
        handle = self.resolve(principal, ident, _seen)
        source = self.store.get('model-reference', ident, principal['scope'])['source']
        run, node, port = self._source(principal, ident, source)
        lineage, dependencies, visited = [], {}, set()
        while True:
            if run['id'] in visited or len(lineage) >= MAX_NODES:
                raise ValueError('Checkpoint lineage contains a cycle or is too large')
            visited.add(run['id'])
            self._access(principal, run, node, _seen | {ident})
            lineage.append(self._lineage_row(run, node, port['name']))
            for edge in run['graph']['edges']:
                if edge['target']['node'] != node['id']:
                    continue
                value = run['nodes'][edge['source']['node']]['outputs'][edge['source']['port']]
                if isinstance(value, dict) and 'studio_reference' in value:
                    ref = value['studio_reference']
                    dependencies[ref] = self.pin(principal, ref, _seen | {ident})
            origin = run['nodes'][node['id']].get('reused_from')
            if not origin:
                break
            run = self.store.get('run', origin, principal['scope'])
            node = next(item for item in run['graph']['nodes'] if item['id'] == source['node'])
        proof = {'kind': 'saved_model', 'id': ident, 'source': source, 'lineage': lineage,
                 'handle': handle, 'dependencies': list(dependencies.values())}
        proof['digest'] = digest(proof)
        # Detect concurrent changes rather than pinning a mixture of producer versions.
        self.resolve_granted(principal, proof, authorize=lambda caller, _proof, producer, inputs: producer is None or self.authorize(caller, producer, inputs) is True)
        return proof

    def resolve_granted(self, principal, proof, *, authorize):
        """Resolve only pinned tuples with a current, mandatory grant/resource verifier."""
        return _GrantedModels(self, proof, authorize).resolve(principal, proof.get('id'))

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
                    try:
                        ident = self.register(run, node, port['name'])
                        self.resolve(principal, ident)
                    except (ValueError, PermissionError, OSError, KeyError):
                        continue
                    rows.append({'id': ident, 'title': f"{run.get('title', 'Studio run')} · {node['id']} · {run['id'][:8]}",
                                 'type': port['type'], 'source': 'studio'})
        return rows


class _GrantedModels(SavedModels):
    """Per-call verifier; no mutable authority state is shared between requests."""

    def __init__(self, registry, proof, authorize):
        super().__init__(registry.store, registry.files_root, authorize)
        self.proofs = {}
        pending = [(proof, 0)]
        while pending:
            item, depth = pending.pop()
            if depth >= MAX_DEPTH:
                raise ValueError('Checkpoint dependency graph is too deep')
            if not isinstance(item, dict) or set(item) != {
                    'kind', 'id', 'source', 'lineage', 'handle', 'dependencies', 'digest'}:
                raise ValueError('Invalid saved checkpoint proof')
            if item['kind'] != 'saved_model' or item['digest'] != digest({k: v for k, v in item.items() if k != 'digest'}):
                raise ValueError('The saved checkpoint proof changed')
            if item['id'] in self.proofs:
                if item != self.proofs[item['id']]:
                    raise ValueError('Conflicting checkpoint dependency proofs')
                continue
            if len(self.proofs) + len(item['lineage']) >= MAX_NODES:
                raise ValueError('Checkpoint dependency graph is too large')
            if not isinstance(item['dependencies'], list) or not isinstance(item['lineage'], list) or not item['lineage']:
                raise ValueError('Invalid saved checkpoint dependencies')
            self.proofs[item['id']] = item
            pending.extend((child, depth + 1) for child in item['dependencies'])
        self.active = None

    def _authorize(self, principal, run, node, inputs, seen):
        proof = self.proofs[self.active]
        record = self._lineage_row(run, node, proof['source']['port'])
        if record not in proof['lineage']:
            raise ValueError('The pinned checkpoint lineage changed')
        if self.authorize(principal, proof, node, inputs) is not True:
            raise PermissionError('This checkpoint grant is no longer authorized')
        self.check_inputs(principal, inputs, seen)

    def resolve(self, principal, ident, seen=frozenset()):
        if ident in seen or ident not in self.proofs:
            raise ValueError('Checkpoint dependency is missing or contains a cycle')
        proof = self.proofs[ident]
        previous, self.active = self.active, ident
        try:
            if self.authorize(principal, proof, None, {}) is not True:
                raise PermissionError('This checkpoint grant is no longer authorized')
            run, node, port = self._source(principal, ident, proof['source'])
            # Both pre/post verification traverse only the pinned producer primary keys.
            for record in proof['lineage']:
                current = self.store.get('run', record['run'], principal['scope'])
                producer = next(item for item in current['graph']['nodes'] if item['id'] == record['node'])
                self._access(principal, current, producer, seen | {ident})
            handle = self._output(principal, run, node, port['name'], seen | {ident})
            handle.update(studio_reference=ident, studio_type=port['type'])
            if handle != proof['handle']:
                raise ValueError('The pinned checkpoint handle changed')
            for record in proof['lineage']:
                current = self.store.get('run', record['run'], principal['scope'])
                producer = next(item for item in current['graph']['nodes'] if item['id'] == record['node'])
                self._access(principal, current, producer, seen | {ident})
            return handle
        finally:
            self.active = previous
