"""Current-owner preview imports through durable Studio runs and immutable resources."""
from __future__ import annotations

import copy
import hashlib
import re
from contextlib import contextmanager
from pathlib import Path

from poolhouse.studio import dataset_assets as assets, dataset_samples as samples
from poolhouse.studio.contracts import validate_graph
from poolhouse.studio.store import Conflict, digest

from . import dataset_catalogue, studio_epoch

PREFIX = 'datasets/huggingface/'
KIND = 'data.hf-preview'
NODE = 'dataset'


class DatasetImports:
    def __init__(self, engine, authority):
        self.engine, self.authority, self.store = engine, authority, engine.store
        self.root = engine.runner.files_root.absolute()

    def _current(self, principal, row):
        if self.engine.authorize(principal, None, {}) is not True:
            raise PermissionError('Sign in again before importing dataset rows.')
        if principal.get('kind') == 'studio_subscription':
            if self.authority.authorize(principal, None, {}) is not True:
                raise PermissionError('This dataset is outside the current execution grant.')
            grant = self.authority.get(principal)
            expected = {'kind': 'dataset', 'id': PREFIX + row['id'] + '/dataset.jsonl'}
            checked = validate_graph(grant['manifest']['workflow']['graph'])
            if checked['valid'] is not True:
                raise PermissionError('This dataset execution manifest is invalid.')
            nodes = checked['graph']['nodes']
            if not any(node.get('type') == 'dataset' and node.get('bindings', {}).get('dataset') == expected for node in nodes):
                raise PermissionError('This dataset is outside the current execution grant.')
            context = self.authority._context()
        else:
            context = self.authority._owner(principal)
        if context != row['context'] or studio_epoch.generation(self.authority.root, context['pool']) != row['epoch']:
            raise PermissionError('This dataset import authorization changed.')
        return context

    @contextmanager
    def _barrier(self, principal, row):
        with self.store.lock, studio_epoch.changing(self.authority.root) as (_fd, epochs):
            self._current(principal, row)
            if epochs['pools'].get(row['context']['pool'], {}).get('epoch') != row['epoch']:
                raise PermissionError('This dataset import authorization changed.')
            yield
            self._current(principal, row)

    def _request(self, principal, ident):
        if not isinstance(ident, str) or not samples.HEX.fullmatch(ident):
            raise ValueError('Choose an existing dataset import.')
        row = self.store.get('dataset-import', ident, principal['scope'])
        body = samples.checked_body(row['body'])
        if (row['id'] != digest({'principal': row['principal'], 'key': body['idempotency_key'], 'kind': KIND})
                or row['body_digest'] != digest(body)
                or row['payload_digest'] != digest({'data': row['data'], 'manifest': row['manifest']})):
            raise ValueError('The immutable dataset import request changed.')
        self._current(principal, row)
        return row

    def check_node(self, principal, node):
        if node.get('type') == KIND:
            self._request(principal, node.get('parameters', {}).get('request_id'))
        return True

    def check_resources(self, principal, node):
        self.check_node(principal, node or {})
        for resource in (node or {}).get('bindings', {}).values():
            if resource.get('kind') == 'dataset':
                self.resolve(resource, {'principal': principal})

    def submit_preview(self, principal, body):
        body = samples.checked_body(body)
        context = self.authority._owner(principal)
        epoch = self.authority.selection_epoch(principal, create=True)
        anchor = {'context': context, 'epoch': epoch}
        self._current(principal, anchor)
        ident = digest({'principal': principal, 'key': body['idempotency_key'], 'kind': KIND})
        with self.store.lock:
            try:
                prior = self._request(principal, ident)
            except KeyError:
                prior = None
            if prior is not None:
                if prior['body_digest'] != digest(body):
                    raise Conflict('This dataset import identity already names a different selection.')
                return self.store.get('run', prior['run_id'], principal['scope'])
        failure = None
        try:
            preview = dataset_catalogue.preview(body['repo'], body['config'], body['split'])
        except (ValueError, OSError, RuntimeError) as exc:
            failure = exc
        self._current(principal, anchor)
        if failure is not None:
            raise ValueError('The selected dataset preview is unavailable. Refresh it before importing.') from None
        data, manifest = samples.selected(body, preview)
        row = {'id': ident, 'scope': principal['scope'], 'title': body['name'], 'principal': copy.deepcopy(principal),
               'context': copy.deepcopy(context), 'epoch': epoch, 'body': body, 'body_digest': digest(body),
               'data': data.decode(), 'manifest': manifest,
               'payload_digest': digest({'data': data.decode(), 'manifest': manifest}), 'run_id': None, 'node_id': NODE}
        graph = {'nodes': [{'id': NODE, 'type': KIND, 'parameters': {'request_id': ident}}], 'edges': []}
        workflow = {'id': ident[:32], 'revision': 1, 'title': body['name'], 'graph': graph}
        # The scheduler uses this same engine lock: the wake cannot expose a half-bound request.
        with self.engine.lock, self._barrier(principal, row), self.store.graph.transaction():
            try:
                prior = self._request(principal, ident)
            except KeyError:
                prior = None
            if prior is not None:
                if prior['body_digest'] != row['body_digest']:
                    raise Conflict('This dataset import identity already names a different selection.')
                return self.store.get('run', prior['run_id'], principal['scope'])
            self.store.put('dataset-import', row)
            run = self.engine.submit(principal, workflow, [NODE], 'dataset-preview:' + ident)
            row['run_id'] = run['id']
            self.store.put('dataset-import', row)
            self.store.graph.put_doc('studio.dataset-run:' + run['id'], {'scope': principal['scope'], 'request_id': ident})
        return run

    def _from_run(self, principal, ident):
        if not isinstance(ident, str) or not re.fullmatch('[0-9a-f]{32}', ident):
            raise ValueError('Choose an existing dataset import run.')
        with self.store.lock:
            pointer = self.store.graph.get_doc('studio.dataset-run:' + ident)
            if not pointer or pointer.get('scope') != principal['scope']:
                raise KeyError('No such dataset import.')
            row = self._request(principal, pointer['request_id'])
            run = self.store.get('run', ident, principal['scope'])
        if (row['run_id'] != ident or row['node_id'] != NODE or run['principal'] != row['principal']
                or run['workflow_id'] != row['id'][:32] or len(run['graph']['nodes']) != 1
                or run['graph']['nodes'][0]['id'] != NODE or run['graph']['nodes'][0]['type'] != KIND
                or run['graph']['nodes'][0]['parameters'] != {'request_id': row['id']}):
            raise PermissionError('The dataset request does not belong to this run.')
        return row, run

    def _bound_run(self, principal, ident):
        try:
            return self._from_run(principal, ident)
        except KeyError:
            raise PermissionError('This run has no authorized dataset import request.') from None

    def _outputs(self, row, run):
        relative = Path('studio') / run['id'] / hashlib.sha256(NODE.encode()).hexdigest()[:24] / 'dataset'
        resource = PREFIX + row['id'] + '/dataset.jsonl'
        return {'dataset': {'kind': 'dataset', 'path': str(self.root / relative / 'dataset.jsonl'), 'id': resource,
                            'rows': row['manifest']['rows'], 'schema': row['manifest']['columns'], 'source': 'huggingface-preview'},
                'artifact': {'kind': 'artifact', 'path': str(self.root / relative)}}

    def execute(self, node, context):
        row, run = self._bound_run(context['principal'], context['run_id'])
        if (context['principal'] != row['principal'] or context['node_id'] != NODE
                or node != run['graph']['nodes'][0] or run['state'] not in {'queued', 'running'}
                or run['nodes'][NODE]['state'] not in {'pending', 'running'}):
            raise PermissionError('This dataset request cannot execute in another run.')
        outputs = self._outputs(row, run)
        artifact = Path(outputs['artifact']['path']).relative_to(self.root)
        if Path(context['output_dir']) != (self.root / artifact).parent:
            raise PermissionError('The dataset output directory changed.')
        with self._barrier(context['principal'], row):
            assets.publish(self.root, artifact, row['data'].encode(), row['manifest'])
        return outputs

    def _producer(self, principal, row, run, *, running=False):
        state = run['nodes'][NODE]
        if (state['state'] != 'done' or run['state'] not in ({'running', 'done'} if running else {'done'})
                or state.get('reused_from') or state['outputs'] != self._outputs(row, run)
                or state.get('output_digest') != digest(state['outputs'])):
            raise ValueError('Choose a completed preview-row import.')
        self._current(principal, row)
        relative = Path(state['outputs']['artifact']['path']).relative_to(self.root)
        with assets.folder(self.root, relative) as directory:
            data, manifest = assets.verify(directory, row['manifest'])
        if data != row['data'].encode():
            raise ValueError('The imported dataset no longer matches its selected rows.')
        self._current(principal, row)
        return data, manifest

    def completed(self, run, node, state, inputs):
        if node['type'] != KIND:
            return
        row, persisted = self._bound_run(run['principal'], run['id'])
        if inputs or state != persisted['nodes'][NODE]:
            raise ValueError('The dataset producer completion changed.')
        data, manifest = self._producer(run['principal'], row, persisted, running=True)
        resource = self._outputs(row, persisted)['dataset']['id']
        with self._barrier(run['principal'], row), self.store.graph.transaction():
            assets.publish(self.root, Path(resource).parent, data, manifest)
            index = {'id': digest({'scope': row['scope'], 'resource': resource}), 'scope': row['scope'],
                     'resource_id': resource, 'request_id': row['id'], 'run_id': run['id'], 'title': row['title']}
            self.store.put('dataset-index', index)

    def resolve(self, binding, context):
        ident = binding.get('id', '')
        if not isinstance(ident, str):
            return None
        if assets.reserved(ident) and not ident.startswith(PREFIX):
            raise ValueError('Choose a registered dataset resource, not a producer artifact path.')
        if not ident.startswith(PREFIX):
            path = self.root / ident
            if assets.reserved(path.resolve().relative_to(self.root.resolve())):
                raise ValueError('Choose a registered dataset resource, not an alias.')
            return None
        if not re.fullmatch(re.escape(PREFIX) + '[0-9a-f]{64}/dataset.jsonl', ident):
            raise ValueError('Choose a registered imported dataset.')
        principal = context['principal']
        key = digest({'scope': principal['scope'], 'resource': ident})
        try:
            index = self.store.get('dataset-index', key, principal['scope'])
        except KeyError:
            raise ValueError('This imported dataset is unavailable.') from None
        row, run = self._from_run(principal, index['run_id'])
        if index['resource_id'] != ident or index['request_id'] != row['id'] or ident != self._outputs(row, run)['dataset']['id']:
            raise ValueError('The dataset registration changed.')
        self._producer(principal, row, run)
        with assets.folder(self.root, Path(ident).parent) as directory:
            assets.verify(directory, row['manifest'])
        self._current(principal, row)
        return {'kind': 'dataset', 'id': ident, 'path': str(self.root / ident),
                'rows': row['manifest']['rows'], 'schema': copy.deepcopy(row['manifest']['columns']), 'source': 'huggingface-preview'}

    def resources(self, principal):
        result = []
        for index in self.store.records('dataset-index', principal['scope']):
            try:
                handle = self.resolve({'kind': 'dataset', 'id': index['resource_id']}, {'principal': principal})
            except (ValueError, OSError, KeyError):
                continue
            result.append({'id': handle['id'], 'title': index['title'], 'rows': handle['rows'],
                           'columns': handle['schema'], 'source': 'huggingface-preview'})
        return result

    @staticmethod
    def public_run(run):
        result = {key: run[key] for key in ('id', 'state', 'created_at', 'finished_at') if key in run}
        result['nodes'] = {ident: {'state': state['state']} for ident, state in run['nodes'].items()}
        if run['state'] in {'failed', 'blocked', 'interrupted'}:
            result['error'] = 'The selected preview rows were not made available as a dataset.'
        return result

    def status(self, principal, ident):
        row, run = self._from_run(principal, ident)
        dataset = None
        if run['state'] == 'done':
            resource = self._outputs(row, run)['dataset']['id']
            handle = self.resolve({'kind': 'dataset', 'id': resource}, {'principal': principal})
            dataset = {'id': resource, 'title': row['title'], 'rows': handle['rows'], 'columns': handle['schema'],
                       'source': 'huggingface-preview', 'preview_digest': row['body']['expected_preview_digest']}
        return {'run': self.public_run(run), 'dataset': dataset}

    def cancel(self, principal, ident):
        self._from_run(principal, ident)
        return {'run': self.public_run(self.engine.cancel(principal, ident))}


def create(ui, engine):
    service = DatasetImports(engine, ui.studio_authority)
    ui.studio_datasets = service
    ui.runner.studio_dataset_import = service.execute
    ui.runner.studio_dataset_resource = service.resolve
    ui.runner.studio_dataset_completed = service.completed
    return service
