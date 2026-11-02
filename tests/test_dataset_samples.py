"""Observed preview selections retain exact content without claiming full-split import."""
import copy
import json

import pytest

from poolhouse.studio import dataset_samples as samples


def selection():
    preview = {'columns': [{'name': 'text', 'type': 'string'}, {'name': 'label', 'type': 'string'}],
               'rows': [{'text': 'first', 'label': 'yes'}, {'text': 'second', 'label': 'no'}]}
    body = {'repo': 'example/data', 'config': 'default', 'split': 'train', 'name': 'Selected rows',
            'idempotency_key': 'choose-once', 'row_indexes': [1, 0], 'columns': {'text': 'prompt'},
            'expected_preview_digest': samples.preview_digest('example/data', 'default', 'train', preview)}
    return body, preview


def test_exact_selected_order_columns_and_observed_provenance():
    body, preview = selection()
    before = copy.deepcopy((body, preview))
    raw, manifest = samples.selected(body, preview)
    assert [json.loads(line) for line in raw.splitlines()] == [{'prompt': 'second'}, {'prompt': 'first'}]
    assert manifest['rows'] == 2 and manifest['columns'] == [{'name': 'prompt', 'type': 'string'}]
    assert manifest['source'] == {'kind': 'huggingface-preview', 'repo': 'example/data', 'config': 'default',
        'split': 'train', 'preview_digest': body['expected_preview_digest'],
        'row_indexes': [1, 0], 'columns': {'text': 'prompt'}}
    assert 'revision' not in manifest['source'] and 'converted_revision' not in manifest['source']
    assert (body, preview) == before
    assert samples.selected(body, preview) == (raw, manifest)


@pytest.mark.parametrize(('key', 'value'), [('row_indexes', [True]), ('row_indexes', [0, 0]),
    ('row_indexes', []), ('row_indexes', [-1]), ('row_indexes', [2]), ('columns', {}),
    ('columns', {'text': 'same', 'label': 'same'}), ('columns', {'missing': 'prompt'}),
    ('columns', {'text': True}), ('columns', {'text': 'bad\nname'}), ('name', ''),
    ('expected_preview_digest', 'x' * 64)])
def test_malformed_or_unlisted_selection_refused(key, value):
    body, preview = selection()
    body[key] = value
    with pytest.raises(ValueError):
        samples.selected(body, preview)


def test_unknown_fields_and_changed_observation_refused():
    body, preview = selection()
    body['url'] = 'https://private.invalid/'
    with pytest.raises(ValueError):
        samples.selected(body, preview)
    del body['url']
    preview['rows'][0]['text'] = 'changed'
    with pytest.raises(ValueError, match='preview changed'):
        samples.selected(body, preview)


def test_missing_actual_row_field_refused():
    body, preview = selection()
    del preview['rows'][0]['text']
    body['expected_preview_digest'] = samples.preview_digest('example/data', 'default', 'train', preview)
    with pytest.raises(ValueError, match='missing'):
        samples.selected(body, preview)


def test_selected_serialized_bytes_use_maintained_record_budget(monkeypatch):
    body, preview = selection()
    monkeypatch.setattr(samples, 'MAX_BODY', 1)
    with pytest.raises(ValueError, match='record budget'):
        samples.selected(body, preview)
