"""Dataset discovery consumes fixed network fixtures and inert metadata."""
import copy
import json
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from poolhouse import net
from poolhouse.fleet import dataset_catalogue as catalogue
from poolhouse.httpguard import Fetched

REPO = 'sample-lab/records'
PATH = 'default/train/0000.parquet'
URL = f'https://huggingface.co/datasets/{REPO}/resolve/refs%2Fconvert%2Fparquet/{PATH}'


@pytest.fixture
def remote(monkeypatch):
    calls = []
    info = {'id': REPO, 'sha': 'a' * 40, 'description': 'Literal <img src=x>\nMetadata.',
            'tags': ['license:mit'], 'siblings': [{'rfilename': PATH, 'lfs': {'sha256': 'c' * 64, 'size': 17}}]}
    parquet = {'parquet_files': [{'dataset': REPO, 'config': 'default', 'split': 'train', 'url': URL, 'size': 17}], 'partial': False}
    values = {'main': info, 'converted': {**copy.deepcopy(info), 'sha': 'b' * 40}, 'parquet': parquet,
        'splits': {'splits': [{'dataset': REPO, 'config': 'default', 'split': 'train'}]},
        'size': {'size': {'splits': [{'dataset': REPO, 'config': 'default', 'split': 'train', 'num_rows': 3, 'num_bytes_parquet': 17}]}},
        'rows': {'features': [{'name': 'text', 'type': {'dtype': 'string'}}],
                 'rows': [{'row_idx': 0, 'row': {'text': '<script>bad()</script>', 'media': {'src': 'http://127.0.0.1/private'}}, 'truncated_cells': []}],
                 'num_rows_total': 3, 'partial': False}, 'search': [info], 'headers': {}}

    class Pipeline:
        def get(self, url, ask):
            location = urlsplit(url)
            assert location.scheme == 'https' and location.netloc in {'huggingface.co', 'datasets-server.huggingface.co'}
            assert ask.token == '' and ask.admit is True and ask.max_bytes == 8 * 1024 * 1024
            calls.append(url)
            endpoint = location.path.rsplit('/', 1)[-1]
            key = ('converted' if endpoint == 'refs%2Fconvert%2Fparquet' else 'main') if '/revision/' in location.path else endpoint
            key = 'search' if location.path == '/api/datasets' else key
            body = values['raw'] if 'raw' in values and values.get('raw_key', key) == key else json.dumps(values[key]).encode()
            return Fetched(url, values.get('status', 200), values['headers'] if key == 'search' else {}, body)

    monkeypatch.setattr(net, 'default', Pipeline)
    return values, calls


def test_search_filters_cursor_and_metadata_are_data(remote):
    values, calls = remote
    params = {'search': 'sample', 'sort': 'downloads', 'direction': '-1', 'limit': '24', 'full': 'true',
              'filter': ['task_categories:text-generation', 'language:en'], 'cursor': 'opaque-data'}
    values['headers']['Link'] = '<' + catalogue.HUB + '?' + urlencode(params, doseq=True) + '>; rel="next"'
    result = catalogue.search('sample', 'text-generation', 'en')
    assert result['datasets'][0]['license'] == 'mit' and result['cursor'] == 'opaque-data'
    assert result['datasets'][0]['description'] == values['main']['description']
    catalogue.search('sample', 'text-generation', 'en', cursor=result['cursor'])
    assert len(calls) == 2 and parse_qs(urlsplit(calls[1]).query)['cursor'] == ['opaque-data']


@pytest.mark.parametrize('changed', ['https://other.invalid/api/datasets', 'http://huggingface.co/api/datasets',
                                     'https://huggingface.co:443/api/datasets', 'https://huggingface.co/api/models'])
def test_pagination_never_adopts_a_returned_url(remote, changed):
    values, calls = remote
    values['headers']['link'] = f'<{changed}?cursor=next>; rel="next"'
    with pytest.raises(ValueError):
        catalogue.search()
    assert len(calls) == 1


def test_pagination_cannot_change_query_or_duplicate_cursor(remote):
    values, _ = remote
    values['headers']['link'] = '<' + catalogue.HUB + '?search=other&cursor=one&cursor=two>; rel="next"'
    with pytest.raises(ValueError):
        catalogue.search('sample')


def test_details_sizes_and_preview_never_fetch_media(remote):
    _, calls = remote
    result = catalogue.details(REPO)
    assert result['revision'] == 'a' * 40
    assert result['splits'] == [{'config': 'default', 'split': 'train', 'rows': 3, 'bytes': 17}]
    sample = catalogue.preview(REPO, 'default', 'train')
    assert sample['live_preview'] is True and sample['total_rows'] == 3
    assert sample['rows'][0]['media']['src'] == 'http://127.0.0.1/private'
    assert len(calls) == 4 and all('/private' not in url for url in calls)


def test_selection_pins_both_independent_revisions_and_digest(remote):
    _, calls = remote
    selected = catalogue.selection(REPO, 'default', 'train')
    assert selected == {'version': 1, 'repo': REPO, 'revision': 'a' * 40, 'converted_revision': 'b' * 40,
                        'config': 'default', 'split': 'train', 'files': [{'path': PATH, 'sha256': 'c' * 64, 'size': 17}]}
    assert len(calls) == 3 and all('/resolve/' not in url for url in calls)


@pytest.mark.parametrize('url', ['http://huggingface.co/datasets/sample-lab/records/resolve/refs%2Fconvert%2Fparquet/x.parquet',
    'https://127.0.0.1/private.parquet', URL.replace('huggingface.co/', 'huggingface.co.evil.invalid/'),
    URL.replace('/default/train/', '/../'), URL + '?download=1', URL.replace('0000.parquet', '%2Fsecret.parquet'),
    URL.replace('sample-lab/records', 'other-lab/records'), URL.replace('.parquet', '.py')])
def test_selection_rejects_remote_url_and_path_substitution(remote, url):
    values, calls = remote
    values['parquet']['parquet_files'][0]['url'] = url
    with pytest.raises(ValueError):
        catalogue.selection(REPO, 'default', 'train')
    assert len(calls) == 3


@pytest.mark.parametrize('change', ['sha', 'size', 'duplicate', 'partial', 'missing', 'revision'])
def test_selection_never_falls_back_to_unpinned_or_partial_data(remote, change):
    values, _ = remote
    if change == 'sha':
        values['converted']['siblings'][0]['lfs']['sha256'] = 'short'
    elif change == 'size':
        values['parquet']['parquet_files'][0]['size'] = 18
    elif change == 'duplicate':
        values['converted']['siblings'] *= 2
    elif change == 'partial':
        values['parquet']['partial'] = True
    elif change == 'missing':
        values['converted']['siblings'] = []
    else:
        values['converted']['sha'] = 'main'
    with pytest.raises(ValueError):
        catalogue.selection(REPO, 'default', 'train')


@pytest.mark.parametrize('raw', [b'{"id":1,"id":2}', b'NaN', b'{', b'[{"id":"sample-lab/records","description":1}]'])
def test_ambiguous_or_invalid_catalogue_answers_fail_closed(remote, raw):
    values, _ = remote
    values['raw'] = raw
    with pytest.raises(ValueError):
        catalogue.search()


@pytest.mark.parametrize('change', ['truncated', 'infinity', 'schema', 'depth'])
def test_incomplete_or_unbounded_preview_is_not_presented_as_rows(remote, change):
    values, _ = remote
    if change == 'truncated':
        values['rows']['rows'][0]['truncated_cells'] = ['text']
    elif change == 'infinity':
        values['rows']['rows'][0]['row']['number'] = float('inf')
    elif change == 'schema':
        values['rows']['features'] = [{'unknown': 'text'}]
    else:
        value = []
        for _ in range(22):
            value = [value]
        values['rows']['rows'][0]['row']['nested'] = value
    with pytest.raises(ValueError):
        catalogue.preview(REPO, 'default', 'train')


@pytest.mark.parametrize('value', ['https://huggingface.co/datasets/sample-lab/records', '../records', 'sample-lab/../records', 'sample-lab/rec%2Fords'])
def test_repo_selection_is_not_a_url_or_filesystem_path(remote, value):
    _, calls = remote
    with pytest.raises(ValueError):
        catalogue.details(value)
    assert calls == []


@pytest.mark.parametrize('function', ['search', 'info', 'parquet'])
@pytest.mark.parametrize('malformed', ['deep', 'overflow'])
def test_all_remote_metadata_has_bounded_finite_structure(remote, function, malformed):
    values, _ = remote
    key = {'search': 'search', 'info': 'main', 'parquet': 'parquet'}[function]
    envelope = copy.deepcopy(values[key])
    target = envelope[0] if isinstance(envelope, list) else envelope
    target['unused'] = 'REPLACE_VALUE'
    encoded = json.dumps(envelope).encode()
    injected = b'[' * 1100 + b'0' + b']' * 1100 if malformed == 'deep' else b'1e999'
    values['raw'], values['raw_key'] = encoded.replace(b'"REPLACE_VALUE"', injected), key
    operation = {'search': catalogue.search, 'info': lambda: catalogue.details(REPO),
                 'parquet': lambda: catalogue.selection(REPO, 'default', 'train')}[function]
    with pytest.raises(ValueError):
        operation()


def test_search_does_not_silently_drop_malformed_rows(remote):
    values, _ = remote
    values['search'].append(None)
    with pytest.raises(ValueError):
        catalogue.search()


def test_selection_size_requires_an_integer_not_boolean_equality(remote):
    values, _ = remote
    values['parquet']['parquet_files'][0]['size'] = True
    values['converted']['siblings'][0]['lfs']['size'] = 1
    with pytest.raises(ValueError):
        catalogue.selection(REPO, 'default', 'train')
