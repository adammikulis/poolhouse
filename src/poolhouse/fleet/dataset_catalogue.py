"""Public Hugging Face dataset discovery and immutable Parquet selections."""
from __future__ import annotations

import json
import math
import re
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from poolhouse import http, net
from poolhouse.json_objects import unique_object

HUB = 'https://huggingface.co/api/datasets'
VIEWER = 'https://datasets-server.huggingface.co'
TASKS = ('text-generation', 'text-classification', 'question-answering', 'translation', 'summarization', 'token-classification')
SORTS = ('downloads', 'likes', 'lastModified')
_PART = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}')
_COMMIT = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')
_SHA = re.compile(r'[0-9a-f]{64}')


def _text(value, limit=256, multiline=False):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 and (not multiline or c not in '\n\r\t') for c in value):
        raise ValueError('The dataset catalogue returned invalid text.')
    return value


def _repo(value):
    parts = _text(value).split('/')
    if len(parts) != 2 or any(not _PART.fullmatch(part) or '..' in part for part in parts):
        raise ValueError('Select a dataset from the Hugging Face catalogue.')
    return value


def _number(value):
    return value if type(value) is int and value >= 0 else None


def _json(url):
    reply = net.default().get(url, net.Ask(purpose='dataset catalogue', max_bytes=8 * 1024 * 1024))
    if reply.status >= 400:
        if reply.status in (401, 403):
            raise ValueError('This dataset requires Hugging Face access. Public datasets remain available.')
        raise http.ServerError('The Hugging Face dataset catalogue is unavailable.', status=reply.status)
    try:
        value = json.loads(reply.body, object_pairs_hook=unique_object,
                           parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite dataset metadata')))
    except (ValueError, UnicodeError):
        raise ValueError('The dataset catalogue returned invalid JSON.') from None
    return value, {key.lower(): value for key, value in reply.headers.items()}


def _read(base, params=None):
    return _json(base + ('?' + urlencode(params, doseq=True) if params else ''))[0]


def _cursor(headers, params):
    link = headers.get('link', '')
    if not link:
        return None
    if len(link) > 16384:
        raise ValueError('The dataset page cursor is invalid.')
    following = re.findall(r'<([^<>]+)>\s*;\s*rel="next"', link)
    if not following:
        return None
    if len(following) != 1:
        raise ValueError('The dataset page cursor is ambiguous.')
    location = urlsplit(following[0])
    if (location.scheme != 'https' or location.netloc != 'huggingface.co'
            or location.path != '/api/datasets' or location.fragment):
        raise ValueError('The dataset page cursor changed its source.')
    query = parse_qs(location.query, keep_blank_values=True, strict_parsing=True)
    wanted = {key: value if isinstance(value, list) else [str(value)] for key, value in params.items() if key != 'cursor'}
    if {key: value for key, value in query.items() if key != 'cursor'} != wanted or len(query.get('cursor', [])) != 1:
        raise ValueError('The dataset page cursor changed the selected filters.')
    return _text(query['cursor'][0], 4096)


def _license(row):
    card = row.get('cardData')
    value = card.get('license') if isinstance(card, dict) else None
    if isinstance(value, list):
        value = ', '.join(_text(item, 128) for item in value[:10])
    tags = row.get('tags') or []
    if not isinstance(tags, list):
        raise ValueError('The dataset licence metadata is invalid.')
    if not value:
        value = next((tag[8:] for tag in tags if isinstance(tag, str) and tag.startswith('license:')), '')
    return _text(value or 'Not specified', 1024)


def _summary(row):
    if not isinstance(row, dict):
        raise ValueError('The dataset catalogue returned an invalid result.')
    repo = _repo(row.get('id'))
    return {'id': repo, 'title': repo, 'description': _text(row.get('description') or '', 50000, True),
            'downloads': _number(row.get('downloads')), 'likes': _number(row.get('likes')),
            'license': _license(row), 'gated': bool(row.get('gated'))}


def search(query='', task='', language='', sort='downloads', cursor=''):
    """Return one public dataset search page and its constrained continuation cursor."""
    _text(query, 256)
    if task not in ('', *TASKS) or sort not in SORTS:
        raise ValueError('Choose a listed dataset task and sort order.')
    if not isinstance(language, str) or (language and not re.fullmatch('[a-z][a-z0-9-]{1,15}', language)):
        raise ValueError('Choose a dataset language code.')
    params = {'search': query, 'sort': sort, 'direction': '-1', 'limit': '24', 'full': 'true'}
    filters = ([f'task_categories:{task}'] if task else []) + ([f'language:{language}'] if language else [])
    if filters:
        params['filter'] = filters
    if cursor:
        params['cursor'] = _text(cursor, 4096)
    rows, headers = _json(HUB + '?' + urlencode(params, doseq=True))
    if not isinstance(rows, list) or len(rows) > 24:
        raise ValueError('The dataset catalogue returned an invalid page.')
    return {'datasets': [_summary(row) for row in rows if isinstance(row, dict) and not row.get('private')],
            'cursor': _cursor(headers, params)}


def _info(repo, revision='main'):
    row = _read(HUB + '/' + _repo(repo) + '/revision/' + quote(revision, safe=''), {'blobs': 'true'})
    if not isinstance(row, dict) or row.get('id') != repo or not _COMMIT.fullmatch(str(row.get('sha', ''))):
        raise ValueError('The selected dataset revision is unavailable.')
    return row


def _split(value):
    value = _text(value, 256)
    if not value or value.startswith('.') or any(c in value for c in '/\\%'):
        raise ValueError('Choose a listed dataset subset and split.')
    return value


def _splits(repo):
    rows = _read(VIEWER + '/splits', {'dataset': repo})
    if not isinstance(rows, dict) or not isinstance(rows.get('splits'), list) or len(rows['splits']) > 4096:
        raise ValueError('Dataset subsets and splits are unavailable.')
    result = []
    for row in rows['splits']:
        if not isinstance(row, dict) or row.get('dataset') != repo:
            raise ValueError('A dataset split changed its source.')
        result.append({'config': _split(row.get('config')), 'split': _split(row.get('split')), 'rows': None, 'bytes': None})
    return result


def details(repo):
    """Return current Hub metadata and observational split sizes."""
    repo = _repo(repo)
    main, splits = _info(repo), _splits(repo)
    sizes = _read(VIEWER + '/size', {'dataset': repo})
    sizes = sizes.get('size', {}).get('splits', []) if isinstance(sizes, dict) else []
    if not isinstance(sizes, list) or len(sizes) > 4096:
        raise ValueError('Dataset split sizes are unavailable.')
    for split in splits:
        found = next((row for row in sizes if isinstance(row, dict) and row.get('dataset') == repo
                      and row.get('config') == split['config'] and row.get('split') == split['split']), {})
        split.update(rows=_number(found.get('num_rows')), bytes=_number(found.get('num_bytes_parquet')))
    return {**_summary(main), 'revision': main['sha'], 'splits': splits}


def _value(value, depth=0):
    if depth > 20:
        raise ValueError('The dataset preview contains deeply nested values.')
    if type(value) is float and not math.isfinite(value):
        raise ValueError('The dataset preview contains nonfinite numbers.')
    if value is None or type(value) in (bool, int, float):
        return value
    if isinstance(value, str):
        if len(value) > 500000:
            raise ValueError('A dataset preview value is too large.')
        return value
    if isinstance(value, list) and len(value) <= 10000:
        return [_value(item, depth + 1) for item in value]
    if isinstance(value, dict) and len(value) <= 512:
        return {_text(key): _value(item, depth + 1) for key, item in value.items()}
    raise ValueError('The dataset preview contains unsupported values.')


def preview(repo, config, split):
    """Return up to ten current viewer rows without fetching embedded assets."""
    repo, config, split = _repo(repo), _split(config), _split(split)
    result = _read(VIEWER + '/rows', {'dataset': repo, 'config': config, 'split': split, 'offset': 0, 'length': 10})
    if (not isinstance(result, dict) or not isinstance(result.get('rows'), list) or len(result['rows']) > 10
            or not isinstance(result.get('features'), list) or len(result['features']) > 512):
        raise ValueError('The dataset preview is unavailable.')
    rows = []
    for item in result['rows']:
        if not isinstance(item, dict) or not isinstance(item.get('row'), dict) or item.get('truncated_cells'):
            raise ValueError('The dataset preview contains incomplete rows.')
        rows.append(_value(item['row']))
    columns = []
    for item in result['features']:
        if not isinstance(item, dict) or not {'name', 'type'}.issubset(item):
            raise ValueError('The dataset preview schema is invalid.')
        columns.append({'name': _text(item['name']), 'type': _value(item['type'])})
    return {'columns': columns, 'rows': rows, 'total_rows': _number(result.get('num_rows_total')),
            'live_preview': True, 'partial': result.get('partial') is True}


def _path(repo, url):
    location = urlsplit(_text(url, 4096))
    prefix = '/datasets/' + repo + '/resolve/refs%2Fconvert%2Fparquet/'
    if (location.scheme != 'https' or location.netloc != 'huggingface.co' or location.query or location.fragment
            or not location.path.startswith(prefix)):
        raise ValueError('A converted dataset file has an unexpected source.')
    path = location.path[len(prefix):]
    if not path.endswith('.parquet') or any(not _PART.fullmatch(part) or '..' in part for part in path.split('/')):
        raise ValueError('A converted dataset file has an unsafe filename.')
    return path


def selection(repo, config, split):
    """Return immutable converted file identities for one listed subset and split."""
    repo, config, split = _repo(repo), _split(config), _split(split)
    main, converted = _info(repo), _info(repo, 'refs/convert/parquet')
    listing = _read(VIEWER + '/parquet', {'dataset': repo})
    if not isinstance(listing, dict) or not isinstance(listing.get('parquet_files'), list):
        raise ValueError('Converted dataset files are unavailable.')
    siblings = converted.get('siblings')
    if not isinstance(siblings, list) or len(siblings) > 100000:
        raise ValueError('The immutable dataset file listing is unavailable.')
    names = {}
    for entry in siblings:
        if not isinstance(entry, dict) or not isinstance(entry.get('rfilename'), str) or entry['rfilename'] in names:
            raise ValueError('The immutable dataset file listing is ambiguous.')
        names[entry['rfilename']] = entry
    files, seen = [], set()
    for row in listing['parquet_files']:
        if not isinstance(row, dict) or row.get('dataset') != repo:
            raise ValueError('A converted dataset file changed its source.')
        if row.get('config') != config or row.get('split') != split:
            continue
        path = _path(repo, row.get('url'))
        entry = names.get(path, {})
        lfs = entry.get('lfs')
        if (path in seen or not isinstance(lfs, dict) or not _SHA.fullmatch(str(lfs.get('sha256', '')))
                or type(lfs.get('size')) is not int or lfs['size'] <= 0 or row.get('size') != lfs['size']):
            raise ValueError('A converted dataset file lacks a matching immutable digest and size.')
        seen.add(path)
        files.append({'path': path, 'sha256': lfs['sha256'], 'size': lfs['size']})
    if not files or listing.get('partial'):
        raise ValueError('This subset and split do not have a complete data-only Parquet export.')
    return {'version': 1, 'repo': repo, 'revision': main['sha'], 'converted_revision': converted['sha'],
            'config': config, 'split': split, 'files': files}
