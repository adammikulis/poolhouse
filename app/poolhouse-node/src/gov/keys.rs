//! An admin key: a public key a charter names (Ed25519, or P-256 for a phone), with the algorithm, role and strength its holder declared and its own acceptance.
//!
//! The acceptance is the key's signature over the pool it joins, the epoch it joins at, its algorithm, role, strength and the person it
//! belongs to, so a key is listed only by its holder: adding a key needs the added key's signature as well as the admins'.

use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::error::{Error, Result};
use crate::fsutil::{sha256_hex, unhex};
use crate::row::canonical;

use super::alg::Alg;

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
    /// The public key in lower-case hex: 64 digits for Ed25519, 130 for a P-256 point.
    pub key: String,
    /// The algorithm the key signs with; it must be the one its encoding says.
    pub alg: Alg,
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

/// Whether ``text`` is the public key of an admin key of either algorithm.
pub fn valid_key(text: &str) -> bool {
    Alg::of_key(text).is_some()
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
        let body = json!({"v": 1, "key": self.key, "alg": self.alg, "role": self.role, "strength": self.strength, "person": self.person, "since": self.since, "anchor": anchor});
        format!("{ACCEPT_DOMAIN}{}", canonical(&body)).into_bytes()
    }

    /// Whether the key itself accepted this listing.
    pub fn accepted(&self, anchor: &str) -> bool {
        self.alg.verifies(&self.key, &self.acceptance_message(anchor), &self.acceptance)
    }

    /// The shape of the entry, not its signatures.
    pub fn check(&self) -> Result<()> {
        if Alg::of_key(&self.key) != Some(self.alg) {
            return Err(Error::Invalid("an admin key is an Ed25519 public key of 64 hex digits or a P-256 point of 130, and names the algorithm its encoding says".into()));
        }
        if !plain_text(&self.person, MOST_PERSON) || !self.person.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b':' | b'.')) {
            return Err(Error::Invalid("a person is named by plain letters and digits".into()));
        }
        if !self.label.is_empty() && !plain_text(&self.label, MOST_LABEL) {
            return Err(Error::Invalid("a key label is at most 40 characters with no control character".into()));
        }
        if self.since == 0 || !self.alg.sig_shape(&self.acceptance) {
            return Err(Error::Invalid("an admin key carries its own acceptance".into()));
        }
        Ok(())
    }
}
