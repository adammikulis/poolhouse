"""The signing key: in the OS keystore by default, encrypted at rest when there is none, never
plaintext on disk; export, rotation and revocation need a permit, which is refused to an agent at once and minted
directly for a process no agent started (no terminal, no typed confirmation)."""

import base64
import io
import json

import keyring
import pytest
from onboard_support import FileKeyring, Recorder

from poolhouse import caller, keystore as keystore_module
from poolhouse.fleet.onboard import manifest as mf, signing
from poolhouse.fleet.onboard.signing import KeyStoreError, SigningKeys
from poolhouse.sentinel.permit import AgentRefused, mint


@pytest.fixture(autouse=True)
def needs_cryptography():
    pytest.importorskip("cryptography")


@pytest.fixture
def keystore(tmp_path, monkeypatch):
    """The OS keystore, stood in for by a real keyring backend over a file."""
    monkeypatch.setenv("POOLHOUSE_TEST_KEYRING", str(tmp_path / "keystore.json"))
    before = keyring.get_keyring()
    keyring.set_keyring(FileKeyring())
    yield tmp_path / "keystore.json"
    keyring.set_keyring(before)


@pytest.fixture
def no_keystore(monkeypatch):
    from keyring.backends import fail
    before = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    yield
    keyring.set_keyring(before)


def permit(action, subject):
    """The permit a process no agent started gets for ``action`` on ``subject``: no terminal, no typed confirmation."""
    return mint(action, subject)


def keys_in(tmp_path, **more):
    rec = Recorder()
    said = []
    return SigningKeys(tmp_path / "state", bus=rec.bus, say=said.append, **more), rec, said


def files_under(root):
    return [p for p in root.rglob("*") if p.is_file()]


def holds_secret(tmp_path, signer_raw):
    needles = (signer_raw, base64.b64encode(signer_raw), base64.urlsafe_b64encode(signer_raw),
               signer_raw.hex().encode())
    return [p for p in files_under(tmp_path / "state")
            if any(n in p.read_bytes() for n in needles)]


def test_the_default_home_of_the_key_is_the_keystore_and_no_file_holds_it(tmp_path, keystore):
    keys, rec, said = keys_in(tmp_path)
    doc = keys.meta()
    assert doc["store"] == "keystore" and said == []
    raw = keys._get("keystore").private_raw()
    assert holds_secret(tmp_path, raw) == []
    assert keys.wrapped_path.exists() and not keys.file_path.exists()
    held = json.loads(keystore.read_text())
    assert list(held) == [f"{keystore_module.SERVICE}/{keystore_module.default().account}"]
    assert all(raw not in base64.b64decode(v[3:]) for v in held.values())
    assert (tmp_path / "state" / "signing.json").stat().st_mode & 0o077 == 0
    assert base64.b64decode(doc["public"]) == keys.public and doc["key_id"] == keys.key_id
    assert rec.of("onboard.signing.created")[0].evidence == {"store": "keystore"}


def test_signing_is_automatic_and_the_manifest_is_short_lived(tmp_path, keystore):
    keys, rec, _ = keys_in(tmp_path)
    f = tmp_path / "a-0.1-py3-none-any.whl"
    f.write_bytes(b"w" * 1000)
    raw = keys.sign([mf.Signer.entry_for(f, kind="wheel")], serial=5)     # no prompt, no grant
    m = mf.verify(raw, keys.public)
    assert m.serial == 5 and 0 < m.expires - m.issued <= 7 * 86400
    assert rec.of("onboard.signing.signed")


def test_without_a_keystore_the_key_is_encrypted_at_rest_and_the_owner_is_warned(
        tmp_path, no_keystore):
    keys, rec, said = keys_in(tmp_path, passphrase=lambda _p: "a long passphrase")
    doc = keys.meta()
    assert doc["store"] == "file"
    assert said and "no OS keystore" in said[0] and str(keys.file_path) in said[0]
    assert rec.of("onboard.signing.file_fallback")[0].severity == "warning"
    assert keys.file_path.stat().st_mode & 0o077 == 0
    raw = signing.open_file(keys.file_path, "a long passphrase")
    assert len(raw) == 32 and holds_secret(tmp_path, raw) == []
    assert json.loads(keys.file_path.read_text())["kdf"] == "scrypt"
    f = tmp_path / "p.whl"
    f.write_bytes(b"x" * 100)
    mf.verify(keys.sign([mf.Signer.entry_for(f, kind="wheel")], serial=1), keys.public)


def test_the_wrong_passphrase_does_not_unlock_the_file(tmp_path, no_keystore):
    keys, _, _ = keys_in(tmp_path, passphrase=lambda _p: "right passphrase")
    keys.meta()
    wrong, _, _ = keys_in(tmp_path, passphrase=lambda _p: "wrong passphrase")
    with pytest.raises(KeyStoreError, match="wrong passphrase"):
        wrong.sign([], serial=1)


def test_no_keystore_and_no_passphrase_is_a_clear_refusal_not_a_plaintext_key(
        tmp_path, no_keystore, monkeypatch):
    monkeypatch.delenv(signing.UNLOCK_ENV, raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO())       # not a terminal
    keys = SigningKeys(tmp_path / "state", bus=Recorder().bus, say=lambda _m: None)
    with pytest.raises(KeyStoreError, match="passphrase"):
        keys.meta()
    assert files_under(tmp_path / "state") == []


def test_exporting_needs_a_permit_and_writes_only_an_encrypted_copy(tmp_path, keystore):
    keys, rec, _ = keys_in(tmp_path)
    keys.meta()
    out = tmp_path / "backup.enc"
    with pytest.raises(AgentRefused):
        keys.export(permit("rotate", keys.key_id), out, "pw pw pw pw")     # a grant for another act
    assert not out.exists()
    keys.export(permit("export", keys.key_id), out, "pw pw pw pw")
    raw = signing.open_file(out, "pw pw pw pw")
    assert mf.Signer.from_raw(raw).key_id == keys.key_id
    assert raw not in out.read_bytes() and rec.of("onboard.signing.exported")


def test_rotating_needs_a_permit_and_the_old_key_announces_the_new_one(tmp_path, keystore):
    keys, rec, _ = keys_in(tmp_path)
    old_public, old_id = keys.public, keys.key_id
    f = tmp_path / "p.whl"
    f.write_bytes(b"x" * 100)
    with pytest.raises(AgentRefused):
        keys.rotate(permit("export", old_id))
    assert keys.key_id == old_id
    fresh = keys.rotate(permit("rotate", old_id))
    assert fresh["key_id"] != old_id and len(fresh["rotations"]) == 1
    raw = keys.sign([mf.Signer.entry_for(f, kind="wheel")], serial=2)
    with pytest.raises(mf.RotationAnnounced) as moved:            # a member still on the old key
        mf.verify(raw, old_public)
    assert moved.value.new_public == keys.public
    assert mf.verify(raw, keys.public).key_id == keys.key_id      # once it has been pinned
    held = json.loads(keystore.read_text())
    assert list(held) == [f"{keystore_module.SERVICE}/{keystore_module.default().account}"]   # only the master is in the keystore
    assert keys.wrapped_path.exists()
    assert rec.of("onboard.signing.rotated")[0].severity == "warning"


def test_revoking_needs_a_permit_and_travels_in_every_manifest_after(tmp_path, keystore):
    keys, _, _ = keys_in(tmp_path)
    with pytest.raises(AgentRefused):
        keys.revoke(permit("rotate", "ab" * 32), "ab" * 32)
    keys.revoke(permit("revoke", "ab" * 32), "ab" * 32)
    assert mf.verify(keys.sign([], serial=1), keys.public).revoked_keys == ("ab" * 32,)


def test_a_key_that_was_in_the_keystore_does_not_silently_become_a_file(tmp_path, keystore):
    keys, _, _ = keys_in(tmp_path)
    keys.meta()
    keyring.set_keyring(__import__("keyring.backends.fail", fromlist=["x"]).Keyring())
    with pytest.raises(KeyStoreError, match="keystore"):
        keys.sign([], serial=1)
    assert not keys.file_path.exists()


@pytest.mark.parametrize("action", ["export", "rotate", "revoke"])
def test_an_agent_is_refused_every_key_command_at_once_and_the_key_is_unchanged(tmp_path, keystore, monkeypatch, action):
    """No permit is minted for a process an agent started, so the command never reaches the key."""
    keys, rec, _ = keys_in(tmp_path)
    keys.meta()
    old_id, doc = keys.key_id, keys.wrapped_path.read_bytes()
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(AgentRefused, match="refused to an agent") as refused:
        permit(action, old_id)
    assert "CLAUDECODE" in str(refused.value) and action in str(refused.value)
    assert keys.key_id == old_id and keys.wrapped_path.read_bytes() == doc
    assert not rec.of("onboard.signing.exported") and not rec.of("onboard.signing.rotated") and not rec.of("onboard.signing.revoked")
    rows = [r for r in caller.audit_rows() if r["event"] == "refused"]
    assert len(rows) == 1 and rows[0]["action"] == f"{action} {old_id}" and rows[0]["who"].startswith("agent CLAUDECODE")


def test_a_process_no_agent_started_gets_each_permit_with_no_terminal_and_the_audit_says_who(tmp_path, keystore):
    keys, _, _ = keys_in(tmp_path)
    keys.meta()
    for action in ("export", "rotate", "revoke"):
        permit(action, keys.key_id)
    rows = [r for r in caller.audit_rows() if r["event"] == "ran"]
    assert [r["action"] for r in rows] == [f"{a} {keys.key_id}" for a in ("export", "rotate", "revoke")]
    assert all(r["who"].startswith("user ") for r in rows)


def test_rotating_a_key_held_in_an_encrypted_file_replaces_the_file(tmp_path, no_keystore):
    keys, _, _ = keys_in(tmp_path, passphrase=lambda _p: "a long passphrase")
    old = keys.key_id
    fresh = keys.rotate(permit("rotate", old))
    assert fresh["key_id"] != old and fresh["store"] == "file"
    assert signing.open_file(keys.file_path, "a long passphrase")
    mf.verify(keys.sign([], serial=1), keys.public)


def test_a_fetch_adopts_a_rotation_the_pinned_key_signed_at_once_and_nothing_waits(tmp_path, monkeypatch):
    """A member still on the old key meets a manifest of the new one, handed over by a statement the old key signed: the new
    key is pinned in the same fetch, with no `signing accept` and no person."""
    import argparse

    from poolhouse.fleet.onboard import cli
    old, new = mf.Signer.generate(), mf.Signer.generate()
    raw = new.sign([], serial=2, carried=mf.Carried((old.announce_rotation(new),)))
    state = tmp_path / "state"
    state.mkdir()
    (state / "trust.json").write_text(json.dumps({
        "certificate": "c", "signing_key": base64.b64encode(old.public).decode(),
        "device_secret": base64.urlsafe_b64encode(b"s" * 32).decode()}), encoding="utf-8")
    monkeypatch.setattr(cli, "resolve", lambda host, *a, **k: host)
    monkeypatch.setattr(cli, "pinned_context", lambda cert: None)
    monkeypatch.setattr(cli, "MemberSource", lambda *a, **k: object())
    monkeypatch.setattr(cli, "fetch_manifest", lambda member: raw)
    monkeypatch.setattr(cli, "remember", lambda *a, **k: None)
    monkeypatch.setattr("builtins.input", lambda _prompt="": pytest.fail("a person was asked"))
    args = argparse.Namespace(state=str(state), source="host:7447", names=[], into="", json=True)
    assert cli.cmd_fetch(args) == 0
    held = json.loads((state / "trust.json").read_text(encoding="utf-8"))
    assert base64.b64decode(held["signing_key"]) == new.public and held["serial"] == 2 and "pending_key" not in held
