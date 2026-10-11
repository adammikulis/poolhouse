//! An admin key: an Ed25519 public key a charter names, with the role and strength its holder declared and its own acceptance.
//!
//! The acceptance is the key's signature over the pool it joins, the epoch it joins at, its role, its strength and the person it
//! belongs to, so a key is listed only by its holder: adding a key needs the added key's signature as well as the admins'.

use ed25519_dalek::VerifyingKey;
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::error::{Error, Result};
use crate::fsutil::{sha256_hex, unhex};
use crate::grants::person::verifies;
use crate::row::canonical;

const ACCEPT_DOMAIN: &str = "poolhouse/pool-accept/v1\n";
/// The longest label and person name a key carries.
pub const MOST_LABEL: usize = 40;
const MOST_PERSON: usize = 64;

/// Whether the key's every signature needs a presence the signing process cannot supply for itself (`Strong`) or any process of the
/// account can use it (`Weak`).
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "lowercase")]
pub enum Strength {
    Strong,
    Weak,
}

/// What a key signs: `Admin` signs and counts; `Recovery` counts toward no immediate threshold.
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    Admin,
    Recovery,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct AdminKey {
    /// The public key, 64 lower-case hex digits.
    pub key: String,
    pub role: Role,
    pub strength: Strength,
    /// The person the key belongs to; two keys with one person count once under `unit: person`.
    pub person: String,
    /// The epoch this key was listed at.
    pub since: u64,
    /// The key's signature over `acceptance_message`, hex.
    pub acceptance: String,
    /// What the adder called the key: untrusted text, shown quoted and never what an approval rests on.
    pub label: String,
}

/// Whether ``text`` is the public key of an Ed25519 key, as 64 lower-case hex digits.
pub fn valid_public(text: &str) -> bool {
    text.len() == 64
        && text.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        && unhex(text).and_then(|b| <[u8; 32]>::try_from(b).ok()).is_some_and(|b| VerifyingKey::from_bytes(&b).is_ok())
}

/// The fingerprint of a public key: the SHA-256 of its bytes, hex.
pub fn fingerprint_of(public: &str) -> String {
    unhex(public).map(|b| sha256_hex(&b)).unwrap_or_default()
}

fn plain_text(text: &str, most: usize) -> bool {
    !text.is_empty() && text.chars().count() <= most && !text.chars().any(char::is_control)
}

impl AdminKey {
    pub fn fingerprint(&self) -> String {
        fingerprint_of(&self.key)
    }

    /// The bytes this key signs to accept being listed: ``anchor`` is what the epoch it joins is known by (the charter without
    /// acceptances for the first, the pool id after).
    pub fn acceptance_message(&self, anchor: &str) -> Vec<u8> {
        let body = json!({"v": 1, "key": self.key, "role": self.role, "strength": self.strength, "person": self.person, "since": self.since, "anchor": anchor});
        format!("{ACCEPT_DOMAIN}{}", canonical(&body)).into_bytes()
    }

    /// Whether the key itself accepted this listing.
    pub fn accepted(&self, anchor: &str) -> bool {
        verifies(&self.key, &self.acceptance_message(anchor), &self.acceptance)
    }

    /// The shape of the entry, not its signatures.
    pub fn check(&self) -> Result<()> {
        if !valid_public(&self.key) {
            return Err(Error::Invalid("an admin key is an Ed25519 public key of 64 hex digits".into()));
        }
        if !plain_text(&self.person, MOST_PERSON) || !self.person.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b':' | b'.')) {
            return Err(Error::Invalid("a person is named by plain letters and digits".into()));
        }
        if !self.label.is_empty() && !plain_text(&self.label, MOST_LABEL) {
            return Err(Error::Invalid("a key label is at most 40 characters with no control character".into()));
        }
        if self.since == 0 || self.acceptance.len() != 128 || unhex(&self.acceptance).is_none() {
            return Err(Error::Invalid("an admin key carries its own acceptance".into()));
        }
        Ok(())
    }
}
