//! The person's keys: the Ed25519 public keys enrolled on this node. A grant counts only when one of them signed
//! it. The private halves live in the person's keystore, never here, and nothing in this module signs.
//!
//! The first key is enrolled with a one-time secret: a person's command at a terminal writes the secret's hash into
//! the state directory (`SECRET_FILE`) and presents the secret to `person_enroll`. The node reads the hash from that
//! file only, so no request can set one.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use ed25519_dalek::{Signature, VerifyingKey};
use serde::{Deserialize, Serialize};

use crate::error::{Error, Result};
use crate::fsutil::{hex, sha256_hex, unhex, write_atomic};

use super::spec::SKEW_MS;

const MOST_KEYS: usize = 8;
/// How many used nonces are remembered at once; past it nothing more is accepted until the oldest age out.
const MOST_NONCES: usize = 256;
/// The file beside `persons.json` that holds the hash of the secret the first key is enrolled with.
pub const SECRET_FILE: &str = "enroll_secret.json";
/// How long the secret a person's command made is good for.
pub const SECRET_TTL_MS: u64 = 10 * 60 * 1000;

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Key {
    /// The public key, 64 hex digits.
    pub public: String,
    pub label: String,
    pub at_ms: u64,
}

#[derive(Deserialize)]
struct Secret {
    sha256: String,
    at_ms: u64,
}

/// The enrolled keys, by fingerprint (SHA-256 of the public key), kept in one atomically replaced file.
pub struct Persons {
    path: PathBuf,
    keys: BTreeMap<String, Key>,
    /// Nonces a person's key signed that were used, with the time: a signature over one is accepted once.
    used: BTreeMap<String, u64>,
}

fn same(a: &str, b: &str) -> bool {
    a.len() == b.len() && a.bytes().zip(b.bytes()).fold(0u8, |d, (x, y)| d | (x ^ y)) == 0
}

fn public_of(public: &str) -> Result<VerifyingKey> {
    let bytes: [u8; 32] = unhex(public).and_then(|b| b.try_into().ok()).ok_or_else(|| Error::Invalid("a person's key is a public key of 64 hex digits".into()))?;
    VerifyingKey::from_bytes(&bytes).map_err(|_| Error::Invalid("that is not a public key".into()))
}

/// Whether ``sig`` (hex) is ``public``'s signature over ``message``.
pub fn verifies(public: &str, message: &[u8], sig: &str) -> bool {
    let Ok(key) = public_of(public) else { return false };
    let Some(sig) = unhex(sig).and_then(|b| <[u8; 64]>::try_from(b).ok()) else { return false };
    key.verify_strict(message, &Signature::from_bytes(&sig)).is_ok()
}

impl Persons {
    pub fn open(path: &Path) -> Result<Persons> {
        let keys = match std::fs::read(path) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| Error::Damaged(format!("persons unreadable: {e}")))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => BTreeMap::new(),
            Err(e) => return Err(e.into()),
        };
        Ok(Persons { path: path.into(), keys, used: BTreeMap::new() })
    }

    pub fn is_empty(&self) -> bool {
        self.keys.is_empty()
    }

    pub fn all(&self) -> &BTreeMap<String, Key> {
        &self.keys
    }

    /// Whether the enrolled key ``fingerprint`` signed ``message``.
    pub fn signed(&self, fingerprint: &str, message: &[u8], sig: &str) -> bool {
        self.keys.get(fingerprint).is_some_and(|k| verifies(&k.public, message, sig))
    }

    /// Whether ``secret`` is the one a person's command left for the first enrolment, fresh at ``now_ms``. The file is
    /// removed whatever the answer, so one guess is all a secret allows.
    pub fn take_secret(&self, secret: &str, now_ms: u64) -> bool {
        let file = self.path.with_file_name(SECRET_FILE);
        let found = std::fs::read(&file).ok().and_then(|bytes| serde_json::from_slice::<Secret>(&bytes).ok());
        let _ = std::fs::remove_file(&file);
        found.is_some_and(|s| !secret.is_empty() && now_ms >= s.at_ms && now_ms - s.at_ms <= SECRET_TTL_MS && same(&s.sha256, &sha256_hex(secret.as_bytes())))
    }

    /// Note that ``nonce`` was used at ``now_ms``: false when it already was within the time a signature is fresh for, or
    /// when too many are remembered to take another.
    pub fn once(&mut self, nonce: &str, now_ms: u64) -> bool {
        self.used.retain(|_, at| now_ms.abs_diff(*at) <= 2 * SKEW_MS);
        if self.used.contains_key(nonce) || self.used.len() >= MOST_NONCES {
            return false;
        }
        self.used.insert(nonce.into(), now_ms);
        true
    }

    /// Remove the enrolled key ``fingerprint``; the last key stays.
    pub fn remove(&mut self, fingerprint: &str) -> Result<Key> {
        if !self.keys.contains_key(fingerprint) {
            return Err(Error::Invalid("no such person key is enrolled on this node".into()));
        }
        if self.keys.len() == 1 {
            return Err(Error::Denied("the last person key stays: to start again, stop the node and delete persons.json (docs/node.md, \"Standing grants\")".into()));
        }
        let key = self.keys.remove(fingerprint).expect("held above");
        write_atomic(&self.path, &serde_json::to_vec(&self.keys)?)?;
        Ok(key)
    }

    /// Enrol ``public``; its fingerprint. Enrolling a key already held changes nothing.
    pub fn add(&mut self, public: &str, label: &str, at_ms: u64) -> Result<String> {
        let public = public_of(public)?;
        let fingerprint = sha256_hex(public.as_bytes());
        if !self.keys.contains_key(&fingerprint) {
            if self.keys.len() >= MOST_KEYS {
                return Err(Error::Quota("this node holds as many person keys as it will".into()));
            }
            self.keys.insert(fingerprint.clone(), Key { public: hex(public.as_bytes()), label: label.into(), at_ms });
            write_atomic(&self.path, &serde_json::to_vec(&self.keys)?)?;
        }
        Ok(fingerprint)
    }
}
