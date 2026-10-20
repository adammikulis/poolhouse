//! The person's keys: the Ed25519 public keys enrolled on this node. A grant counts only when one of them signed
//! it. The private halves live in the person's keystore, never here, and nothing in this module signs.
//!
//! The first key is enrolled with a one-time secret that the operating system's own prompt made when the person said yes to it
//! (`presence`): the node keeps it in memory, never in a file, and no request can set one.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use ed25519_dalek::{Signature, VerifyingKey};
use serde::{Deserialize, Serialize};

use crate::error::{Error, Result};
use crate::fsutil::{sha256_hex, unhex, write_atomic};

use super::spec::SKEW_MS;

pub const MOST_KEYS: usize = 8;
/// How many used nonces are remembered at once; past it nothing more is accepted until the oldest age out.
const MOST_NONCES: usize = 256;
/// The file beside `persons.json` that holds when each key that was removed was removed: a removed key is never enrolled again.
pub const REMOVED_FILE: &str = "persons_removed.json";
/// The file beside `persons.json` that holds the nonces a person's key signed that were used, with the time.
pub const NONCES_FILE: &str = "persons_nonces.json";

/// The key algorithms a person key may use: Ed25519 (a key kept beside the node, signed with a typed `yes`) and ECDSA P-256 with SHA-256
/// (a phone's Keystore key, signatures in DER).
pub const ED25519: &str = "ed25519";
pub const P256: &str = "p256";

fn ed25519() -> String {
    ED25519.into()
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Key {
    /// The public key in hex: 64 digits for Ed25519, 130 for a P-256 point (`04` and both coordinates).
    pub public: String,
    pub label: String,
    pub at_ms: u64,
    #[serde(default = "ed25519")]
    pub alg: String,
    /// Whether a phone's own enrolment put the key here: only such a key may be a strong admin key of a pool.
    #[serde(default)]
    pub phone: bool,
}

/// The enrolled keys, by fingerprint (SHA-256 of the public key), kept in one atomically replaced file.
pub struct Persons {
    path: PathBuf,
    keys: BTreeMap<String, Key>,
    /// Fingerprint to the wall time the key was removed. A removed key is not enrolled again, so nothing it signed counts, whatever
    /// time the entry claims.
    removed: BTreeMap<String, u64>,
    /// Nonces a person's key signed that were used, with the time: a signature over one is accepted once, across restarts.
    used: BTreeMap<String, u64>,
}

fn public_of(public: &str) -> Result<VerifyingKey> {
    let bytes: [u8; 32] = unhex(public).and_then(|b| b.try_into().ok()).ok_or_else(|| Error::Invalid("a person's key is a public key of 64 hex digits".into()))?;
    VerifyingKey::from_bytes(&bytes).map_err(|_| Error::Invalid("that is not a public key".into()))
}

/// Whether ``sig`` (hex) is the Ed25519 key ``public``'s signature over ``message``.
pub fn verifies(public: &str, message: &[u8], sig: &str) -> bool {
    let Ok(key) = public_of(public) else { return false };
    let Some(sig) = unhex(sig).and_then(|b| <[u8; 64]>::try_from(b).ok()) else { return false };
    key.verify_strict(message, &Signature::from_bytes(&sig)).is_ok()
}

/// The bytes of a P-256 public key given in hex: an uncompressed point of 65 bytes.
pub fn p256_point(public: &str) -> Option<Vec<u8>> {
    unhex(public).filter(|b| b.len() == 65 && b[0] == 4)
}

/// Whether ``sig`` (hex, DER) is the P-256 key ``public``'s ECDSA signature with SHA-256 over ``message``.
pub fn verifies_p256(public: &str, message: &[u8], sig: &str) -> bool {
    let (Some(point), Some(sig)) = (p256_point(public), unhex(sig)) else { return false };
    sig.len() <= 72 && ring::signature::UnparsedPublicKey::new(&ring::signature::ECDSA_P256_SHA256_ASN1, point).verify(message, &sig).is_ok()
}

/// Whether ``sig`` is the signature of the key ``public`` of algorithm ``alg`` over ``message``; an algorithm this build does not know verifies nothing.
pub fn verifies_with(alg: &str, public: &str, message: &[u8], sig: &str) -> bool {
    match alg {
        ED25519 => verifies(public, message, sig),
        P256 => verifies_p256(public, message, sig),
        _ => false,
    }
}

/// The fingerprint a key of ``alg`` is known by: SHA-256 of its public bytes.
pub fn fingerprint_of(alg: &str, public: &str) -> Result<String> {
    let bytes = match alg {
        ED25519 => public_of(public)?.as_bytes().to_vec(),
        P256 => p256_point(public).ok_or_else(|| Error::Invalid("a phone's key is an uncompressed P-256 point of 130 hex digits".into()))?,
        _ => return Err(Error::Invalid("a person's key is an Ed25519 key or a P-256 key".into())),
    };
    Ok(sha256_hex(&bytes))
}

impl Persons {
    pub fn open(path: &Path) -> Result<Persons> {
        let keys = match std::fs::read(path) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| Error::Damaged(format!("persons unreadable: {e}")))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => BTreeMap::new(),
            Err(e) => return Err(e.into()),
        };
        let removed = match std::fs::read(path.with_file_name(REMOVED_FILE)) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| Error::Damaged(format!("removed person keys unreadable: {e}")))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => BTreeMap::new(),
            Err(e) => return Err(e.into()),
        };
        let used = match std::fs::read(path.with_file_name(NONCES_FILE)) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| Error::Damaged(format!("used nonces unreadable: {e}")))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => BTreeMap::new(),
            Err(e) => return Err(e.into()),
        };
        Ok(Persons { path: path.into(), keys, removed, used })
    }

    pub fn is_empty(&self) -> bool {
        self.keys.is_empty()
    }

    pub fn all(&self) -> &BTreeMap<String, Key> {
        &self.keys
    }

    /// Whether the enrolled key ``fingerprint`` signed ``message``.
    pub fn signed(&self, fingerprint: &str, message: &[u8], sig: &str) -> bool {
        self.keys.get(fingerprint).is_some_and(|k| verifies_with(&k.alg, &k.public, message, sig))
    }

    /// Whether the key ``fingerprint`` was removed from this node.
    pub fn was_removed(&self, fingerprint: &str) -> bool {
        self.removed.contains_key(fingerprint)
    }

    /// Note that ``nonce`` was used at ``now_ms``, in a file that outlasts a restart: false when it already was within the time a
    /// signature is fresh for, or when too many are remembered to take another.
    pub fn once(&mut self, nonce: &str, now_ms: u64) -> Result<bool> {
        self.used.retain(|_, at| now_ms.abs_diff(*at) <= 2 * SKEW_MS);
        if self.used.contains_key(nonce) || self.used.len() >= MOST_NONCES {
            return Ok(false);
        }
        self.used.insert(nonce.into(), now_ms);
        write_atomic(&self.path.with_file_name(NONCES_FILE), &serde_json::to_vec(&self.used)?)?;
        Ok(true)
    }

    /// Remove the enrolled key ``fingerprint`` at ``now_ms``; the last key stays.
    pub fn remove(&mut self, fingerprint: &str, now_ms: u64) -> Result<Key> {
        if !self.keys.contains_key(fingerprint) {
            return Err(Error::Invalid("no such person key is enrolled on this node".into()));
        }
        if self.keys.len() == 1 {
            return Err(Error::Denied("the last person key stays: to start again, stop the node and delete persons.json (docs/node.md, \"Standing grants\")".into()));
        }
        let key = self.keys.remove(fingerprint).expect("held above");
        self.removed.insert(fingerprint.into(), now_ms);
        write_atomic(&self.path.with_file_name(REMOVED_FILE), &serde_json::to_vec(&self.removed)?)?;
        write_atomic(&self.path, &serde_json::to_vec(&self.keys)?)?;
        Ok(key)
    }

    /// Enrol the Ed25519 key ``public``; its fingerprint. Enrolling a key already held changes nothing, and a key that was removed is not enrolled again.
    pub fn add(&mut self, public: &str, label: &str, at_ms: u64) -> Result<String> {
        self.add_key(ED25519, public, label, at_ms)
    }

    /// Enrol the key ``public`` of algorithm ``alg`` (`ed25519` or `p256`), as `add` does.
    pub fn add_key(&mut self, alg: &str, public: &str, label: &str, at_ms: u64) -> Result<String> {
        self.enrol(alg, public, label, at_ms, false)
    }

    /// Enrol the key ``public`` of algorithm ``alg`` as a phone's. A key already held keeps how it was enrolled.
    pub fn add_phone_key(&mut self, alg: &str, public: &str, label: &str, at_ms: u64) -> Result<String> {
        self.enrol(alg, public, label, at_ms, true)
    }

    fn enrol(&mut self, alg: &str, public: &str, label: &str, at_ms: u64, phone: bool) -> Result<String> {
        let fingerprint = fingerprint_of(alg, public)?;
        if self.removed.contains_key(&fingerprint) {
            return Err(Error::Denied("that key was removed from this node and is not enrolled again: make a new key on that device".into()));
        }
        if !self.keys.contains_key(&fingerprint) {
            if self.keys.len() >= MOST_KEYS {
                return Err(crate::limits::refuse("this node holds as many person keys as it may", MOST_KEYS, "MOST_KEYS", "grants/person.rs"));
            }
            self.keys.insert(fingerprint.clone(), Key { public: public.to_ascii_lowercase(), label: label.into(), at_ms, alg: alg.into(), phone });
            write_atomic(&self.path, &serde_json::to_vec(&self.keys)?)?;
        }
        Ok(fingerprint)
    }

    /// Whether ``public`` (hex) is a key a phone enrolled on this node and that was not removed.
    pub fn is_phone_key(&self, public: &str) -> bool {
        self.keys.values().any(|k| k.phone && k.public == public)
    }

    /// The enrolled key ``fingerprint``.
    pub fn get(&self, fingerprint: &str) -> Option<&Key> {
        self.keys.get(fingerprint)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fsutil::hex;
    use ed25519_dalek::SigningKey;

    fn public(seed: u8) -> String {
        hex(SigningKey::from_bytes(&[seed; 32]).verifying_key().as_bytes())
    }

    #[test]
    fn a_used_nonce_is_remembered_across_a_reopen_and_forgotten_after_ten_minutes() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("persons.json");
        let mut persons = Persons::open(&path).unwrap();
        assert!(persons.once("0123456789abcdef", 1_000_000).unwrap());
        assert!(!persons.once("0123456789abcdef", 1_000_500).unwrap());
        let mut persons = Persons::open(&path).unwrap();
        assert!(!persons.once("0123456789abcdef", 1_100_000).unwrap(), "the node remembers it after a restart");
        assert!(persons.once("fedcba9876543210", 1_100_000).unwrap(), "another nonce is free");
        assert!(persons.once("0123456789abcdef", 1_000_000 + 2 * SKEW_MS + 1).unwrap(), "ten minutes on, a nonce is forgotten: no signature is fresh that long");
    }

    #[test]
    fn only_a_key_a_phone_enrolled_is_a_phone_key_and_enrolling_again_changes_nothing() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("persons.json");
        let mut persons = Persons::open(&path).unwrap();
        persons.add(&public(1), "typed", 1).unwrap();
        persons.add_phone_key(ED25519, &public(2), "phone", 2).unwrap();
        assert!(!persons.is_phone_key(&public(1)) && persons.is_phone_key(&public(2)) && !persons.is_phone_key(&public(3)));
        persons.add_phone_key(ED25519, &public(1), "phone", 3).unwrap();
        persons.add(&public(2), "typed", 4).unwrap();
        assert!(!persons.is_phone_key(&public(1)), "a key held as a typed yes made it does not become a phone's");
        assert!(persons.is_phone_key(&public(2)), "nor does a phone's key stop being one");
        assert!(Persons::open(&path).unwrap().is_phone_key(&public(2)), "a restart keeps it");
    }

    #[test]
    fn a_key_the_file_holds_without_the_mark_is_not_a_phone_key() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("persons.json");
        let key = format!(r#"{{"{}": {{"public": "{}", "label": "old", "at_ms": 1, "alg": "ed25519"}}}}"#, "ab".repeat(32), public(1));
        std::fs::write(&path, key).unwrap();
        assert!(!Persons::open(&path).unwrap().is_phone_key(&public(1)));
    }

    #[test]
    fn a_key_that_was_removed_is_never_enrolled_again_even_after_a_reopen() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("persons.json");
        let mut persons = Persons::open(&path).unwrap();
        let (first, second) = (persons.add(&public(1), "a", 1).unwrap(), persons.add(&public(2), "b", 2).unwrap());
        persons.remove(&second, 3).unwrap();
        assert!(persons.was_removed(&second) && !persons.was_removed(&first));
        for _ in 0..2 {
            assert!(matches!(persons.add(&public(2), "b", 4), Err(Error::Denied(_))), "a removed key is not enrolled again");
            persons = Persons::open(&path).unwrap();
        }
        assert_eq!(persons.add(&public(1), "a", 5).unwrap(), first, "a key that is held is still held");
        assert_eq!(persons.all().len(), 1);
    }
}
