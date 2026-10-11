//! An act: one signed change to a pool.
//!
//! `{v, pool, epoch, kind, body, at_ms, nonce, prev, signatures}`, signed over `poolhouse/pool-act/v1`, a line break and the
//! canonical JSON of everything but the signatures. The id of an act is the hash of those same bytes, so every co-signer of one
//! act signs the same bytes and the signature sets of two copies of it merge. `prev` is the hash of the charter the signer
//! knew; `epoch` is that charter's epoch.

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::cert::{device_public, valid_fingerprint};
use crate::error::{Error, Result};
use crate::fsutil::{hex, random_hex, sha256_hex};
use crate::row::canonical;

use super::alg::Alg;
use super::{MAX_ACT_BYTES, MAX_SIGNATURES};

const DOMAIN: &str = "poolhouse/pool-act/v1\n";

/// Every kind of act this build knows.
pub const KINDS: [&str; 13] = [
    "found", "admit", "remove", "leave", "admin_add", "admin_remove", "veto", "cancel", "set_threshold", "set_holds", "set_mode", "delegate", "delegate_end",
];
/// The kinds that change the admin set, a threshold, a hold or the mode: they form one chain, an epoch each.
pub const CHAIN: [&str; 5] = ["admin_add", "admin_remove", "set_threshold", "set_holds", "set_mode"];

/// Who signed: an admin key (by its public key) or a device (by the fingerprint of its certificate).
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "lowercase")]
pub enum SigKind {
    Admin,
    Device,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
#[serde(deny_unknown_fields)]
pub struct ActSig {
    pub kind: SigKind,
    pub key: String,
    /// The algorithm of the key; a device signs with Ed25519, an admin key with the one its charter entry names.
    pub alg: Alg,
    pub sig: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Act {
    pub v: u32,
    pub pool: String,
    pub epoch: u64,
    pub kind: String,
    pub body: Value,
    pub at_ms: u64,
    pub nonce: String,
    pub prev: String,
    pub signatures: Vec<ActSig>,
}

fn bad<T>(why: &str) -> Result<T> {
    Err(Error::Invalid(why.into()))
}

fn is_hex(text: &str, len: usize) -> bool {
    text.len() == len && text.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Whether ``id`` is the id of an act: 64 hex digits.
pub fn valid_id(id: &str) -> bool {
    is_hex(id, 64)
}

impl Act {
    /// An act with no signature yet, made at ``at_ms`` for the charter ``prev`` of epoch ``epoch``.
    pub fn new(pool: &str, epoch: u64, kind: &str, body: Value, at_ms: u64, prev: &str) -> Result<Act> {
        Ok(Act { v: 1, pool: pool.into(), epoch, kind: kind.into(), body, at_ms, nonce: random_hex(16)?, prev: prev.into(), signatures: Vec::new() })
    }

    /// The act without its signatures, as the JSON that is signed.
    pub fn unsigned(&self) -> Value {
        let mut doc = serde_json::to_value(self).unwrap_or(Value::Null);
        if let Value::Object(map) = &mut doc {
            map.remove("signatures");
        }
        doc
    }

    /// The id of the act, which every signature set of it shares.
    pub fn id(&self) -> String {
        sha256_hex(canonical(&self.unsigned()).as_bytes())
    }

    /// The bytes each signer signs.
    pub fn sign_bytes(&self) -> Vec<u8> {
        format!("{DOMAIN}{}", canonical(&self.unsigned())).into_bytes()
    }

    pub fn is_chain(&self) -> bool {
        CHAIN.contains(&self.kind.as_str())
    }

    /// The shape of the act: version, kind, sizes and the form of every field. Whether it is authorised is for the fold.
    pub fn check_shape(&self) -> Result<()> {
        if self.v != 1 || !KINDS.contains(&self.kind.as_str()) {
            return bad("this build does not know that kind of act");
        }
        if !is_hex(&self.pool, 32) || !is_hex(&self.nonce, 32) || !(self.prev.is_empty() || valid_id(&self.prev)) || self.epoch == 0 || self.at_ms == 0 {
            return bad("an act names its pool, epoch, time, nonce and the charter it follows");
        }
        if self.signatures.len() > MAX_SIGNATURES || serde_json::to_vec(self).map_or(usize::MAX, |b| b.len()) > MAX_ACT_BYTES {
            return bad("an act is at most 16 KiB with at most eight signatures");
        }
        let mut signers = std::collections::BTreeSet::new();
        for s in &self.signatures {
            if !signers.insert((s.kind, s.key.as_str())) {
                return bad("an act is signed once by each signer");
            }
            let key_ok = match s.kind {
                SigKind::Admin => Alg::of_key(&s.key) == Some(s.alg),
                SigKind::Device => valid_fingerprint(&s.key) && s.alg == Alg::Ed25519,
            };
            if !key_ok || !s.alg.sig_shape(&s.sig) {
                return bad("a signature names its key and its algorithm, and is that algorithm's signature");
            }
        }
        Ok(())
    }

    /// Whether ``sig`` is a signature of these bytes: an admin by the key it names, a device by the certificate ``der`` it names.
    pub fn verifies(&self, sig: &ActSig, der: Option<&[u8]>) -> bool {
        let public = match (sig.kind, der) {
            (SigKind::Admin, _) => sig.key.clone(),
            (SigKind::Device, Some(der)) if sig.alg == Alg::Ed25519 => match device_public(der) {
                Ok(p) if crate::cert::cert_fingerprint(der) == sig.key => hex(&p),
                _ => return false,
            },
            (SigKind::Device, _) => return false,
        };
        sig.alg.verifies(&public, &self.sign_bytes(), &sig.sig)
    }

    /// Add ``sig`` if this act has no signature by that key yet; whether it was added.
    pub fn add_signature(&mut self, sig: ActSig) -> bool {
        if self.signatures.iter().any(|s| s.kind == sig.kind && s.key == sig.key) || self.signatures.len() >= MAX_SIGNATURES {
            return false;
        }
        self.signatures.push(sig);
        self.signatures.sort();
        true
    }

    /// The public keys of the admin signatures.
    pub fn admin_keys(&self) -> Vec<&str> {
        self.signatures.iter().filter(|s| s.kind == SigKind::Admin).map(|s| s.key.as_str()).collect()
    }

    /// The fingerprints of the device signatures.
    pub fn device_keys(&self) -> Vec<&str> {
        self.signatures.iter().filter(|s| s.kind == SigKind::Device).map(|s| s.key.as_str()).collect()
    }
}
