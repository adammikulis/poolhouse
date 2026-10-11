"""Where the device key is kept: in the keystore wherever the computer has one that needs nothing of a person, under a passphrase or in a
file marked weak where it has none, and never as a file beside a node that was handed it.

The keystore here is a dictionary (`MemoryRing`) for the tests that stay in one process and a file (`FileKeyring`) for the ones that start
the supervisor, which is a process of its own; no test reads or writes the machine's keystore.
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from poolhouse import (
    keystore,
    node_binary as node_binary_module,
    node_health,
    node_key,
    node_launch,
    node_permission,
    node_supervise,
)
from poolhouse.node_key import KeyUnavailable

pytest_plugins = ["node_kit"]

SEED = bytes(range(32))
HERE = str(Path(__file__).resolve().parent)
SRC = str(Path(__file__).resolve().parents[1] / "src")


@pytest.fixture
def ks(tmp_path):
    """A keystore whose master key exists, so a background process may read it."""
    store = keystore.Keystore(directory=tmp_path / "keystore", wires=keystore.Wires(interactive=lambda: True))
    store.provision()
    return store


@pytest.fixture
def untouched(tmp_path):
    """A keystore nobody has made a master key in, with no person at a screen to be asked."""
    return keystore.Keystore(directory=tmp_path / "untouched", wires=keystore.Wires(interactive=lambda: False))


@pytest.fixture
def person_here(tmp_path):
    """A keystore nobody has made a master key in, with a person at a screen who would be asked the one question."""
    return keystore.Keystore(directory=tmp_path / "person", wires=keystore.Wires(interactive=lambda: True))


@pytest.fixture
def state(tmp_path):
    path = tmp_path / "node"
    path.mkdir(mode=0o700)
    return path


def is_a_note(path: Path) -> bool:
    """``path`` holds the words a node that reads it as a key stops on."""
    return path.read_text() == node_key.NOTE and not path.read_text().strip().isalnum()


def held_anywhere(root: Path, seed: bytes) -> list[str]:
    """The files under ``root`` that hold the key, raw or as hex."""
    return [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()
            and (seed in p.read_bytes() or seed.hex().encode() in p.read_bytes())]


def test_a_new_device_with_a_keystore_that_is_ready_keeps_its_key_there_and_in_no_file(state, ks):
    released = node_key.release(state, ks=ks)
    assert released is not None and released.custody == "keystore" and len(released.seed) == 32
    assert is_a_note(state / "device.key"), "a node that knows nothing of the keystore stops on it instead of making a key"
    assert held_anywhere(state, released.seed) == [], "no file of the state directory holds the key"
    assert node_key.status(state)["custody"] == "keystore" and node_key.status(state)["fingerprint"] == node_key.fingerprint(released.seed)
    assert node_key.release(state, ks=ks) == released, "the same key every time it is asked for"


def test_a_copy_of_the_state_directory_does_not_open_the_key(tmp_path, state, ks):
    node_key.release(state, ks=ks)
    copy = tmp_path / "copy"
    shutil.copytree(state, copy)
    with pytest.raises(KeyUnavailable, match="keystore"):
        node_key.release(copy, ks=ks)


def test_a_state_directory_that_was_given_another_computers_keystore_does_not_open_the_key(tmp_path, state, ks):
    node_key.release(state, ks=ks)
    other = keystore.Keystore(directory=tmp_path / "other", wires=keystore.Wires(interactive=lambda: True))
    other._key = bytearray(os.urandom(32))
    with pytest.raises(KeyUnavailable, match="keystore"):
        node_key.release(state, ks=other)


def test_a_wrapped_key_that_is_gone_is_said_not_made_again(state, ks):
    node_key.release(state, ks=ks)
    (state / "device.key.wrapped").unlink()
    with pytest.raises(KeyUnavailable, match="gone"):
        node_key.release(state, ks=ks)
    assert not (state / "device.key.wrapped").exists(), "a new key is never made for a device that had one"


def test_a_new_device_with_no_keystore_ready_leaves_the_node_to_make_a_weak_key_file(state, untouched, monkeypatch, caplog):
    monkeypatch.delenv(node_key.PASSPHRASE_ENV, raising=False)
    with caplog.at_level("WARNING", logger="poolhouse.node_key"):
        assert node_key.release(state, ks=untouched) is None
    assert "not kept in the keystore" not in caplog.text, "the keystore was not even tried: nobody was there to be asked"
    assert list(state.iterdir()) == []
    assert untouched.status()["provisioned"] is False, "a node nobody is at the screen for never makes the keystore's master key"
    assert node_key.status(state) | {"keystore": None} == {"custody": "none", "weak": True, "fingerprint": "", "keystore": None}
    (state / "device.key").write_text(SEED.hex())
    assert node_key.release(state, ks=untouched) is None, "an existing key file is the node's to read"
    assert node_key.status(state)["custody"] == "file" and node_key.status(state)["weak"] is True


def test_the_keystore_master_key_is_let_go_of_after_every_release_whatever_the_outcome(state, ks, monkeypatch):
    locked: list[int] = []
    monkeypatch.setattr(ks, "lock", lambda: locked.append(1))
    node_key.release(state, ks=ks)
    node_key.release(state, ks=ks)
    (state / "device.key.wrapped").unlink()
    with pytest.raises(KeyUnavailable):
        node_key.release(state, ks=ks)
    assert len(locked) == 3


def test_a_released_key_is_never_shown_when_it_is_printed(state, ks):
    released = node_key.release(state, ks=ks)
    assert released is not None
    assert released.seed.hex() not in repr(released) and repr(released.seed) not in repr(released)


def test_the_status_of_a_directory_whose_record_cannot_be_read_says_unknown_and_does_not_fail(state):
    (state / "device.custody.json").write_text("{")
    assert node_key.status(state)["custody"] == "unknown" and node_key.status(state)["weak"] is True


def test_a_new_device_with_a_person_at_the_screen_is_born_into_the_keystore_and_makes_its_master_key(state, person_here):
    released = node_key.release(state, ks=person_here)
    assert released is not None and released.custody == "keystore"
    assert person_here.status()["provisioned"] is True


def test_a_directory_that_is_a_devices_and_says_nothing_of_its_key_is_never_given_another(tmp_path, state, ks):
    first = node_key.release(state, ks=ks)
    assert first is not None
    wrapped = (state / "device.key.wrapped").read_bytes()
    (state / "device.custody.json").unlink()
    with pytest.raises(KeyUnavailable, match="no record"):
        node_key.release(state, ks=ks)
    (state / "device.key").unlink()
    with pytest.raises(KeyUnavailable, match="no record"):
        node_key.release(state, ks=ks)
    (state / "device.custody.json").write_text("{")
    with pytest.raises(KeyUnavailable, match="cannot be read"):
        node_key.release(state, ks=ks)
    assert (state / "device.key.wrapped").read_bytes() == wrapped, "the key of the device was not overwritten"
    only_a_certificate = tmp_path / "cert"
    only_a_certificate.mkdir()
    (only_a_certificate / "device.cert").write_bytes(b"x")
    with pytest.raises(KeyUnavailable, match="no record"):
        node_key.release(only_a_certificate, ks=ks)


def test_a_new_device_with_a_passphrase_keeps_its_key_encrypted_and_asks_for_it_again(state, untouched, monkeypatch):
    monkeypatch.setenv(node_key.PASSPHRASE_ENV, "correct horse battery")
    released = node_key.release(state, ks=untouched)
    assert released is not None and released.custody == "passphrase"
    assert (state / "device.key.enc").is_file() and held_anywhere(state, released.seed) == []
    assert node_key.release(state, ks=untouched) == released
    monkeypatch.setenv(node_key.PASSPHRASE_ENV, "another one")
    with pytest.raises(KeyUnavailable, match="would not open"):
        node_key.release(state, ks=untouched)
    monkeypatch.delenv(node_key.PASSPHRASE_ENV)
    with pytest.raises(KeyUnavailable, match=node_key.PASSPHRASE_ENV):
        node_key.release(state, ks=untouched)


def test_moving_a_key_file_into_the_keystore_keeps_the_identity_and_removes_the_file(state, ks):
    (state / "device.key").write_text(SEED.hex())
    moved = node_key.migrate(state, ks=ks)
    assert moved == {"moved": True, "custody": "keystore", "fingerprint": node_key.fingerprint(SEED)}
    assert is_a_note(state / "device.key")
    assert held_anywhere(state, SEED) == []
    released = node_key.release(state, ks=ks)
    assert released is not None and released.seed == SEED and released.custody == "keystore"
    assert node_key.migrate(state, ks=ks)["moved"] is False, "nothing is left to move"


def test_a_wrapped_key_that_is_not_this_devices_is_not_overwritten_by_moving_a_key_file(state, ks):
    (state / "device.key.wrapped").write_bytes(b"MKW1" + b"\0" * 40)
    (state / "device.key").write_text(SEED.hex())
    with pytest.raises(KeyUnavailable, match="not this device's key"):
        node_key.migrate(state, ks=ks)
    assert (state / "device.key").read_text() == SEED.hex()
    assert (state / "device.key.wrapped").read_bytes() == b"MKW1" + b"\0" * 40


def test_a_move_that_stopped_after_the_wrapped_copy_is_finished_by_running_it_again(state, ks):
    (state / "device.key").write_text(SEED.hex())
    (state / "device.key.wrapped").write_bytes(ks.wrap(node_key.PURPOSE, node_key._owner(state), SEED))
    assert node_key.migrate(state, ks=ks)["moved"] is True
    released = node_key.release(state, ks=ks)
    assert released is not None and released.seed == SEED


def test_a_key_file_stays_when_the_keystore_will_not_take_the_key(state, ks, monkeypatch):
    (state / "device.key").write_text(SEED.hex())

    def refuses(*_args):
        raise keystore.KeystoreDenied("the keystore said no")

    monkeypatch.setattr(ks, "wrap", refuses)
    with pytest.raises(KeyUnavailable, match="the key file stays"):
        node_key.migrate(state, ks=ks)
    assert (state / "device.key").read_text() == SEED.hex()
    assert not (state / "device.custody.json").exists() and not (state / "device.key.wrapped").exists()


def test_a_key_file_stays_when_the_wrapped_copy_does_not_open_again(state, ks, monkeypatch):
    (state / "device.key").write_text(SEED.hex())
    monkeypatch.setattr(ks, "unwrap", lambda *_args: b"\x00" * 32)
    with pytest.raises(KeyUnavailable):
        node_key.migrate(state, ks=ks)
    assert (state / "device.key").read_text() == SEED.hex()
    assert not (state / "device.custody.json").exists()


def test_a_file_that_is_not_a_key_is_not_moved(state, ks):
    (state / "device.key").write_text("not a key")
    with pytest.raises(KeyUnavailable, match="does not hold a device key"):
        node_key.migrate(state, ks=ks)
    assert (state / "device.key").read_text() == "not a key"


def test_moving_a_key_without_a_keystore_needs_the_passphrase_and_then_keeps_it_under_it(state, untouched, monkeypatch):
    monkeypatch.delenv(node_key.PASSPHRASE_ENV, raising=False)
    monkeypatch.setattr(keystore.Keystore, "available", lambda self: False)
    (state / "device.key").write_text(SEED.hex())
    with pytest.raises(KeyUnavailable, match=node_key.PASSPHRASE_ENV):
        node_key.migrate(state, ks=untouched)
    assert (state / "device.key").exists()
    monkeypatch.setenv(node_key.PASSPHRASE_ENV, "a passphrase")
    (state / "device.key.enc").write_bytes(b"another device's")
    with pytest.raises(KeyUnavailable, match="is here already"):
        node_key.migrate(state, ks=untouched)
    assert (state / "device.key").read_text() == SEED.hex() and (state / "device.key.enc").read_bytes() == b"another device's"
    (state / "device.key.enc").unlink()
    assert node_key.migrate(state, ks=untouched)["custody"] == "passphrase"
    assert is_a_note(state / "device.key")
    released = node_key.release(state, ks=untouched)
    assert released is not None and released.seed == SEED


def test_the_command_that_moves_the_key_is_a_persons(state, ks, monkeypatch):
    (state / "device.key").write_text(SEED.hex())
    monkeypatch.setenv("CLAUDECODE", "1")
    args = type("Args", (), {"state": str(state), "migrate": True})()
    assert node_permission._key_command(args) == 2
    assert (state / "device.key").read_text() == SEED.hex(), "an agent moved nothing"


def test_the_command_moves_the_key_and_restarts_a_node_that_is_running(state, ks, monkeypatch, capsys):
    (state / "device.key").write_text(SEED.hex())
    calls: list[str] = []
    monkeypatch.setattr(node_permission.human, "require_person", lambda _what: None)
    monkeypatch.setattr(node_key.keystore, "default", lambda: ks)
    monkeypatch.setattr(node_health, "node_health", lambda _state: {"pid": 1})
    monkeypatch.setattr(node_launch, "stop_node", lambda _state: calls.append("stop") or True)
    monkeypatch.setattr(node_launch, "ensure_node", lambda _state: calls.append("start") or {})
    assert node_permission._key_command(type("Args", (), {"state": str(state), "migrate": True})()) == 0
    assert calls == ["stop", "start"]
    said = capsys.readouterr().out
    assert "keystore" in said and "backup or copy" in said


def test_the_command_says_so_when_the_node_does_not_start_again(state, ks, monkeypatch):
    (state / "device.key").write_text(SEED.hex())
    monkeypatch.setattr(node_permission.human, "require_person", lambda _what: None)
    monkeypatch.setattr(node_key.keystore, "default", lambda: ks)
    monkeypatch.setattr(node_health, "node_health", lambda _state: {"pid": 1})
    monkeypatch.setattr(node_launch, "stop_node", lambda _state: True)

    def refuses(_state):
        raise node_launch.NodeUnavailable("it did not answer")

    monkeypatch.setattr(node_launch, "ensure_node", refuses)
    assert node_permission._key_command(type("Args", (), {"state": str(state), "migrate": True})()) == 1


def test_the_command_without_a_flag_says_where_the_key_is_and_touches_nothing(state, capsys):
    (state / "device.key").write_text(SEED.hex())
    assert node_permission._key_command(type("Args", (), {"state": str(state), "migrate": False})()) == 0
    assert "weak" in capsys.readouterr().out
    assert (state / "device.key").exists()


class RecordingNode:
    """A program in the place of the node binary: it writes down what it was started with and what it read from standard input."""

    def __init__(self, folder: Path) -> None:
        self.record = folder / "started.json"
        self.path = folder / "poolhouse-node"
        self.path.write_text(
            f"#!{sys.executable}\nimport json, os, sys\n"
            f"json.dump({{'argv': sys.argv[1:], 'stdin': sys.stdin.read(), 'env': dict(os.environ)}}, open({str(self.record)!r}, 'w'))\n")
        self.path.chmod(0o755)

    def started(self) -> dict:
        deadline = time.monotonic() + 10
        while not self.record.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        return json.loads(self.record.read_text())


def test_the_supervisor_gives_the_key_on_standard_input_and_nowhere_else(tmp_path, state, ks, monkeypatch):
    released = node_key.release(state, ks=ks)
    node = RecordingNode(tmp_path)
    monkeypatch.setattr(node_key, "release", lambda *_args, **_kw: released)
    child = node_supervise._spawn(state, node.path, "0" * 64, [], released)
    child.wait(timeout=10)
    seen = node.started()
    assert seen["stdin"] == released.seed.hex() + "\n"
    assert seen["argv"][:5] == ["run", "--state", str(state), "--key-stdin", "--key-custody"] and seen["argv"][5] == "keystore"
    assert released.seed.hex() not in " ".join(seen["argv"]) and released.seed.hex() not in repr(seen["env"])
    assert released.seed.hex() not in (state / "node.log").read_text()


def test_the_node_is_not_given_the_passphrase_the_supervisor_was(tmp_path, state, ks, monkeypatch):
    monkeypatch.setenv(node_key.PASSPHRASE_ENV, "a passphrase nobody else needs")
    released = node_key.release(state, ks=ks)
    node = RecordingNode(tmp_path)
    node_supervise._spawn(state, node.path, "0" * 64, [], released).wait(timeout=10)
    assert "a passphrase nobody else needs" not in repr(node.started()["env"])


def test_a_node_with_a_key_file_is_started_with_nothing_on_standard_input(tmp_path, state):
    node = RecordingNode(tmp_path)
    node_supervise._spawn(state, node.path, "0" * 64, [], None).wait(timeout=10)
    seen = node.started()
    assert seen["stdin"] == "" and "--key-stdin" not in seen["argv"]


def test_the_supervisor_does_not_start_a_node_whose_key_it_cannot_have(tmp_path, state, monkeypatch):
    node = RecordingNode(tmp_path)

    def refuses(*_args, **_kw):
        raise KeyUnavailable("the keystore would not give the key")

    monkeypatch.setattr(node_key, "release", refuses)
    monkeypatch.setattr(node_supervise, "resolve", lambda _state: (node.path, "0" * 64))
    monkeypatch.setattr(node_supervise, "MIN_DELAY_S", 0.01)
    until = time.monotonic() + 1.5
    assert node_supervise._loop(state, [], lambda: node.record.exists() or time.monotonic() > until) == 0
    assert "the keystore would not give the key" in (state / "node.log").read_text()
    assert not node.record.exists(), "no node was started"


def test_a_keystore_that_will_not_answer_for_a_while_does_not_end_the_supervisor(tmp_path, state, monkeypatch):
    node = RecordingNode(tmp_path)
    refusals = []

    def release(*_args, **_kw):
        refusals.append(1)
        if len(refusals) <= node_supervise.GIVE_UP_AFTER + 3:
            raise KeyUnavailable("the keystore is locked")
        return None

    monkeypatch.setattr(node_key, "release", release)
    monkeypatch.setattr(node_supervise, "resolve", lambda _state: (node.path, "0" * 64))
    monkeypatch.setattr(node_supervise, "MIN_DELAY_S", 0.01)
    monkeypatch.setattr(node_supervise, "MAX_DELAY_S", 0.05)
    until = time.monotonic() + 30
    assert node_supervise._loop(state, [], lambda: node.record.exists() or time.monotonic() > until) == 0
    assert node.record.exists(), "the node started once the keystore answered"
    assert (state / "node.log").read_text().count("the keystore is locked") == 1, "the same refusal is said once"


def test_a_keystore_that_the_person_would_not_open_is_asked_a_few_times_and_then_left_alone(tmp_path, state, ks, monkeypatch):
    node = RecordingNode(tmp_path)
    asked: list[float] = []

    def denied(*_args, **_kw):
        asked.append(time.monotonic())
        raise KeyUnavailable("the person declined the keystore", denied=True)

    monkeypatch.setattr(node_key, "release", denied)
    monkeypatch.setattr(node_supervise, "resolve", lambda _state: (node.path, "0" * 64))
    monkeypatch.setattr(node_supervise, "DENIED_WAIT_S", 0.3)
    monkeypatch.setattr(node_supervise, "MIN_DELAY_S", 0.01)
    until = time.monotonic() + 10
    assert node_supervise._loop(state, [], lambda: time.monotonic() > until) == 1, "a person who said no is not asked for ever"
    assert len(asked) == node_supervise.DENIED_GIVE_UP
    assert all(later - earlier >= 0.25 for earlier, later in itertools.pairwise(asked)), "the keystore's own cooldown is waited out between asks"
    assert "not asked again" in (state / "node.log").read_text()
    assert not node.record.exists()


def test_a_denied_keystore_is_what_the_key_says_it_was(state, ks, monkeypatch):
    node_key.release(state, ks=ks)

    def declined(*_args):
        raise keystore.KeystoreDenied("declined")

    monkeypatch.setattr(ks, "unwrap", declined)
    with pytest.raises(KeyUnavailable) as caught:
        node_key.release(state, ks=ks)
    assert caught.value.denied is True
    (state / "device.key.wrapped").write_bytes(b"x")
    monkeypatch.setattr(ks, "unwrap", lambda *_args: (_ for _ in ()).throw(keystore.KeystoreError("not this one")))
    with pytest.raises(KeyUnavailable) as other:
        node_key.release(state, ks=ks)
    assert other.value.denied is False


@pytest.mark.skipif(os.name != "posix", reason="core-file limits are a POSIX setting")
def test_a_supervisor_that_holds_keys_can_leave_no_core_file(tmp_path, monkeypatch):
    done = subprocess.run(
        [sys.executable, "-c", "import resource; from poolhouse import node_supervise; node_supervise._no_core_files(); print(resource.getrlimit(resource.RLIMIT_CORE))"],
        env={**os.environ, "PYTHONPATH": SRC}, capture_output=True, text=True, timeout=60, check=True)
    assert done.stdout.strip() == "(0, 0)"
    asked: list[str] = []
    monkeypatch.setattr(node_supervise, "_no_core_files", lambda: asked.append("no core files"))
    monkeypatch.setattr(node_supervise, "_stop_requests", lambda _state: (lambda: True, lambda: None))
    assert node_supervise.supervise(tmp_path / "state") == 0
    assert asked == ["no core files"], "the supervisor asks for it before it holds a key"


def test_the_supervisor_does_not_keep_the_key_it_gave_while_the_node_runs(tmp_path, state, monkeypatch):
    import gc
    import weakref

    node = RecordingNode(tmp_path)
    made: list[weakref.ref] = []
    held_while_running: list[bool] = []

    def release(*_args, **_kw):
        key = node_key.Released(SEED, "keystore")
        made.append(weakref.ref(key))
        return key

    def waiting(*_args, **_kw):
        gc.collect()
        held_while_running.append(made[0]() is not None)
        return 0

    monkeypatch.setattr(node_key, "release", release)
    monkeypatch.setattr(node_supervise, "resolve", lambda _state: (node.path, "0" * 64))
    monkeypatch.setattr(node_supervise, "_spawn", lambda *_args, **_kw: type("Child", (), {"pid": 1, "returncode": 0})())
    monkeypatch.setattr(node_supervise, "_wait", waiting)
    monkeypatch.setattr(node_supervise, "_pause", lambda *_args, **_kw: None)
    stops = iter([False, True])
    node_supervise._loop(state, [], lambda: next(stops))
    assert held_while_running == [False], "the supervisor lets go of the key once the node has it"


@pytest.fixture
def keystore_node(node_binary, monkeypatch, tmp_path):
    """A state directory whose supervisor, a process of its own, reaches a keystore that already has its master key; the node starts on it."""
    import keyring
    from onboard_support import FileKeyring

    root = Path(tempfile.mkdtemp(prefix="ml", dir="/tmp"))
    state = root / "node"
    state.mkdir(mode=0o700)
    ring = tmp_path / "keyring.json"
    for name, value in {"PYTHON_KEYRING_BACKEND": "onboard_support.FileKeyring", "POOLHOUSE_TEST_KEYRING": str(ring),
                        "PYTHONPATH": f"{SRC}:{HERE}", "POOLHOUSE_HOME": str(root)}.items():
        monkeypatch.setenv(name, value)
    before = keyring.get_keyring()
    keyring.set_keyring(FileKeyring())
    keystore.Keystore(wires=keystore.Wires(interactive=lambda: True)).provision()
    node_supervise.point(state, node_binary, node_binary_module.sha256(node_binary))
    yield state
    node_launch.stop_node(state)
    keyring.set_keyring(before)
    shutil.rmtree(root, ignore_errors=True)


def test_a_node_started_by_its_supervisor_runs_on_a_key_it_was_given_and_keeps_none(keystore_node):
    state = keystore_node
    first = node_launch.ensure_node(state)
    said = node_health.call(state, "pool_status")
    assert said["device_key"] == {"custody": "keystore", "weak": False}
    assert is_a_note(state / "device.key") and (state / "device.key.wrapped").is_file()
    released = node_key.release(state)
    assert released is not None and held_anywhere(state, released.seed) == []
    assert first["fingerprint"] == node_key.fingerprint(released.seed)
    subprocess.run(["kill", "-9", str(first["pid"])], check=True)
    again = node_launch.ensure_node(state)
    assert again["pid"] != first["pid"] and again["fingerprint"] == first["fingerprint"], "the restart is the same device"


def test_a_copy_of_the_state_directory_of_a_running_device_starts_no_node(keystore_node, tmp_path):
    state = keystore_node
    node_launch.ensure_node(state)
    node_launch.stop_node(state)
    copy = state.parent / "copy"
    copy.mkdir(mode=0o700)
    for entry in state.iterdir():
        if entry.is_file() and entry.name not in {"node.lock", "node.sock", "supervisor.lock"}:
            shutil.copy2(entry, copy / entry.name)
    node_supervise.point(copy, Path(node_supervise.pointer(state)["binary"]), node_supervise.pointer(state)["sha256"])
    with pytest.raises(node_launch.NodeUnavailable, match="keystore"):
        node_launch.ensure_node(copy, wait_s=8.0)
    assert is_a_note(copy / "device.key"), "the copy did not make an identity of its own"
