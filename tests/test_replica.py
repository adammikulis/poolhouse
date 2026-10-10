"""Posts are logged, show a sync status, and reach a member through the real exchange."""

import base64
import os
import subprocess
import sys
from pathlib import Path

import pytest
from workspace_kit import Kit, clean_env

from poolhouse import macauth
from poolhouse.fleet.onboard.manifest import Signer
from poolhouse.fleet.remote import Member
from poolhouse.workspace import device_logs_merge as rules, replica_fold, replica_sync
from poolhouse.workspace.identity import Denied
from poolhouse.workspace.replica import Replica
from poolhouse.workspace.service import Workspace

TESTS = Path(__file__).resolve().parent
SRC = TESTS.parent / 'src'


def kinds(ws):
    return [r['kind'] for r in ws.replica.logs.rows(ws.replica.origin)]


def posts(ws):
    return [(r['from'], r['role'], r['body']) for r in ws.bus.log.rows()]


def test_with_no_joined_device_every_post_is_logged_and_synced_without_signing(tmp_path, monkeypatch):
    kit = Kit(clean_env(monkeypatch, tmp_path))
    kit.ws.replica.logs.signer = lambda: pytest.fail('a pool of one never signs')
    alice, bob = kit.agent('alice'), kit.agent('bob')
    kit.ws.send(alice, '#general', 'status', 'hello all')
    kit.ws.announce(alice, 'milestone', 'one line')
    note = kit.ws.note_add(alice, 'fact', 'a title', 'a body')
    assert kinds(kit.ws) == ['message', 'announce', 'note']
    assert note['sync'] == 'synced'
    assert [r['sync'] for r in kit.ws.outbox(alice)] == ['synced', 'synced']
    assert replica_sync.run(kit.ws, kit.owner) == []
    assert bob


def test_direct_messages_stay_on_the_device_that_wrote_them(tmp_path, monkeypatch):
    kit = Kit(clean_env(monkeypatch, tmp_path))
    alice, bob = kit.agent('alice'), kit.agent('bob')
    kit.ws.send(alice, 'bob', 'status', 'private words')
    assert kinds(kit.ws) == []
    assert [r['sync'] for r in kit.ws.outbox(alice)] == ['local']
    assert kit.ws.inbox(bob, raw=True)[0]['raw'] == 'private words'


def test_an_agent_token_cannot_start_a_sync(tmp_path, monkeypatch):
    kit = Kit(clean_env(monkeypatch, tmp_path))
    with pytest.raises(Denied):
        replica_sync.run(kit.ws, kit.agent('alice'))
    assert replica_sync.run(kit.ws, kit.owner) == []


def test_a_post_with_an_unreachable_member_is_durable_provisional_and_never_errors(tmp_path, monkeypatch):
    base = clean_env(monkeypatch, tmp_path)
    kit = Kit(base)
    key = Signer.generate()
    kit.ws.replica = Replica(base, signer=lambda: key, roster=lambda: ['f' * 64])
    alice = kit.agent('alice')
    kit.ws.send(alice, '#general', 'status', 'queued')
    assert [r['sync'] for r in kit.ws.outbox(alice)] == ['provisional']
    nowhere = replica_sync.Target('f' * 64, Member('http://127.0.0.1:1', 'x', timeout=1))
    assert replica_sync.sync(kit.ws, [nowhere])[0]['error'] == 'unreachable or refused'
    reopened = Workspace(base)
    reopened.replica = Replica(base, roster=lambda: ['f' * 64])
    assert [r['sync'] for r in reopened.outbox(alice)] == ['provisional']
    assert reopened.replica.logs.rows(reopened.replica.origin)[0]['body']['body'] == 'queued'


def test_a_repeated_request_id_logs_one_row(tmp_path, monkeypatch):
    kit = Kit(clean_env(monkeypatch, tmp_path))
    first = kit.ws.replica.record('message', 'alice', {'body': 'x'}, idem='req-1')
    again = kit.ws.replica.record('message', 'alice', {'body': 'x'}, idem='req-1')
    other = kit.ws.replica.record('message', 'bob', {'body': 'x'}, idem='req-1')
    assert again == first and other['seq'] == 2


def test_a_row_logged_but_not_written_to_the_board_is_applied_by_the_next_fold(tmp_path, monkeypatch):
    kit = Kit(clean_env(monkeypatch, tmp_path))
    kit.agent('alice')
    kit.ws.replica.record('announce', 'alice', {'type': 'milestone', 'from': 'alice', 'role': 'agent',
                                             'to': '#announcements', 'subject': 'milestone', 'body': 'crashed before the board'})
    assert posts(kit.ws) == []
    assert replica_fold.apply(kit.ws) == 1
    assert posts(kit.ws) == [('alice', 'agent', 'crashed before the board')]
    assert replica_fold.apply(kit.ws) == 0


@pytest.fixture
def join(tmp_path, monkeypatch):
    """Device A in this process and device B as a real daemon process, each with its own state root."""
    secret = base64.urlsafe_b64encode(b'm' * 32).decode()
    a_for_b, b_for_a = 'a' * 64, 'b' * 64
    root_b = tmp_path / 'device-b'
    environment = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(SRC), str(TESTS)]),
                   'POOLHOUSE_WORKSPACE_HOME': str(root_b / 'workspace'), 'POOLHOUSE_HOME': str(root_b / 'home'),
                   'PYTHON_KEYRING_BACKEND': 'onboard_support.FileKeyring',
                   'POOLHOUSE_TEST_KEYRING': str(root_b / 'keyring.json'), 'POOLHOUSE_NOTIFY': 'off'}
    environment.pop('CLAUDECODE', None)
    server = subprocess.Popen([sys.executable, str(TESTS / 'replica_server.py'), str(root_b), a_for_b, secret],
                              env=environment, stdout=subprocess.PIPE, text=True)
    try:
        port = int(server.stdout.readline())
        clean_env(monkeypatch, tmp_path / 'device-a')
        kit_a = Kit(tmp_path / 'device-a' / 'workspace')
        key = Signer.generate()
        kit_a.ws.replica = Replica(kit_a.base, signer=lambda: key, roster=lambda: [b_for_a])
        kit_b = Kit(root_b / 'workspace')
        kit_b.ws.replica = Replica(kit_b.base, roster=lambda: [a_for_b])
        kit_b.ws.replica.logs.signer = lambda: pytest.fail('the daemon signs for device B')
        member = Member(f'http://127.0.0.1:{port}', macauth.derive(base64.urlsafe_b64decode(secret)), timeout=60)
        yield kit_a, kit_b, replica_sync.Target(b_for_a, member)
    finally:
        server.terminate()
        server.wait(10)
        server.stdout.close()


def test_two_devices_exchange_posts_and_notes_and_mark_them_synced(join):
    a, b, target = join
    alice, bob = a.agent('alice'), b.agent('bob')
    a.ws.send(alice, '#general', 'status', 'from device a')
    a.ws.announce(alice, 'milestone', 'a is up')
    a.ws.note_add(alice, 'fact', 'shared fact', 'known on a')
    assert [r['sync'] for r in a.ws.outbox(alice)] == ['provisional', 'provisional']

    report = replica_sync.sync(a.ws, [target])[0]
    assert report['pushed'] >= 4 and not report['refused'] and report['signed']
    assert [r['sync'] for r in a.ws.outbox(alice)] == ['synced', 'synced']
    assert posts(b.ws) == [('alice@d1', 'agent', 'from device a'), ('alice@d1', 'agent', 'a is up')]
    found = b.ws.note_search('shared')
    assert [n['author'] for n in found] == ['alice@d1'] and 'known on a' in found[0]['text']

    b.ws.send(bob, '#general', 'status', 'from device b')
    assert b.ws.outbox(bob)[0]['sync'] == 'provisional'
    report = replica_sync.sync(a.ws, [target])[0]
    assert report['pulled'] >= 2 and not report['refused']
    assert ('bob@d1', 'agent', 'from device b') in posts(a.ws)
    assert b.ws.outbox(bob)[0]['sync'] == 'synced'
    before = posts(a.ws)
    assert replica_sync.sync(a.ws, [target])[0]['pulled'] == 0
    assert posts(a.ws) == before

    merged_a = rules.merge({o: a.ws.replica.logs.rows(o) for o in a.ws.replica.logs.origins()})
    merged_b = rules.merge({o: b.ws.replica.logs.rows(o) for o in b.ws.replica.logs.origins()})
    assert [rules.row_id(r) for r in merged_a] == [rules.row_id(r) for r in merged_b]
    assert replica_fold.apply(a.ws) == 0


def test_a_person_role_in_a_row_from_another_device_is_stored_as_an_agent(join):
    a, b, target = join
    a.agent('alice')
    b.agent('bob')
    a.ws.replica.record('message', 'alice', {'type': 'status', 'from': 'alice', 'role': 'human',
                                          'to': '#general', 'subject': '', 'body': 'I am the owner'})
    replica_sync.sync(a.ws, [target])
    assert posts(b.ws) == [('alice@d1', 'agent', 'I am the owner')]


def test_a_member_serving_a_head_under_another_key_is_refused_and_nothing_of_it_is_folded(join):
    a, b, target = join
    a.agent('alice')
    bob = b.agent('bob')
    b.ws.send(bob, '#general', 'status', 'real')
    replica_sync.sync(a.ws, [target])
    other = Signer.generate()
    b_origin = b.ws.replica.origin
    a.ws.replica.logs.keys.put(b_origin, {'public': base64.b64encode(other.public).decode(), 'bound': True})
    b.ws.send(bob, '#general', 'status', 'after the key was swapped')
    report = replica_sync.sync(a.ws, [target])[0]
    assert report['refused'] == {b_origin: 'Damaged'}
    assert [body for _, _, body in posts(a.ws)] == ['real']
    assert b_origin in a.ws.replica.logs.damaged()


def test_a_device_cannot_present_a_second_log_under_the_same_fingerprint(join):
    a, _, target = join
    a.agent('alice')
    replica_sync.sync(a.ws, [target])
    a.ws.replica.members.put(target.fingerprint, 'c' * 32)
    assert replica_sync.sync(a.ws, [target])[0]['error'] == 'unreachable or refused'
