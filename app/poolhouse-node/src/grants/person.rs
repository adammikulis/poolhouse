//! The person's keys: the Ed25519 public keys enrolled on this node. A grant counts only when one of them signed
//! it. The private halves live in the person's keystore, never here, and nothing in this module signs.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use ed25519_dalek::{Signature, VerifyingKey};
use serde::{Deserialize, Serialize};

use crate::error::{Error, Result};
use crate::fsutil::{hex, sha256_hex, unhex, write_atomic};

const MOST_KEYS: usize = 8;

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Key {
    /// The public key, 64 hex digits.
    pub public: String,
    pub label: String,
    pub at_ms: u64,
}

/// The enrolled keys, by fingerprint (SHA-256 of the public key), kept in one atomically replaced file.
pub struct Persons {
    path: PathBuf,
    keys: BTreeMap<String, Key>,
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
        Ok(Persons { path: path.into(), keys })
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
