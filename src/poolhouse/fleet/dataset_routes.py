"""Authenticated, fixed Hugging Face catalogue reads for the Data screen."""
from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

from poolhouse.studio.store import digest

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
            result = dataset_preview.preview(query.get('path', ''), handler.ui.runner.files_root)
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
    if failure is not None:
        raise ValueError('Dataset information is unavailable. Check the selection and try again.') from None
    if action == 'preview':
        result = {**result, 'preview_digest': digest({
            'repo': query.get('repo', ''), 'config': query.get('config', ''), 'split': query.get('split', ''),
            'columns': result['columns'], 'rows': result['rows']})}
    handler.send(200, result)
    return True
