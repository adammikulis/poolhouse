"""Concurrent read cursors and output-safe inbox acknowledgments."""

from __future__ import annotations

import argparse
import io
import stat
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from poolhouse.board import credentials
from poolhouse.board.session import Entry, Page
from poolhouse.workspace import board_cli, inbound, mail_cli


def test_concurrent_cursor_updates_keep_every_origin_and_question(tmp_path, monkeypatch):
    original = credentials._read_cursors

    def snapshot(*args):
        value = original(*args)
        time.sleep(0.01)
        return value

    monkeypatch.setattr(credentials, '_read_cursors', snapshot)
    updates = [('inbox', {'first': 9}), ('inbox', {'second': 4}), ('digest', {'third': 7})]
    with ThreadPoolExecutor(max_workers=3) as workers:
        list(workers.map(lambda item: credentials.remember(tmp_path, 'board', 'reader', *item), updates))
    credentials.remember(tmp_path, 'board', 'reader', 'inbox', {'first': 1})
    assert credentials.cursors(tmp_path, 'board', 'reader') == {
        'inbox': {'first': 9, 'second': 4}, 'digest': {'third': 7}}
    folder = tmp_path / 'client' / 'board'
    assert stat.S_IMODE((folder / 'reader.cursors.json').stat().st_mode) == 0o600
    assert stat.S_IMODE((folder / 'reader.cursors.lock').stat().st_mode) == 0o600


@pytest.mark.parametrize('cursor', [{'origin': -1}, {'origin': True}, {'origin': '1'}])
def test_invalid_cursor_never_replaces_saved_progress(tmp_path, cursor):
    credentials.remember(tmp_path, 'board', 'reader', 'inbox', {'origin': 4})
    with pytest.raises(ValueError, match='nonnegative integers'):
        credentials.remember(tmp_path, 'board', 'reader', 'inbox', cursor)
    assert credentials.cursors(tmp_path, 'board', 'reader')['inbox'] == {'origin': 4}


def test_cursor_lock_refuses_symlink_and_preserves_external_file(tmp_path):
    folder = credentials.prepare(tmp_path) / 'board'
    folder.mkdir(mode=0o700)
    external = tmp_path / 'external'
    external.write_text('keep')
    (folder / 'reader.cursors.lock').symlink_to(external)
    with pytest.raises(ValueError, match='symlink'):
        credentials.remember(tmp_path, 'board', 'reader', 'inbox', {'origin': 4})
    assert external.read_text() == 'keep'


class Reader:
    def __init__(self):
        self.progress = {}
        self.entries = [Entry(f'board:device:{number}', str(number), 'message', 'sender', False,
                              '#dm', {'to': 'reader', 'type': 'note', 'body': 'hello'}, 1000)
                        for number in (2, 3)]

    def cursor(self, _question):
        return dict(self.progress)

    def keep(self, _question, value):
        self.progress = value

    def read(self, *_args, **_kwargs):
        return Page(self.entries, {'device': 3})


def arguments():
    return argparse.Namespace(json=True, ack=True, children=False, all=False, limit=0, raw=False,
                              for_agent='', agent='', timeout=0)


@pytest.mark.parametrize('handler', [board_cli.inbox, mail_cli.wait])
@pytest.mark.parametrize('failure', ['write', 'flush', None])
def test_read_ack_follows_successful_output_and_flush(monkeypatch, handler, failure):
    reader = Reader()
    monkeypatch.setattr(board_cli, 'connect', lambda _args: reader)
    monkeypatch.setattr(inbound, 'names', lambda _reader: {})

    def present(_reader, entries, *_args):
        return inbound.Held({'seq': entry.ref, 'text': 'hello'} for entry in entries[:1])

    monkeypatch.setattr(inbound, 'present', present)

    class Output(io.StringIO):
        def write(self, value):
            assert not reader.progress
            if failure == 'write':
                raise BrokenPipeError('closed')
            return super().write(value)

        def flush(self):
            assert not reader.progress
            if failure == 'flush':
                raise BrokenPipeError('closed')
            return super().flush()

    monkeypatch.setattr(board_cli.sys, 'stdout', Output())
    if failure == 'write':
        with pytest.raises(BrokenPipeError):
            board_cli.runner(handler)(arguments())
    else:
        assert board_cli.runner(handler)(arguments()) == (3 if failure else 0)
    assert reader.progress == ({} if failure else {'device': 2})


def test_a_filtered_output_cannot_acknowledge_a_skipped_earlier_message():
    reader = Reader()
    out = inbound.Held({'seq': entry.ref} for entry in reader.entries)
    inbound.acknowledge_after_output(reader, reader.entries, out)
    assert out.after_output is not None
    out.after_output([{'seq': '3'}])
    assert reader.progress == {}
    out.after_output([{'seq': '2'}])
    assert reader.progress == {'device': 2}


@pytest.mark.parametrize('text', ['broken', '{"inbox": {"device": "7"}}', '{"inbox": []}'])
def test_malformed_saved_cursor_is_not_silently_reset(tmp_path, text):
    folder = credentials.prepare(tmp_path) / 'board'
    folder.mkdir(mode=0o700)
    path = folder / 'reader.cursors.json'
    path.write_text(text)
    path.chmod(0o600)
    with pytest.raises(ValueError):
        credentials.remember(tmp_path, 'board', 'reader', 'inbox', {'device': 2})
    assert path.read_text() == text


def test_remember_merges_the_single_validated_snapshot(tmp_path, monkeypatch):
    credentials.remember(tmp_path, 'board', 'reader', 'inbox', {'device': 7})

    def reread(*_args):
        raise AssertionError('validated cursor must not be read again')

    monkeypatch.setattr(credentials, 'cursors', reread)
    credentials.remember(tmp_path, 'board', 'reader', 'inbox', {'remote': 3})
    target = tmp_path / 'client' / 'board' / 'reader.cursors.json'
    assert credentials._read_cursors(target) == {'inbox': {'device': 7, 'remote': 3}}


def test_cursor_replaced_during_open_is_refused(tmp_path, monkeypatch):
    credentials.remember(tmp_path, 'board', 'reader', 'inbox', {'device': 7})
    target = tmp_path / 'client' / 'board' / 'reader.cursors.json'
    external = tmp_path / 'external'
    external.write_text('keep')
    original = credentials.os.open
    opened = []

    def replace(path, flags, *args, **kwargs):
        handle = original(path, flags, *args, **kwargs)
        if path == target:
            opened.append(handle)
            target.unlink()
            target.symlink_to(external)
        return handle

    monkeypatch.setattr(credentials.os, 'open', replace)
    with pytest.raises(ValueError, match='changed while opening'):
        credentials.remember(tmp_path, 'board', 'reader', 'inbox', {'device': 8})
    assert external.read_text() == 'keep'
    with pytest.raises(OSError):
        credentials.os.fstat(opened[0])


@pytest.mark.parametrize('component', ['state', 'client', 'board'])
def test_private_cursor_parents_refuse_redirects_without_touching_target(tmp_path, component):
    state = tmp_path / 'state'
    external = tmp_path / 'external'
    external.mkdir(mode=0o700)
    if component == 'state':
        state.symlink_to(external, target_is_directory=True)
    else:
        state.mkdir(mode=0o700)
        path = state / 'client'
        if component == 'board':
            path.mkdir(mode=0o700)
            path /= 'board'
        path.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match=r'symlink|plain directory'):
        credentials.remember(state, 'board', 'reader', 'inbox', {'device': 1})
    assert list(external.iterdir()) == []
    assert stat.S_IMODE(external.stat().st_mode) == 0o700


def test_shared_private_parent_is_refused_without_changing_its_mode(tmp_path):
    state = tmp_path / 'state'
    state.mkdir(mode=0o755)
    with pytest.raises(ValueError, match='lets others read'):
        credentials.remember(state, 'board', 'reader', 'inbox', {'device': 1})
    assert stat.S_IMODE(state.stat().st_mode) == 0o755
    assert list(state.iterdir()) == []


def test_cursor_cache_uses_authenticated_identity_instead_of_cached_label(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from poolhouse.board.session import Session

    reader = Session(SimpleNamespace(state=tmp_path), 'board', 'own-test-token', named='other-label')
    monkeypatch.setattr(Session, 'whoami', lambda _self: SimpleNamespace(name='reader'))
    reader.keep('inbox', {'device': 4})
    assert reader.cursor('inbox') == {'device': 4}
    assert credentials.cursors(tmp_path, 'board', 'reader') == {'inbox': {'device': 4}}
    assert not (tmp_path / 'client' / 'board' / 'other-label.cursors.json').exists()


@pytest.mark.parametrize('operation', ['cursor', 'keep'])
def test_invalid_token_cannot_read_or_write_a_cursor_cache(tmp_path, monkeypatch, operation):
    from types import SimpleNamespace

    from poolhouse.board.client import Denied
    from poolhouse.board.session import Session

    reader = Session(SimpleNamespace(state=tmp_path), 'board', 'invalid-test-token', named='other-label')

    def refuse(_self):
        raise Denied('denied', 'token is not known')

    def touched(*_args):
        raise AssertionError('unauthenticated cursor path must not be accessed')

    monkeypatch.setattr(Session, 'whoami', refuse)
    monkeypatch.setattr(credentials, 'cursors', touched)
    monkeypatch.setattr(credentials, 'remember', touched)
    with pytest.raises(Denied):
        if operation == 'cursor':
            reader.cursor('inbox')
        else:
            reader.keep('inbox', {'device': 4})
    assert list(tmp_path.iterdir()) == []
