//! Who signs for a person.
//!
//! The node never holds a person's key. An act that needs a person's signature is put to a `Signer`: the phone approval of
//! `docs/phone-approval.md` when it exists, and the typed-yes person key path outside the node, which signs an act and hands it
//! back through `pool_submit`. A rig or a test holds a key in memory (`RigSigner`); nothing in the local API or the command line
//! can make one.

use std::sync::atomic::{AtomicBool, Ordering};

use ed25519_dalek::{Signer as _, SigningKey};
use serde_json::json;

use crate::error::{Error, Result};
use crate::fsutil::{hex, write_atomic};

/// The file a check names in the environment to have every step it asks of a person written to it, one JSON line each.
pub const LEDGER_ENV: &str = "POOLHOUSE_STEP_LEDGER";

/// Whether the device key lives where the operating system will not hand it to the process (a keystore item) or in a file.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum KeyBinding {
    File,
    Keystore,
}

/// A key a signer holds: its public key and the person it belongs to.
#[derive(Clone, Debug)]
pub struct SignerKey {
    pub public: String,
    pub person: String,
    pub label: String,
}

pub trait Signer: Send + Sync {
    /// The keys this signer holds.
    fn keys(&self) -> Vec<SignerKey>;

    /// The signature, as hex, of the key ``public`` over ``message``, after ``summary`` was put to the person.
    fn sign(&self, public: &str, message: &[u8], summary: &str) -> Result<String>;
}

/// Write one step the product asked of a person to the ledger a check named, and do nothing when none is named.
pub fn ledger_step(journey: &str, kind: &str) {
    let Some(path) = std::env::var_os(LEDGER_ENV) else { return };
    let line = json!({"journey": journey, "kind": kind}).to_string();
    let mut text = std::fs::read_to_string(&path).unwrap_or_default();
    text.push_str(&line);
    text.push('\n');
    let _ = write_atomic(std::path::Path::new(&path), text.as_bytes());
}

/// A person for a rig or a test: keys held in memory that sign what they are asked to, or refuse everything while declining.
pub struct RigSigner {
    keys: Vec<(SigningKey, SignerKey)>,
    declining: AtomicBool,
}

impl RigSigner {
    pub fn new() -> RigSigner {
        RigSigner { keys: Vec::new(), declining: AtomicBool::new(false) }
    }

    /// Add the key made from ``seed``, belonging to ``person``.
    pub fn with_key(mut self, seed: u8, person: &str) -> RigSigner {
        let key = SigningKey::from_bytes(&[seed; 32]);
        let public = hex(key.verifying_key().as_bytes());
        self.keys.push((key, SignerKey { public, person: person.into(), label: format!("{person}'s key") }));
        self
    }

    /// Whether the person says no to everything from now on.
    pub fn decline(&self, on: bool) {
        self.declining.store(on, Ordering::SeqCst);
    }

    /// The public key of the key made from ``seed``.
    pub fn public(seed: u8) -> String {
        hex(SigningKey::from_bytes(&[seed; 32]).verifying_key().as_bytes())
    }

    /// Sign ``message`` with the key made from ``seed``, as a second person who is not behind this node would.
    pub fn sign_as(seed: u8, message: &[u8]) -> String {
        hex(&SigningKey::from_bytes(&[seed; 32]).sign(message).to_bytes())
    }
}

impl Default for RigSigner {
    fn default() -> RigSigner {
        RigSigner::new()
    }
}

impl Signer for RigSigner {
    fn keys(&self) -> Vec<SignerKey> {
        self.keys.iter().map(|(_, k)| k.clone()).collect()
    }

    fn sign(&self, public: &str, message: &[u8], _summary: &str) -> Result<String> {
        if self.declining.load(Ordering::SeqCst) {
            return Err(Error::say("denied", "Your phone said no, so nothing was signed.", "approval", "stop"));
        }
        let (key, _) = self.keys.iter().find(|(_, k)| k.public == public).ok_or_else(|| Error::Invalid("this signer does not hold that key".into()))?;
        ledger_step("rig", "tap");
        Ok(hex(&key.sign(message).to_bytes()))
    }
}
