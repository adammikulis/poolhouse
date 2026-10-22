"""Durable conversation creation intent, duplicates and read-only reconciliation."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from poolhouse.fleet import conversation_creation as creation, conversation_graph as graph
from poolhouse.fleet.conversations import Conversations

IDENTITY = {"principal": "browser-session", "scope": "pool-device-root", "device": "device-a"}
OPERATION = "a" * 32


@pytest.fixture
def store(tmp_path):
    return Conversations(tmp_path / "chats")


def create(store, request=None, **options):
    return store.create(OPERATION, request or {}, options.pop("identity", IDENTITY),
                        defaults=options.pop("defaults", {}), current=options.pop("current", lambda: True), **options)


def test_duplicate_create_and_lost_ack_attach_historical_response_after_reopen(store):
    first = create(store, {"model": "model-a"}, defaults={"max_output_tokens": 512})
    store.update(first["id"], title="Changed", model="model-b")
    store.append(first["id"], "user", "Keep later content")
    restarted = Conversations(store.root)
    assert create(restarted, {"model": "model-a"}, defaults={"max_output_tokens": 2048}) == first
    assert restarted.creation(OPERATION, IDENTITY, current=lambda: True) == first
    assert len(restarted.all()) == 1
    assert restarted.get(first["id"]).title == "Changed"
    assert restarted.get(first["id"]).messages[0].content == "Keep later content"


@pytest.mark.parametrize("changed", [{"model": "other"}, {"title": "Other"}, {"settings": {"effort": "high"}},
                                     {"settings": {"temperature": None}}])
def test_changed_request_cannot_reuse_operation(store, changed):
    first = create(store)
    with pytest.raises(creation.Conflict):
        create(store, changed)
    assert [row.id for row in store.all()] == [first["id"]]


@pytest.mark.parametrize("field", ["principal", "scope", "device"])
def test_other_identity_cannot_create_or_reconcile_original_operation(store, field):
    first = create(store)
    other = {**IDENTITY, field: "other"}
    with pytest.raises(creation.Conflict):
        create(store, identity=other)
    assert store.creation(OPERATION, other, current=lambda: True) is None
    assert [row.id for row in store.all()] == [first["id"]]


def test_unknown_reconciliation_never_initializes_storage(store):
    assert store.creation(OPERATION, IDENTITY, current=lambda: True) is None
    assert not store.root.exists()


@pytest.mark.parametrize("current", [False, None, 1])
def test_inactive_identity_cannot_initialize_or_read_storage(store, current):
    with pytest.raises(PermissionError):
        create(store, current=lambda: current)
    with pytest.raises(PermissionError):
        store.creation(OPERATION, IDENTITY, current=lambda: current)
    assert not store.root.exists()


@pytest.mark.parametrize("operation", ["", "A" * 32, "a" * 31, "../other", True, None])
def test_invalid_operation_cannot_initialize_storage(store, operation):
    with pytest.raises(ValueError):
        store.create(operation, {}, IDENTITY, defaults={}, current=lambda: True)
    assert not store.root.exists()


@pytest.mark.parametrize("payload", [{"id": "chosen"}, {"model": 1}, {"title": None}, {"settings": None},
                                     {"settings": {"temperature": float("nan")}},
                                     {"settings": {"principal": "owner"}}, {"model": "x" * 4097}])
def test_invalid_creation_fields_cannot_initialize_storage(store, payload):
    with pytest.raises(ValueError):
        create(store, payload)
    assert not store.root.exists()


def test_revocation_after_graph_save_rolls_back_conversation_receipt_and_marker(store, monkeypatch):
    valid = [True]
    original = graph.save
    def save(database, conversation):
        original(database, conversation)
        valid[0] = False
    monkeypatch.setattr(graph, "save", save)
    with pytest.raises(PermissionError):
        create(store, current=lambda: valid[0])
    assert store.creation(OPERATION, IDENTITY, current=lambda: True) is None
    assert store.all() == []


@pytest.mark.parametrize("missing", ["receipt", "marker", "edge", "conversation", "digest"])
def test_missing_or_corrupt_retained_evidence_never_recreates(store, missing):
    first = create(store)
    key = creation.operation(OPERATION)
    with graph.opened(store.root) as database, database.transaction():
        if missing == "receipt":
            database.delete_doc(key)
        elif missing == "marker":
            database.query("MATCH (n:Node {id:$id}) DETACH DELETE n", {"id": key})
        elif missing == "edge":
            database.query("MATCH (n:Node {id:$id})-[e:Edge {rel:'created'}]->() DELETE e", {"id": key})
        elif missing == "conversation":
            graph.remove(database, first["id"])
        else:
            row = database.get_doc(key)
            row["response"]["model"] = "substituted"
            database.put_doc(key, row)
    with pytest.raises(creation.Conflict):
        create(store)
    assert len(store.all()) == (0 if missing == "conversation" else 1)


def test_copied_graph_cannot_adopt_original_storage_binding(store, tmp_path):
    create(store)
    copy = tmp_path / "copy"
    shutil.copytree(store.root, copy)
    with pytest.raises(creation.Conflict):
        create(Conversations(copy))
    assert Conversations(copy).creation(OPERATION, IDENTITY, current=lambda: True) is None


def test_actual_two_process_duplicate_creators_commit_one_conversation(store, tmp_path):
    barrier = tmp_path / "begin"
    source = '''import json,sys,time
from pathlib import Path
from poolhouse.fleet.conversations import Conversations
root,barrier,operation,identity=sys.argv[1:]
while not Path(barrier).exists(): time.sleep(.005)
print(json.dumps(Conversations(root).create(operation, {"model":"model-a"}, json.loads(identity), defaults={}, current=lambda:True)))
'''
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    processes = [subprocess.Popen([sys.executable, "-c", source, str(store.root), str(barrier), OPERATION,
                                  json.dumps(IDENTITY)], cwd=tmp_path, env=environment, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        barrier.touch()
        responses = []
        for process in processes:
            output, error = process.communicate(timeout=20)
            assert process.returncode == 0, error
            responses.append(json.loads(output))
        assert responses[0] == responses[1]
        assert [row.id for row in store.all()] == [responses[0]["id"]]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)



def test_redirected_storage_alias_cannot_create_or_adopt_existing_receipt(store, tmp_path):
    first = create(store)
    alias = tmp_path / "alias"
    alias.symlink_to(store.root, target_is_directory=True)
    with pytest.raises(PermissionError):
        create(Conversations(alias))
    with pytest.raises(PermissionError):
        Conversations(alias).creation(OPERATION, IDENTITY, current=lambda: True)
    assert [row.id for row in store.all()] == [first["id"]]
