//! Who signs for a person.
//!
//! The node never holds a person's key. An act that needs a person's signature is put to a `Signer`: the phone approval of
//! `docs/phone-approval.md` when it exists, and the typed-yes person key path outside the node, which signs an act and hands it
//! back through `pool_submit`. A rig or a test holds a key in memory (`RigSigner`); nothing in the local API or the command line
//! can make one.

use std::collections::BTreeMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

use ed25519_dalek::{Signer as _, SigningKey};
use ring::rand::SystemRandom;
use ring::signature::{EcdsaKeyPair, KeyPair, ECDSA_P256_SHA256_ASN1_SIGNING};
use serde_json::json;

use crate::error::{Error, Result};
use crate::fsutil::{hex, write_atomic};

use super::alg::Alg;
use super::keys::Strength;

/// The file a check names in the environment to have every step it asks of a person written to it, one JSON line each.
pub const LEDGER_ENV: &str = "POOLHOUSE_STEP_LEDGER";

/// Whether the device key lives where the operating system will not hand it to the process (a keystore item) or in a file.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum KeyBinding {
    File,
    Keystore,
}

/// A key a signer holds: its public key, the strength its holder declares and the person it belongs to.
#[derive(Clone, Debug)]
pub struct SignerKey {
    pub public: String,
    pub alg: Alg,
    pub strength: Strength,
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

/// The seeds from this up make P-256 keys, drawn once for the process, so a test can hold a phone-shaped admin key by its seed.
pub const P256_SEEDS: u8 = 200;

fn p256_signing(seed: u8) -> Arc<EcdsaKeyPair> {
    static DRAWN: OnceLock<Mutex<BTreeMap<u8, Arc<EcdsaKeyPair>>>> = OnceLock::new();
    let mut drawn = DRAWN.get_or_init(Default::default).lock().expect("the key table is not poisoned");
    drawn.entry(seed).or_insert_with(|| {
        let rng = SystemRandom::new();
        let pkcs8 = EcdsaKeyPair::generate_pkcs8(&ECDSA_P256_SHA256_ASN1_SIGNING, &rng).expect("the system draws a key");
        Arc::new(EcdsaKeyPair::from_pkcs8(&ECDSA_P256_SHA256_ASN1_SIGNING, pkcs8.as_ref(), &rng).expect("a drawn key is well formed"))
    }).clone()
}

enum Held {
    Ed(SigningKey),
    P256(Arc<EcdsaKeyPair>),
}

impl Held {
    fn of(seed: u8) -> Held {
        if seed >= P256_SEEDS { Held::P256(p256_signing(seed)) } else { Held::Ed(SigningKey::from_bytes(&[seed; 32])) }
    }

    fn public(&self) -> (String, Alg) {
        match self {
            Held::Ed(k) => (hex(k.verifying_key().as_bytes()), Alg::Ed25519),
            Held::P256(k) => (hex(k.public_key().as_ref()), Alg::P256Ecdsa),
        }
    }

    fn sign(&self, message: &[u8]) -> String {
        match self {
            Held::Ed(k) => hex(&k.sign(message).to_bytes()),
            Held::P256(k) => hex(k.sign(&SystemRandom::new(), message).expect("a held key signs").as_ref()),
        }
    }
}

/// A person for a rig or a test: keys held in memory that sign what they are asked to, or refuse everything while declining.
pub struct RigSigner {
    keys: Vec<(Held, SignerKey)>,
    declining: AtomicBool,
}

impl RigSigner {
    pub fn new() -> RigSigner {
        RigSigner { keys: Vec::new(), declining: AtomicBool::new(false) }
    }

    /// Add the key made from ``seed``, belonging to ``person``: Ed25519, or P-256 from `P256_SEEDS` up.
    pub fn with_key(mut self, seed: u8, person: &str, strength: Strength) -> RigSigner {
        let key = Held::of(seed);
        let (public, alg) = key.public();
        self.keys.push((key, SignerKey { public, alg, strength, person: person.into(), label: format!("{person}'s key") }));
        self
    }

    /// Whether the person says no to everything from now on.
    pub fn decline(&self, on: bool) {
        self.declining.store(on, Ordering::SeqCst);
    }

    /// The public key of the key made from ``seed``.
    pub fn public(seed: u8) -> String {
        Held::of(seed).public().0
    }

    /// The algorithm of the key made from ``seed``.
    pub fn alg(seed: u8) -> Alg {
        Held::of(seed).public().1
    }

    /// Sign ``message`` with the key made from ``seed``, as a second person who is not behind this node would.
    pub fn sign_as(seed: u8, message: &[u8]) -> String {
        Held::of(seed).sign(message)
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
        Ok(key.sign(message))
    }
}
