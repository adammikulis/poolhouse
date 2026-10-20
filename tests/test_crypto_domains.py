"""Current namespace separation refuses foreign encrypted state without rewriting it."""
import base64
import hashlib
import hmac
import json

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from poolhouse import keystore, sealing
from poolhouse.activity.log import ActivityLog, Unreadable
from poolhouse.fleet.onboard import joining, signing
from poolhouse.memory import vault
from poolhouse.requests.store import Ask, Inbox, Unavailable, build

KEY = bytes(range(32))
FOREIGN = b'foreign/application/v1'


def test_poolhouse_default_key_derivation_and_wrapping_reject_foreign_domains(tmp_path):
    assert keystore.hkdf(KEY, b'purpose') == keystore.hkdf(KEY, b'purpose', salt=b'poolhouse/keystore/v1')
    assert keystore.hkdf(KEY, b'purpose') != keystore.hkdf(KEY, b'purpose', salt=FOREIGN)
    assert sealing.box_key('synthetic secret') == keystore.hkdf(b'synthetic secret', sealing.INFO)
    keys = keystore.Keystore(user='synthetic-account', directory=tmp_path)
    keys._key = bytearray(KEY)  # temporary in-process fixture; no backend calls
    blob = keys.wrap('memory', 'owner', b'private data')
    assert keys.unwrap('memory', 'owner', blob) == b'private data'
    nonce = bytes(range(12))
    foreign = b'MKW1' + nonce + keystore.aead(keys.subkey('memory', 'owner')).encrypt(nonce, b'private data', FOREIGN)
    for altered in (foreign, blob[:-1] + bytes([blob[-1] ^ 1])):
        with pytest.raises(keystore.KeystoreError):
            keys.unwrap('memory', 'owner', altered)
    with pytest.raises(keystore.KeystoreError):
        keys.unwrap('other-purpose', 'owner', blob)


def test_memory_key_identifier_has_current_domain_and_authenticates_header(monkeypatch):
    assert vault._kid(KEY) == hmac.new(KEY, b'poolhouse/memory/key-id/v1', hashlib.sha256).digest()[:vault.KID]
    blob = vault.seal_blob(b'private data', KEY, mode='passphrase', salt=bytes(16), owner='owner')
    assert vault.open_blob(blob, [KEY], owner='owner')[0] == b'private data'
    with monkeypatch.context() as patch:
        patch.setattr(vault, '_kid', lambda key: hmac.new(key, FOREIGN, hashlib.sha256).digest()[:vault.KID])
        foreign = vault.seal_blob(b'private data', KEY, mode='passphrase', salt=bytes(16), owner='owner')
    for altered in (foreign, blob[:8] + bytes([blob[8] ^ 1]) + blob[9:]):
        with pytest.raises(vault.BadSeal):
            vault.open_blob(altered, [KEY], owner='owner')


def test_foreign_request_file_cannot_be_read_or_overwritten(tmp_path):
    inbox = Inbox(tmp_path, key=lambda: KEY)
    nonce = bytes(range(12))
    original = b'MLR1' + nonce + keystore.aead(KEY).encrypt(nonce, b'{"rows":[]}', FOREIGN)
    inbox.path.write_bytes(original)
    with pytest.raises(Unavailable):
        inbox.list()
    with pytest.raises(Unavailable):
        inbox.add(build(Ask('tool_call', 'synthetic operation', 'reversible local operation',
                            ('allow-once', 'deny')), 1800000000.0), 'synthetic-secret')
    assert inbox.path.read_bytes() == original


def test_activity_reports_foreign_ciphertext_in_place(tmp_path):
    log = ActivityLog(tmp_path, key=lambda: KEY)
    nonce, at = bytes(range(12)), 1800000000.0
    box = keystore.aead(KEY).encrypt(nonce, b'{}', FOREIGN + repr(at).encode())
    log.append_record({'v': 1, 'ts': at, 'ct': base64.b64encode(nonce + box).decode()})
    before = {path: path.read_bytes() for path in log.files()}
    assert all(isinstance(entry, Unreadable) for entry in log.entries())
    assert {path: path.read_bytes() for path in log.files()} == before


def test_signing_file_uses_current_domain_and_refuses_foreign_ciphertext(tmp_path):
    path, password = tmp_path / 'signing.enc', 'synthetic passphrase'
    signing.seal_file(path, KEY, password)
    assert signing.open_file(path, password) == KEY
    doc = json.loads(path.read_text())
    salt, nonce = (base64.b64decode(doc[field]) for field in ('salt', 'nonce'))
    cipher = ChaCha20Poly1305(signing._kdf(password, salt, doc['n']))
    assert cipher.decrypt(nonce, base64.b64decode(doc['ciphertext']), b'poolhouse/signing/v1') == KEY
    with pytest.raises(InvalidTag):
        cipher.decrypt(nonce, base64.b64decode(doc['ciphertext']), FOREIGN)
    doc['ciphertext'] = base64.b64encode(cipher.encrypt(nonce, KEY, FOREIGN)).decode()
    path.write_text(json.dumps(doc))
    before = path.read_bytes()
    with pytest.raises(signing.KeyStoreError):
        signing.open_file(path, password)
    assert path.read_bytes() == before


def test_join_secret_uses_poolhouse_namespace_and_pool_binding():
    salt = hashlib.sha256(b'poolhouse/join/v1/' + b'synthetic-pool').digest()
    expected = base64.urlsafe_b64encode(keystore.scrypt_key('passphrase', salt)).decode().rstrip('=')
    assert joining.join_secret('passphrase', 'synthetic-pool') == expected
    assert joining.join_secret('passphrase', 'other-pool') != expected


def test_unsupported_signing_storage_never_migrates_or_generates_identity(tmp_path):
    keys = signing.SigningKeys(tmp_path / 'keys', say=lambda _message: None)
    for operation in (keys._get, keys._drop):
        with pytest.raises(signing.KeyStoreError, match='Unsupported'):
            operation('unsupported')
    assert not list(tmp_path.rglob('*'))
