"""Poolhouse reads its own key after its macOS executable changes, with no person: the old executable hands it to the new one.

Real signed copies of the interpreter run the real hand-off steps as separate processes; the keychain is `keychain_stand_in`
(plumbing only), which lets an item be read only by the code identity that added it, as macOS was measured to.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import keyring
import pytest

from poolhouse import (
    app_bundle,
    home,
    keystore,
    keystore_guard,
    keystore_handoff,
    node_signing,
    runtime,
    runtime_deploy,
    runtime_identity,
    runtime_store,
    setup,
)

sys.path.insert(0, str(Path(__file__).parent))
import keychain_stand_in

KEY = "v1:" + base64.b64encode(b"plumbing-only master key, 32 b!!").decode()
BASE = "poolhouse/" + keystore.Keystore().account
IMPORTS = [entry for entry in sys.path if entry and Path(entry).is_dir()]


@pytest.fixture
def keychain(tmp_path, monkeypatch):
    """The installed software of a Mac with the plumbing-only keychain, in this process's children too."""
    if sys.platform != "darwin":
        pytest.skip("macOS ties a keychain item to the code that made it")
    monkeypatch.setenv("POOLHOUSE_TEST_KEYCHAIN", str(tmp_path / "keychain.json"))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keychain_stand_in.Keyring")
    monkeypatch.setattr(keystore_handoff, "_paths", lambda: IMPORTS)
    monkeypatch.setattr(runtime_identity, "applies", lambda: True)
    monkeypatch.setattr(runtime_identity, "_certificate", lambda: "")
    monkeypatch.setattr(node_signing, "usable_certificate", lambda: "")
    monkeypatch.setattr(app_bundle, "ICON_SVG", Path("/nowhere/Poolhouse.svg"))
    return keychain_stand_in


def interpreter() -> Path:
    return Path(os.path.realpath(sys.executable))


def identity_of(program: Path) -> str:
    code = "import sys; sys.path[:0] = sys.argv[1:]; from poolhouse import keystore_guard; print(keystore_guard.code_identity())"
    return subprocess.run([str(program), "-I", "-S", "-c", code, *IMPORTS], capture_output=True, text=True, check=True).stdout.strip()


def made_by(owner: str) -> None:
    """An install whose one key item the code ``owner`` added, as the state files say."""
    keychain_stand_in.keep({"items": {BASE: {"owner": owner, "value": KEY}}})
    directory = home.state("keystore")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "provisioned.json").write_text(json.dumps({"at": 1.0}))


def installed() -> Path:
    """A Poolhouse executable whose own signature made the key."""
    executable = runtime_identity.ensure_bundle(interpreter())
    made_by(identity_of(executable))
    return executable


def faults(**what) -> None:
    keychain_stand_in.keep({**keychain_stand_in.held(), **what})


def another_build(monkeypatch, letter: str = "f") -> None:
    """The next executable asked for holds other bytes, as one from an upgraded interpreter does."""
    monkeypatch.setattr(runtime_identity, "digest", lambda files: letter * 64)


def values() -> list[str]:
    return [row["value"] for row in keychain_stand_in.held()["items"].values()]


def test_a_rebuilt_executable_reads_the_key_and_the_old_item_stays(keychain, monkeypatch):
    executable = installed()
    old = identity_of(executable)
    another_build(monkeypatch)
    runtime_identity.ensure_bundle(interpreter())
    new = identity_of(executable)
    assert new != old and keystore_handoff.reads(executable)
    assert keychain.held()["items"][BASE] == {"owner": old, "value": KEY}, "the item that was there is not touched"
    assert keychain.held()["items"][f"{BASE}@{new}"] == {"owner": new, "value": KEY}
    assert runtime_identity.unreadable_note() == ""


def test_one_upgrade_after_another_hands_the_key_on_each_time(keychain, monkeypatch):
    executable = installed()
    for letter in "fe":
        another_build(monkeypatch, letter)
        runtime_identity.ensure_bundle(interpreter())
        assert keystore_handoff.reads(executable) and set(values()) == {KEY}


def test_enable_and_remove_hand_the_key_on(keychain, monkeypatch):
    executable = installed()
    original, local = identity_of(executable), node_signing.sign

    def certificate(bundle, identifier, requirement_for):  # plumbing only: another signature stands in for the certificate's
        subprocess.run(["codesign", "--force", "--sign", "-", "--identifier", "plumbing.certificate", "--timestamp=none", str(bundle)],
                       check=True, capture_output=True)
        return "certificate"

    for signer in (certificate, local):
        monkeypatch.setattr(node_signing, "sign", signer)
        runtime_identity.resign(runtime_identity.bundle_path(), runtime_identity.BUNDLE_ID, runtime_identity.requirement)
        assert keystore_handoff.reads(executable) and set(values()) == {KEY}
    assert identity_of(executable) == original and len(keychain.held()["items"]) == 2, "the first signature reads its item again"


def test_a_stop_between_the_hand_off_and_the_move_leaves_the_old_executable_with_its_key(keychain, monkeypatch):
    executable = installed()
    owner = identity_of(executable)

    def stopped(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(runtime_identity, "_swap", stopped)
    another_build(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        runtime_identity.ensure_bundle(interpreter())
    assert identity_of(executable) == owner and keychain.held()["items"][BASE] == {"owner": owner, "value": KEY}
    assert len(keychain.held()["items"]) == 2


@pytest.mark.parametrize("fault", ["refuse_add", "locked"])
def test_a_hand_off_that_fails_now_keeps_the_executable_that_reads_the_key(keychain, monkeypatch, capsys, fault):
    executable = installed()
    stamp, owner = executable.stat().st_ino, identity_of(executable)
    faults(**{fault: True})
    another_build(monkeypatch)
    assert runtime_identity.ensure_bundle(interpreter()) == executable, "the update goes on"
    assert executable.stat().st_ino == stamp and identity_of(executable) == owner
    assert "keeps the executable" in capsys.readouterr().err and runtime_identity.unreadable_note() == ""
    assert keychain.held()["items"] == {BASE: {"owner": owner, "value": KEY}}, "nothing was written or deleted"


def test_a_new_executable_that_cannot_read_the_key_from_its_place_is_taken_out_again(keychain, monkeypatch):
    executable = installed()
    owner = identity_of(executable)
    faults(refuse_check=True)
    another_build(monkeypatch)
    with pytest.raises(runtime_identity.IdentityError, match="does not read the key from its place"):
        runtime_identity.ensure_bundle(interpreter())
    assert identity_of(executable) == owner and keychain.held()["items"][BASE]["owner"] == owner


def test_a_selection_that_fails_leaves_an_executable_that_reads_the_key(keychain, monkeypatch):
    executable = installed()
    owner = identity_of(executable)
    with pytest.raises(OSError, match="publish"), runtime_identity.restored_on_failure():
        another_build(monkeypatch)
        runtime_identity.ensure_bundle(interpreter())
        raise OSError("plumbing-only: publish failed")
    assert identity_of(executable) == owner and keychain.held()["items"][BASE]["owner"] == owner
    assert len(keychain.held()["items"]) == 2, "the key had been handed on; the item that was there is the one read"


def test_a_key_the_old_executable_cannot_read_is_said_and_the_upgrade_goes_on(keychain, monkeypatch, capsys):
    executable = runtime_identity.ensure_bundle(interpreter())
    made_by("a" * 40)  # some earlier program made it
    another_build(monkeypatch)
    runtime_identity.ensure_bundle(interpreter())
    assert runtime_identity.source_of(runtime_identity.bundle_path())["sha256"] == "f" * 64 and executable.is_file()
    said = runtime_identity.unreadable_note()
    assert "cannot read the encryption key" in said and "does not let" in said and "has not been verified" in said
    assert "cannot read the encryption key" in capsys.readouterr().err
    assert keychain.held()["items"] == {BASE: {"owner": "a" * 40, "value": KEY}}, "nothing was written or deleted"
    [finding] = [one for one in setup.signature_findings() if one.name == "macOS encryption key"]
    assert not finding.good and finding.fix == ["poolhouse-security", "unlock"]


def test_no_program_left_that_starts_is_a_clear_state(keychain, monkeypatch):
    installed()
    starts = runtime_identity.bare_problem
    monkeypatch.setattr(runtime_identity, "bare_problem",
                        lambda path: "plumbing-only: its Python is gone" if ".building" not in str(path) else starts(path))
    another_build(monkeypatch)
    runtime_identity.ensure_bundle(interpreter())
    assert "no program that could read it starts" in runtime_identity.unreadable_note()


def test_before_the_first_binding_the_selected_runtimes_interpreter_hands_the_key_on(keychain, tmp_path, monkeypatch):
    selected = tmp_path / "selected"
    (selected / "bin").mkdir(parents=True)
    (selected / "bin" / "python").symlink_to(interpreter())
    monkeypatch.setattr(runtime_identity.runtime, "selection_prefix", lambda: selected)
    made_by(identity_of(interpreter()))
    executable = runtime_identity.ensure_bundle(interpreter())
    assert keystore_handoff.reads(executable) and runtime_identity.unreadable_note() == ""


def test_a_key_the_bare_python_made_is_handed_on_though_an_executable_is_already_there(keychain, tmp_path, monkeypatch):
    executable = runtime_identity.ensure_bundle(interpreter())
    selected = tmp_path / "selected"
    (selected / "bin").mkdir(parents=True)
    (selected / "bin" / "python").symlink_to(interpreter())
    monkeypatch.setattr(runtime_identity.runtime, "selection_prefix", lambda: selected)
    made_by(identity_of(interpreter()))
    another_build(monkeypatch)
    runtime_identity.ensure_bundle(interpreter())
    assert keystore_handoff.reads(executable) and runtime_identity.unreadable_note() == ""


def test_only_the_current_item_and_the_one_before_stay(keychain, monkeypatch):
    executable = installed()
    owners = [identity_of(executable)]
    for letter in "fed":
        another_build(monkeypatch, letter)
        runtime_identity.ensure_bundle(interpreter())
        owners.append(identity_of(executable))
        runtime_identity.retire_old_keys()
    kept = {row["owner"] for row in keychain.held()["items"].values()}
    assert kept == set(owners[-2:]) and keystore_handoff.reads(executable)


def test_a_key_that_cannot_be_handed_on_is_in_the_updates_outcome_and_record(keychain, tmp_path, monkeypatch):
    prefix = tmp_path / "runtimes" / "one"
    subprocess.run([runtime_identity.unbound(Path(sys.executable)), "-m", "venv", "--without-pip", str(prefix)], check=True,
                   capture_output=True)
    chosen = runtime.Runtime(prefix, "a" * 40, "0", runtime.identity())
    runtime_identity.bind(chosen)
    made_by("a" * 40)
    another_build(monkeypatch)
    told = runtime_deploy._identity(chosen, runtime_deploy.Plan(tmp_path, chosen.commit, None))
    assert any("cannot read the encryption key" in one for one in told)
    assert any("cannot read the encryption key" in one for one in runtime_store.read_state()["identity_notices"])


def test_a_read_that_succeeds_ends_the_unreadable_state(keychain):
    made_by(keystore_guard.code_identity())
    keyring.set_keyring(keychain.Keyring())
    store = keystore.Keystore(wires=keystore.Wires(interactive=lambda: False))
    store.mark_unreadable("plumbing-only: recorded by an update")
    assert runtime_identity.unreadable_note()
    assert len(store.subkey("plumbing-only")) == 32 and runtime_identity.unreadable_note() == ""


def test_a_program_with_no_item_of_its_own_never_makes_a_second_key(keychain, monkeypatch):
    executable = installed()
    for letter in "fe":
        another_build(monkeypatch, letter)
        runtime_identity.ensure_bundle(interpreter())
        runtime_identity.retire_old_keys()
    assert keystore_handoff.reads(executable)
    before = keychain.held()["items"]
    assert BASE not in before, "the first item made was retired"
    keyring.set_keyring(keychain.Keyring())
    person = keystore.Keystore(wires=keystore.Wires(interactive=lambda: True, say=lambda _: None))
    with pytest.raises(keystore.KeystoreDenied):  # macOS is asked about an item that is there, not told the key is gone
        person.provision()
    assert keychain.held()["items"] == before
    faults(items={})
    with pytest.raises(keystore.KeystoreMissing, match="no second key"):
        person.provision()
    assert keychain.held()["items"] == {}


def test_a_hostile_program_path_is_one_argument_of_every_step_and_no_shell_reads_it(monkeypatch):
    seen = []

    def refuse(argv, **kwargs):  # plumbing only: nothing is started
        seen.append((argv, kwargs))
        raise OSError("plumbing-only: nothing is started")

    monkeypatch.setattr(keystore_handoff.subprocess, "Popen", refuse)
    monkeypatch.setattr(keystore_handoff.subprocess, "run", refuse)
    hostile = Path("/x/$(touch pwned); `id` | rm -rf y/Poolhouse")
    with pytest.raises(keystore_handoff.HandoverError):
        keystore_handoff.hand_over(hostile, hostile)
    assert keystore_handoff.reads(hostile) is False and keystore_handoff.retire(hostile) is False
    assert [argv[5] for argv, _ in seen] == ["give", "check", "retire"]
    for argv, kwargs in seen:
        assert argv[:5] == [str(hostile), "-I", "-S", "-c", keystore_handoff.BOOT] and not kwargs.get("shell")


def test_the_key_goes_only_through_the_pipe(keychain, monkeypatch, tmp_path, capfd):
    installed()
    started, popen = [], subprocess.Popen

    def recorded(argv, **kwargs):
        started.append((argv, kwargs.get("env")))
        return popen(argv, **kwargs)

    monkeypatch.setattr(keystore_handoff.subprocess, "Popen", recorded)
    another_build(monkeypatch)
    runtime_identity.ensure_bundle(interpreter())
    said = capfd.readouterr()
    secret = KEY.removeprefix("v1:")
    assert [argv[5] for argv, _ in started if keystore_handoff.BOOT in argv] == ["give", "take", "check"]
    assert all(secret not in " ".join(map(str, argv)) and env is None for argv, env in started)
    assert secret not in said.out and secret not in said.err
    assert not [one for one in home.home().rglob("*") if one.is_file() and secret.encode() in one.read_bytes()]
    with (tmp_path / "out").open("w") as plain:
        keyring.set_keyring(keychain.Keyring())
        assert keystore.Keystore(wires=keystore.Wires(interactive=lambda: False)).give(plain) == keystore.FAILED
    assert (tmp_path / "out").read_text() == "", "the key is written to nothing but a pipe"
