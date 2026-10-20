"""Authenticated, fixed Hugging Face catalogue reads for the Data screen."""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urlsplit

from poolhouse.graph.guard import MAX_BODY
from poolhouse.json_objects import unique_object
from poolhouse.studio.dataset_samples import preview_digest
from poolhouse.studio.store import Conflict

from . import dataset_catalogue, dataset_preview

PREFIX = '/ui/workspace/datasets/'
FIELDS = {
    'search': {'query', 'task', 'language', 'sort', 'cursor'},
    'details': {'repo'},
    'preview': {'repo', 'config', 'split'},
    'selection': {'repo', 'config', 'split'},
    'local-preview': {'path'},
}


def _current(engine, principal):
    if engine.authorize(principal, None, {}) is not True:
        raise PermissionError('Sign in again to browse datasets.')


def route(handler):
    """Called only after the existing UI authorization guard, never as an open route."""
    if not handler.path.startswith(PREFIX):
        return False
    action = handler.path[len(PREFIX):]
    if action == 'preview-imports' or action.startswith('imports/'):
        try:
            return _imports(handler, action)
        except PermissionError:
            handler.send(403, {'error': 'Sign in again to import dataset rows.'})
            return True
        except Conflict as exc:
            handler.send(409, {'error': str(exc)})
            return True
        except KeyError:
            handler.send(404, {'error': 'No such dataset import.'})
            return True
    if action not in FIELDS:
        return False
    if handler.method != 'GET':
        handler.send(405, {'error': 'Dataset browsing uses GET.'})
        return True
    try:
        return _read(handler, action)
    except PermissionError:
        handler.send(403, {'error': 'Sign in again to browse datasets.'})
        return True


def _read(handler, action):
    engine, principal = handler._service()
    _current(engine, principal)
    entries = parse_qsl(urlsplit(handler.handler.path).query, keep_blank_values=True,
                      errors='strict', max_num_fields=len(FIELDS[action]))
    query = dict(entries)
    if len(query) != len(entries) or set(query) - FIELDS[action]:
        raise ValueError('Choose only the supported dataset search fields once.')
    failure = None
    try:
        if action == 'local-preview':
            binding = {'kind': 'dataset', 'id': query.get('path', '')}
            if binding['id'].startswith('datasets/huggingface/'):
                handler.ui.studio_datasets.resolve(binding, {'principal': principal})
            result = dataset_preview.preview(binding['id'], handler.ui.runner.files_root)
        elif action == 'search':
            result = dataset_catalogue.search(**query)
        elif action == 'details':
            result = dataset_catalogue.details(query.get('repo', ''))
        elif action == 'selection':
            result = dataset_catalogue.selection(query.get('repo', ''), query.get('config', ''), query.get('split', ''))
        else:
            result = dataset_catalogue.preview(query.get('repo', ''), query.get('config', ''), query.get('split', ''))
    except (ValueError, OSError, RuntimeError) as exc:
        failure = exc
    _current(engine, principal)
    if isinstance(failure, PermissionError):
        raise PermissionError('This dataset is no longer authorized.') from None
    if failure is not None:
        raise ValueError('Dataset information is unavailable. Check the selection and try again.') from None
    if action == 'preview':
        result = {**result, 'preview_digest': preview_digest(
            query.get('repo', ''), query.get('config', ''), query.get('split', ''), result)}
    handler.send(200, result)
    return True


def _imports(handler, action):
    engine, principal = handler._service()
    _current(engine, principal)
    service = handler.ui.studio_datasets
    if handler.query:
        raise ValueError('Dataset import commands do not accept query fields.')
    if action == 'preview-imports' and handler.method == 'POST':
        length = int(handler.header('Content-Length', '0') or 0)
        if not 0 < length <= MAX_BODY:
            raise ValueError('Choose a dataset import within the record budget.')
        try:
            body = json.loads(handler.handler.rfile.read(length), object_pairs_hook=unique_object)
        except (ValueError, RecursionError):
            raise ValueError('Choose a valid dataset import object.') from None
        run = service.submit_preview(principal, body)
        handler.send(202, {'run': service.public_run(run)})
        return True
    parts = action.split('/')
    if len(parts) in (2, 3) and parts[0] == 'imports' and re.fullmatch('[0-9a-f]{32}', parts[1]):
        if len(parts) == 2 and handler.method == 'GET':
            handler.send(200, service.status(principal, parts[1]))
            return True
        if parts[2:] == ['cancel'] and handler.method == 'POST':
            handler.send(200, service.cancel(principal, parts[1]))
            return True
    handler.send(405, {'error': 'Choose a supported dataset import action.'})
    return True
