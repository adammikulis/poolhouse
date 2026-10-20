"""Original sources yield complete, authorized records with durable generation identities."""
import copy
import os
import time

import pytest

from poolhouse.studio.stream_sources import SourceChanged, WatchedSource, bind


def allowed():
    return True


def read(watcher, cursor=None, *, maximum=4 * 1024 * 1024, authorize=allowed):
    return watcher.poll(cursor, authorize=authorize, max_record_bytes=maximum, deadline=time.monotonic() + 1)


def file_source(tmp_path, content, suffix='jsonl'):
    path = tmp_path / ('events.' + suffix)
    path.write_bytes(content)
    binding = bind(path, 'file', authorize=allowed)
    return path, binding, WatchedSource(binding)


def test_jsonl_complete_records_replay_identically_until_cursor_commits(tmp_path):
    path, binding, watcher = file_source(tmp_path, b'{"text":"first","id":"not-an-authority"}\n{"text":"second"}\n')
    first = read(watcher)
    assert first['event']['payload'] == {'text': 'first', 'id': 'not-an-authority'}
    assert first['event']['id'] != 'not-an-authority'
    assert read(watcher)['event'] == first['event']
    resumed = WatchedSource(binding)
    second = read(resumed, first['cursor'])
    assert second['event']['payload'] == {'text': 'second'}
    assert second['event']['id'] != first['event']['id']
    assert read(resumed, second['cursor'])['event'] is None
    watcher.close()
    resumed.close()
    assert path.read_bytes().startswith(b'{"text":"first"')


def test_partial_line_is_withheld_until_append_completes_it(tmp_path):
    path, _, watcher = file_source(tmp_path, b'{"text":"par')
    pending = read(watcher)
    assert pending['event'] is None
    assert pending['cursor']['files'][path.name]['offset'] == 0
    with path.open('ab') as output:
        output.write(b'tial"}\n')
    ready = read(watcher, pending['cursor'])
    assert ready['event']['payload'] == {'text': 'partial'}
    watcher.close()


@pytest.mark.parametrize('content', [b'not json\n', b'{"x":1,"x":2}\n', b'{"value":NaN}\n', b'{"value":1e309}\n', b'[]\n', b'\xff\n'])
def test_invalid_complete_records_are_quarantined_and_advanced(tmp_path, content):
    path, _, watcher = file_source(tmp_path, content + b'{"valid":true}\n')
    invalid = read(watcher)
    assert 'error' in invalid['event'] and 'payload' not in invalid['event']
    assert invalid['cursor']['files'][path.name]['offset'] == len(content)
    assert read(watcher, invalid['cursor'])['event']['payload'] == {'valid': True}
    watcher.close()


def test_csv_multiline_and_escaped_quotes_complete_as_one_record(tmp_path):
    path, _, watcher = file_source(tmp_path, b'text,label\r\n"first\nsecond ""quote""",yes\r\n', 'csv')
    header = read(watcher)
    assert header['event'] is None
    row = read(watcher, header['cursor'])
    assert row['event']['payload'] == {'text': 'first\nsecond "quote"', 'label': 'yes'}
    assert row['cursor']['files'][path.name]['offset'] == path.stat().st_size
    watcher.close()


def test_csv_partial_quote_is_not_dispatched_and_invalid_rows_are_quarantined(tmp_path):
    path, _, watcher = file_source(tmp_path, b'text,label\n"unfinished\n', 'csv')
    header = read(watcher)
    partial = read(watcher, header['cursor'])
    assert partial['event'] is None
    with path.open('ab') as output:
        output.write(b'finished",yes\nwrong,count,extra\n')
    complete = read(watcher, partial['cursor'])
    assert complete['event']['payload']['text'] == 'unfinished\nfinished'
    invalid = read(watcher, complete['cursor'])
    assert 'error' in invalid['event'] and 'payload' not in invalid['event']
    watcher.close()


def test_csv_record_budget_does_not_inherit_a_smaller_default_field_cap(tmp_path):
    cell = 'x' * 150_000
    _, _, watcher = file_source(tmp_path, ('text\n' + cell + '\n').encode(), 'csv')
    header = read(watcher)
    assert read(watcher, header['cursor'])['event']['payload'] == {'text': cell}
    watcher.close()


@pytest.mark.parametrize('suffix,content', [('jsonl', b'{"text":"' + b'x' * 100_000 + b'"}\n'),
                                             ('csv', b'"' + b'x' * 100_000 + b'"\n')])
def test_oversized_records_are_streamed_to_quarantine_not_truncated(tmp_path, suffix, content):
    path, _, watcher = file_source(tmp_path, content, suffix)
    result = read(watcher, maximum=100)
    assert 'error' in result['event'] and 'payload' not in result['event']
    assert result['cursor']['files'][path.name]['offset'] == len(content)
    assert not watcher.readers
    watcher.close()


@pytest.mark.parametrize('change', ['truncate', 'prefix'])
def test_same_inode_consumed_prefix_changes_require_deliberate_reset(tmp_path, change):
    path, _, watcher = file_source(tmp_path, b'{"n":1}\n{"n":2}\n')
    first = read(watcher)
    if change == 'truncate':
        path.write_bytes(b'')
    else:
        path.write_bytes(b'{"n":9}\n{"n":2}\n')
    with pytest.raises(SourceChanged):
        read(watcher, first['cursor'])
    watcher.close()


def test_atomic_replacement_creates_new_generation_and_reports_old_partial(tmp_path):
    path, binding, watcher = file_source(tmp_path, b'{"old":true}\n{"partial":')
    first = read(watcher)
    pending = read(watcher, first['cursor'])
    replacement = tmp_path / 'replacement'
    replacement.write_bytes(b'{"new":true}\n')
    replacement.replace(path)
    rotation = read(watcher, pending['cursor'])
    assert 'pending record' in rotation['event']['error']
    new = read(watcher, rotation['cursor'])
    assert new['event']['payload'] == {'new': True}
    assert new['event']['generation'] != first['event']['generation']
    watcher.close()
    restarted = WatchedSource(binding)
    assert read(restarted, new['cursor'])['event'] is None
    restarted.close()


def test_folder_scanner_is_nonrecursive_and_eventually_visits_all_files(tmp_path):
    root = tmp_path / 'folder'
    root.mkdir()
    for name in ['a.jsonl', 'b.jsonl', 'c.jsonl']:
        (root / name).write_text('{"file":"' + name + '"}\n')
    (root / 'nested').mkdir()
    (root / 'nested' / 'private.jsonl').write_text('{"must_not_read":true}\n')
    (root / 'unselected.txt').write_text('not an event')
    watcher = WatchedSource(bind(root, 'folder', authorize=allowed))
    cursor, events = None, []
    while len(events) < 3:
        result = read(watcher, cursor)
        cursor = result['cursor']
        if result['event']:
            events.append(result['event']['payload']['file'])
    assert set(events) == {'a.jsonl', 'b.jsonl', 'c.jsonl'}
    assert len(cursor['files']) == 3
    watcher.close()


@pytest.mark.parametrize('kind', ['file-link', 'parent-link', 'hardlink', 'folder-link'])
def test_descriptor_bindings_reject_links_and_nonowned_files(tmp_path, kind):
    root = tmp_path / 'root'
    root.mkdir()
    source = root / 'data.jsonl'
    source.write_text('{}\n')
    if kind == 'file-link':
        chosen = root / 'linked.jsonl'
        chosen.symlink_to(source)
    elif kind == 'parent-link':
        chosen = tmp_path / 'alias'
        chosen.symlink_to(root, target_is_directory=True)
        chosen = chosen / 'data.jsonl'
    elif kind == 'hardlink':
        chosen = root / 'linked.jsonl'
        os.link(source, chosen)
    else:
        chosen = tmp_path / 'alias'
        chosen.symlink_to(root, target_is_directory=True)
    with pytest.raises((OSError, SourceChanged)):
        bind(chosen, 'folder' if kind == 'folder-link' else 'file', authorize=allowed)


def test_parent_replacement_and_folder_child_links_are_refused(tmp_path):
    root = tmp_path / 'folder'
    root.mkdir()
    (root / 'a.jsonl').write_text('{}\n')
    watcher = WatchedSource(bind(root, 'folder', authorize=allowed))
    root.rename(tmp_path / 'previous')
    root.mkdir()
    with pytest.raises(SourceChanged):
        read(watcher)
    watcher.close()
    (root / 'linked.jsonl').symlink_to(tmp_path / 'previous' / 'a.jsonl')
    watcher = WatchedSource(bind(root, 'folder', authorize=allowed))
    with pytest.raises((OSError, SourceChanged)):
        read(watcher)
    watcher.close()


def test_revocation_before_bind_and_after_record_read_never_returns_a_cursor(tmp_path, monkeypatch):
    path = tmp_path / 'events.jsonl'
    path.write_text('{}\n')
    with pytest.raises(PermissionError):
        bind(path, 'file', authorize=lambda: False)
    _, binding, watcher = file_source(tmp_path, b'{"data":1}\n')
    from poolhouse.studio import stream_sources
    live = {'value': True}
    original = stream_sources._Record.read
    def revoke_after_read(reader, *args):
        ready = original(reader, *args)
        if ready:
            live['value'] = False
        return ready
    monkeypatch.setattr(stream_sources._Record, 'read', revoke_after_read)
    with pytest.raises(PermissionError):
        read(watcher, authorize=lambda: live['value'])
    monkeypatch.setattr(stream_sources._Record, 'read', original)
    watcher.close()
    restarted = WatchedSource(binding)
    assert read(restarted)['event']['payload'] == {'data': 1}
    restarted.close()


@pytest.mark.parametrize('change', ['binding', 'offset', 'prefix', 'path', 'version'])
def test_forged_or_foreign_cursors_are_refused(tmp_path, change):
    _, _, watcher = file_source(tmp_path, b'{"n":1}\n{"n":2}\n')
    cursor = copy.deepcopy(read(watcher)['cursor'])
    row = cursor['files']['events.jsonl']
    if change == 'binding':
        cursor['binding'] = 'other'
    elif change == 'offset':
        row['offset'] = True
    elif change == 'prefix':
        row['prefix'] = '0' * 64
    elif change == 'path':
        cursor['files']['../private'] = cursor['files'].pop('events.jsonl')
    else:
        cursor['version'] = True
    with pytest.raises(SourceChanged):
        read(watcher, cursor)
    watcher.close()


def test_deadline_yields_without_acknowledging_or_losing_pending_records(tmp_path):
    _, _, watcher = file_source(tmp_path, b'{"n":1}\n')
    idle = watcher.poll(authorize=allowed, max_record_bytes=100, deadline=time.monotonic() - 1)
    assert idle['event'] is None and idle['cursor']['files']['events.jsonl']['offset'] == 0
    assert read(watcher, idle['cursor'])['event']['payload'] == {'n': 1}
    watcher.close()


def test_mutation_during_record_read_never_returns_inconsistent_event(tmp_path, monkeypatch):
    from poolhouse.studio import stream_sources
    path, _, watcher = file_source(tmp_path, b'{"n":1}\n')
    original = stream_sources._Record.read
    def mutate(reader, *args):
        ready = original(reader, *args)
        path.write_bytes(b'{"n":9}\n')
        return ready
    monkeypatch.setattr(stream_sources._Record, 'read', mutate)
    result = read(watcher)
    assert result['event'] is None and result['cursor']['files'][path.name]['offset'] == 0
    monkeypatch.setattr(stream_sources._Record, 'read', original)
    assert read(watcher, result['cursor'])['event']['payload'] == {'n': 9}
    watcher.close()


def test_same_size_mutation_with_restored_mtime_restarts_retained_reader(tmp_path):
    import hashlib
    old, new = b'{"text":"old', b'{"text":"new'
    path, _, watcher = file_source(tmp_path, old)
    partial = read(watcher)
    original = path.stat()
    path.write_bytes(new)
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    changed = read(watcher, partial['cursor'])
    assert changed['event'] is None
    assert changed['cursor']['files'][path.name]['pending']['sha256'] == hashlib.sha256(new).hexdigest()
    watcher.close()


def test_consumed_prefix_changed_during_read_never_acknowledges_next_record(tmp_path, monkeypatch):
    from poolhouse.studio import stream_sources
    path, _, watcher = file_source(tmp_path, b'{"n":1}\n{"n":2}\n')
    first = read(watcher)
    original = stream_sources._Record.read
    def mutate(reader, *args):
        ready = original(reader, *args)
        path.write_bytes(b'{"n":9}\n{"n":2}\n')
        return ready
    monkeypatch.setattr(stream_sources._Record, 'read', mutate)
    result = read(watcher, first['cursor'])
    assert result['event'] is None
    assert result['cursor']['files'][path.name]['offset'] == first['cursor']['files'][path.name]['offset']
    monkeypatch.setattr(stream_sources._Record, 'read', original)
    with pytest.raises(SourceChanged):
        read(watcher, result['cursor'])
    watcher.close()


def test_prefix_verification_resumes_across_scheduler_quantums(tmp_path, monkeypatch):
    import itertools

    from poolhouse.studio import stream_sources
    _, binding, watcher = file_source(tmp_path, b'{"large":"' + b'x' * 100_000 + b'"}\n{"next":true}\n')
    first = read(watcher)
    watcher.close()
    watcher = WatchedSource(binding)
    ticks = itertools.count()
    monkeypatch.setattr(stream_sources.time, 'monotonic', lambda: next(ticks))
    pending = watcher.poll(first['cursor'], authorize=allowed, max_record_bytes=200_000, deadline=2)
    assert pending['event'] is None
    reader = next(iter(watcher.readers.values()))
    assert 0 < reader.verified < first['cursor']['files']['events.jsonl']['offset']
    ready = watcher.poll(pending['cursor'], authorize=allowed, max_record_bytes=200_000, deadline=100)
    assert ready['event']['payload'] == {'next': True}
    watcher.close()


def test_oversize_discard_resumes_with_bounded_memory_until_boundary(tmp_path, monkeypatch):
    import itertools

    from poolhouse.studio import stream_sources
    _, _, watcher = file_source(tmp_path, b'{"large":"' + b'x' * 100_000 + b'"}\n')
    ticks = itertools.count()
    monkeypatch.setattr(stream_sources.time, 'monotonic', lambda: next(ticks))
    partial = watcher.poll(authorize=allowed, max_record_bytes=100, deadline=2)
    assert partial['event'] is None
    reader = next(iter(watcher.readers.values()))
    assert reader.oversize and len(reader.buffer) <= 100
    ready = watcher.poll(partial['cursor'], authorize=allowed, max_record_bytes=100, deadline=100)
    assert 'error' in ready['event'] and not watcher.readers
    watcher.close()
