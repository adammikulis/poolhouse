//! The device key: the Ed25519 key a device is known by. It signs the heads of every board's logs and the beacon, and the
//! certificate is made from it.
//!
//! Where it is kept is its custody. In the operating system's keystore (`keystore`) or in a file under a passphrase
//! (`passphrase`) the key is the Python side's (`poolhouse.node_key`): whatever starts the node releases it to the node over its
//! standard input, and the node never writes it. With neither, the key is a file only the owner can read (`file`), and the node
//! says so. A state directory that has been told its key is kept elsewhere starts only with that key.

use std::path::Path;

use ed25519_dalek::SigningKey;
use serde_json::{json, Value};

use crate::error::{Error, Result};
use crate::fsutil::{hex, random_hex, sha256_hex, unhex, write_atomic};

const FILE: &str = "device.key";
/// What a state directory remembers of a key kept elsewhere: where, and the fingerprint it must be.
const RECORD: &str = "device.custody.json";

/// Where a device's key is kept.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Custody {
    /// In the operating system's keystore, which gives it to the process that starts the node.
    Keystore,
    /// In a file encrypted under a passphrase a person gives at start.
    Passphrase,
    /// In a file only the owner can read.
    File,
}

impl Custody {
    pub fn word(self) -> &'static str {
        match self {
            Custody::Keystore => "keystore",
            Custody::Passphrase => "passphrase",
            Custody::File => "file",
        }
    }

    pub fn parse(word: &str) -> Option<Custody> {
        [Custody::Keystore, Custody::Passphrase, Custody::File].into_iter().find(|c| c.word() == word)
    }

    /// Whether anything but the keystore keeps it: a copy of the files, or of the passphrase, is a copy of the device.
    pub fn weak(self) -> bool {
        self != Custody::Keystore
    }

    /// What `pool_status` says of the key.
    pub fn status(self) -> Value {
        json!({"custody": self.word(), "weak": self.weak()})
    }
}

/// A key a node is given to run as, with where it came from (never `File`).
#[derive(Clone, Copy, Debug)]
pub struct Supplied {
    pub seed: [u8; 32],
    pub custody: Custody,
}

/// The custody a state directory recorded for its key and the fingerprint the key must have, when it recorded one.
fn recorded(dir: &Path) -> Result<Option<(Custody, String)>> {
    let bytes = match std::fs::read(dir.join(RECORD)) {
        Ok(bytes) => bytes,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e.into()),
    };
    let doc: Value = serde_json::from_slice(&bytes).map_err(|_| Error::Damaged("the record of the device key is unreadable".into()))?;
    let custody = doc["custody"].as_str().and_then(Custody::parse).filter(|c| *c != Custody::File);
    let fingerprint = doc["fingerprint"].as_str().filter(|f| f.len() == 64).map(str::to_string);
    custody.zip(fingerprint).map(Some).ok_or_else(|| Error::Damaged("the record of the device key is unreadable".into()))
}

fn plain(dir: &Path) -> Result<Option<SigningKey>> {
    match std::fs::read_to_string(dir.join(FILE)) {
        Ok(text) => {
            let bytes: [u8; 32] = unhex(text.trim()).and_then(|b| b.try_into().ok()).ok_or_else(|| Error::Damaged("device key unreadable".into()))?;
            Ok(Some(SigningKey::from_bytes(&bytes)))
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(e.into()),
    }
}

/// The key of the device whose state is in ``dir``, and where it is kept: the one ``supplied``, which is checked against what the
/// directory remembers and moves out of a key file; else the key file, made on first use.
pub fn load(dir: &Path, supplied: Option<&Supplied>) -> Result<(SigningKey, Custody)> {
    let known = recorded(dir)?;
    let Some(given) = supplied else {
        if let Some((custody, _)) = known {
            return Err(Error::Denied(format!(
                "the key of this device is kept in the {}, and the node was not given it: start the node through poolhouse", custody.word())));
        }
        return load_or_create(dir).map(|key| (key, Custody::File));
    };
    let key = SigningKey::from_bytes(&given.seed);
    let (mine, in_a_file) = (fingerprint(&key), plain(dir)?);
    if known.as_ref().is_some_and(|(_, fp)| *fp != mine) || in_a_file.as_ref().is_some_and(|f| fingerprint(f) != mine) {
        return Err(Error::Denied("the key given is not the key of the device whose state this is".into()));
    }
    if known.as_ref().map(|(c, _)| *c) != Some(given.custody) {
        write_atomic(&dir.join(RECORD), json!({"custody": given.custody.word(), "fingerprint": mine}).to_string().as_bytes())?;
    }
    if in_a_file.is_some() {
        std::fs::remove_file(dir.join(FILE))?;
    }
    Ok((key, given.custody))
}

/// Load the key file from ``dir``, creating it on first use.
pub fn load_or_create(dir: &Path) -> Result<SigningKey> {
    if let Some(key) = plain(dir)? {
        return Ok(key);
    }
    let seed = random_hex(32)?;
    write_atomic(&dir.join(FILE), seed.as_bytes())?;
    plain(dir)?.ok_or_else(|| Error::Damaged("device key unreadable".into()))
}

/// The fingerprint of a device: SHA-256 of its public key as hex.
pub fn fingerprint(key: &SigningKey) -> String {
    sha256_hex(key.verifying_key().as_bytes())
}

/// The public key as hex.
pub fn public_hex(key: &SigningKey) -> String {
    hex(key.verifying_key().as_bytes())
}
