"""Local dataset samples use held confined files and strict data-only decoders."""
import json
import os
from pathlib import Path

import pytest

from poolhouse.fleet import dataset_preview


def write(tmp_path, name, value):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding='utf-8')
    return name


def test_jsonl_sample_keeps_nested_fields_without_fetching_assets(tmp_path):
    path = write(tmp_path, 'datasets/records.jsonl', json.dumps({'text': '<img src=x>', 'nested': {'url': 'http://127.0.0.1/private'},
                                                             **{f'field-{n}': n for n in range(12)}}) + '\n')
    result = dataset_preview.preview(path, tmp_path)
    assert result['rows'][0]['nested']['url'] == 'http://127.0.0.1/private'
    assert len(result['columns']) == 14 and result['sampled'] and not result['truncated']
    assert result['path'] == path and result['text'] == ''


def test_jsonl_truncated_tail_preserves_complete_sample_and_utf8_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_preview, 'MAX_BYTES', 30)
    path = write(tmp_path, 'records.jsonl', '{"text":"complete"}\n{"text":"' + 'é' * 20 + '"}\n')
    result = dataset_preview.preview(path, tmp_path)
    assert result['rows'] == [{'text': 'complete'}] and result['truncated']


def test_csv_uses_literal_quoted_multiline_records(tmp_path):
    path = write(tmp_path, 'records.csv', 'question,response\n"Which, item?","A quoted\nanswer"\n')
    assert dataset_preview.preview(path, tmp_path)['rows'] == [{'question': 'Which, item?', 'response': 'A quoted\nanswer'}]


def test_csv_truncated_incomplete_record_does_not_discard_complete_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_preview, 'MAX_BYTES', 30)
    path = write(tmp_path, 'records.csv', 'text,value\nfirst,one\n"' + 'x' * 100 + '",two\n')
    result = dataset_preview.preview(path, tmp_path)
    assert result['rows'] == [{'text': 'first', 'value': 'one'}] and result['truncated']


def test_truncated_json_array_is_text_and_never_a_partial_dataset(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_preview, 'MAX_BYTES', 20)
    result = dataset_preview.preview(write(tmp_path, 'records.json', '[{"text":"' + 'x' * 30 + '"}]'), tmp_path)
    assert result['rows'] == [] and result['truncated'] and result['text']


@pytest.mark.parametrize('name,text', [('bad.jsonl', '{"text":1,"text":2}\n'), ('bad.jsonl', '{"text":1e999}\n'),
    ('bad.json', '[1,2]'), ('bad.csv', 'text,text\none,two\n'), ('bad.csv', ',value\none,two\n'),
    ('bad.csv', 'text,value\none\n'), ('bad.csv', 'text,value\none,two,three\n'),
    ('bad.csv', 'text,value\n"unterminated,two')])
def test_malformed_complete_sample_is_not_usable_rows(tmp_path, name, text):
    with pytest.raises(ValueError):
        dataset_preview.preview(write(tmp_path, name, text), tmp_path)


@pytest.mark.parametrize('path', ['../outside.jsonl', '/outside.jsonl', 'jobs/private.jsonl', 'studio/private.jsonl',
                                 'studio-device-resources/private.jsonl', 'records%2Ejsonl', './records.jsonl'])
def test_preview_cannot_select_internal_or_aliased_paths(tmp_path, path):
    write(tmp_path, 'records.jsonl', '{}\n')
    with pytest.raises((ValueError, RuntimeError)):
        dataset_preview.preview(path, tmp_path)


def test_symlinked_directory_and_file_and_hardlinks_are_not_followed(tmp_path):
    write(tmp_path, 'real/records.jsonl', '{}\n')
    (tmp_path / 'alias').symlink_to(tmp_path / 'real', target_is_directory=True)
    (tmp_path / 'link.jsonl').symlink_to(tmp_path / 'real/records.jsonl')
    os.link(tmp_path / 'real/records.jsonl', tmp_path / 'hard.jsonl')
    for name in ['alias/records.jsonl', 'link.jsonl', 'hard.jsonl']:
        with pytest.raises(ValueError):
            dataset_preview.preview(name, tmp_path)


def test_file_replacement_during_held_read_is_refused(tmp_path, monkeypatch):
    write(tmp_path, 'records.jsonl', '{"text":"before"}\n')
    original = dataset_preview._open

    class Replaced:
        def __enter__(self):
            self.source = original(tmp_path, Path('records.jsonl'))
            return self

        def __exit__(self, *args):
            self.source.close()

        def fileno(self):
            return self.source.fileno()

        def read(self, size):
            raw = self.source.read(size)
            (tmp_path / 'records.jsonl').unlink()
            write(tmp_path, 'records.jsonl', '{"text":"replacement"}\n')
            return raw

    monkeypatch.setattr(dataset_preview, '_open', lambda *args: Replaced())
    with pytest.raises(ValueError, match='changed'):
        dataset_preview.preview('records.jsonl', tmp_path)


def test_large_csv_partial_unquoted_tail_is_not_an_importable_row(tmp_path):
    prefix = 'text,value\nfirst,complete\npartial,'
    path = write(tmp_path, 'records.csv', prefix + 'x' * 1_000_000 + '\n')
    result = dataset_preview.preview(path, tmp_path)
    assert result['truncated'] and result['rows'] == [{'text': 'first', 'value': 'complete'}]


def test_truncated_csv_header_never_becomes_a_complete_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_preview, 'MAX_BYTES', 8)
    path = write(tmp_path, 'records.csv', 'long-header,other\nfirst,second\n')
    result = dataset_preview.preview(path, tmp_path)
    assert result['truncated'] and result['columns'] == [] and result['rows'] == []


def test_parent_replaced_by_link_to_same_inode_is_refused(tmp_path, monkeypatch):
    write(tmp_path, 'data/records.jsonl', '{"text":"original"}\n')
    original = dataset_preview._open
    changed = False

    class ParentReplaced:
        def __enter__(self):
            self.source = original(tmp_path, Path('data/records.jsonl'))
            return self

        def __exit__(self, *args):
            self.source.close()

        def fileno(self):
            return self.source.fileno()

        def read(self, size):
            nonlocal changed
            raw = self.source.read(size)
            if not changed:
                (tmp_path / 'data').rename(tmp_path / 'original')
                (tmp_path / 'data').symlink_to(tmp_path / 'original', target_is_directory=True)
                changed = True
            return raw

    monkeypatch.setattr(dataset_preview, '_open', lambda *args: ParentReplaced())
    with pytest.raises(ValueError):
        dataset_preview.preview('data/records.jsonl', tmp_path)
