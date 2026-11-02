//! An admin key: a public key a charter names (an Ed25519 key, or a phone's P-256 key), with the role and the claim of how it is held
//! that its holder declared and its own acceptance.
//!
//! The acceptance is the key's signature over the pool it joins, the charter it follows, its algorithm, role, claim and the person it
//! belongs to, so a key is listed only by its holder: adding a key needs the added key's signature as well as the admins'. The charter
//! stores the claim and never a level: every member computes the level a key counts at from the claim (`Claim::counted`).

use std::collections::BTreeMap;
use std::sync::Mutex;

use ed25519_dalek::VerifyingKey;
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::error::{Error, Result};
use crate::fsutil::{sha256_hex, unhex};
use crate::grants::person::{self, ED25519, P256};
use crate::row::canonical;

const ACCEPT_DOMAIN: &str = "poolhouse/pool-accept/v3\n";
/// The longest label and person name a key carries.
pub const MOST_LABEL: usize = 40;
const MOST_PERSON: usize = 64;
/// How many verdicts of `accepted_memo` are kept.
const MEMO_MOST: usize = 1024;

/// How much stands between a program of the account and a key's signature, lowest first: `Weak` (a typed yes or a file: a process of the
/// account can use the key), `Computer` (the computer's own prompt raised the key, or a phone or protected helper whose claim no member
/// can check), `Native` and `Phone` (levels a member could count only with evidence the pool does not have, so no listed key holds either
/// yet). A charter's floors name the lowest level that counts for a class of act; `Weak` is below every floor.
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
#[serde(rename_all = "lowercase")]
pub enum Level {
    Weak,
    Computer,
    Native,
    Phone,
}

impl Level {
    /// The level as the charter and `pool_status` spell it.
    pub fn name(self) -> &'static str {
        match self {
            Level::Weak => "weak",
            Level::Computer => "computer",
            Level::Native => "native",
            Level::Phone => "phone",
        }
    }

    /// The level in the words a person is shown.
    pub fn shown(self) -> &'static str {
        match self {
            Level::Weak => "a typed yes",
            Level::Computer => "this computer",
            Level::Native => "this computer, protected",
            Level::Phone => "your phone",
        }
    }

    /// How a change is approved at this level, as a plain sentence for the card, the level line and a device about to join: what approves it
    /// and what that does not stop.
    pub fn approval(self) -> &'static str {
        match self {
            Level::Weak => "approved by a typed yes; a program running as you could approve it",
            Level::Computer => "approved from this computer; a program running as you could approve it without asking",
            Level::Native => "approved from this computer, protected",
            Level::Phone => "approved from your phone",
        }
    }
}

/// How a key is held, in the closed vocabulary of the keys document (3.1).
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
#[serde(rename_all = "kebab-case")]
pub enum Method {
    /// A phone's hardware keystore key, authentication for each use.
    PhoneKeystore,
    /// A protected helper under another operating-system identity.
    Helper,
    /// A key sealed under the keystore, made through the operating system's own prompt for this key.
    KeystorePrompt,
    /// A key sealed under the keystore, made by a typed yes.
    KeystoreTyped,
    /// A passphrase file or a plain file.
    File,
}

/// What each use of the key asks the person for.
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
#[serde(rename_all = "kebab-case")]
pub enum Prompt {
    Biometric,
    OsDialog,
    TypedYes,
    None,
}

/// The operating system of the device that vouches for the key, for words.
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
#[serde(rename_all = "lowercase")]
pub enum Os {
    Android,
    Macos,
    Linux,
    Windows,
    Other,
}

impl Os {
    /// The operating system this node runs on.
    pub fn here() -> Os {
        if cfg!(target_os = "macos") {
            Os::Macos
        } else if cfg!(target_os = "linux") {
            Os::Linux
        } else if cfg!(windows) {
            Os::Windows
        } else {
            Os::Other
        }
    }
}

/// A claim of how a key is held. The level it counts at is computed from it and never stored or named by a caller.
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq, Hash)]
#[serde(deny_unknown_fields)]
pub struct Claim {
    pub method: Method,
    pub os: Os,
    pub prompt: Prompt,
}

impl Claim {
    /// The claim of a key made through the operating system's own prompt on this computer.
    pub fn keystore_prompt() -> Claim {
        Claim { method: Method::KeystorePrompt, os: Os::here(), prompt: Prompt::OsDialog }
    }

    /// The claim of a key a typed yes made on this computer.
    pub fn keystore_typed() -> Claim {
        Claim { method: Method::KeystoreTyped, os: Os::here(), prompt: Prompt::TypedYes }
    }

    /// The claim of a key that sits in a file beside the node.
    pub fn file() -> Claim {
        Claim { method: Method::File, os: Os::here(), prompt: Prompt::TypedYes }
    }

    /// The claim of a phone's own key.
    pub fn phone_keystore() -> Claim {
        Claim { method: Method::PhoneKeystore, os: Os::Android, prompt: Prompt::Biometric }
    }

    /// Whether the prompt fits the method: a claim whose prompt does not is no claim.
    pub fn fits(&self) -> bool {
        match self.method {
            Method::PhoneKeystore => self.prompt == Prompt::Biometric,
            Method::Helper | Method::KeystorePrompt => matches!(self.prompt, Prompt::OsDialog | Prompt::Biometric),
            Method::KeystoreTyped => self.prompt == Prompt::TypedYes,
            Method::File => matches!(self.prompt, Prompt::TypedYes | Prompt::None),
        }
    }

    /// The level the words of the claim name, which no member can check beyond `Computer`.
    pub fn level_of(&self) -> Level {
        match self.method {
            Method::PhoneKeystore => Level::Phone,
            Method::Helper => Level::Native,
            Method::KeystorePrompt => Level::Computer,
            Method::KeystoreTyped | Method::File => Level::Weak,
        }
    }

    /// The level the key counts at: the claim's, and no more than `Computer`, because the claim is the vouching device's word and no
    /// member can verify a phone's attestation or a helper's identity (keys document C1, C6).
    pub fn counted(&self) -> Level {
        self.level_of().min(Level::Computer)
    }

    /// The claim in the words a person reads on a card: what the key is, and what nobody else can check about it.
    pub fn shown(&self) -> &'static str {
        match self.method {
            Method::PhoneKeystore => "your phone, as it says; this pool cannot check it",
            Method::Helper => "this computer, protected, as it says; this pool cannot check it",
            Method::KeystorePrompt => "this computer, as it says; nothing else can check it",
            Method::KeystoreTyped | Method::File => "a typed yes or a file; it counts for nothing",
        }
    }
}

/// What a key signs: `Admin` signs and counts; `Recovery` counts toward no immediate threshold.
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    Admin,
    Recovery,
}

/// The signature algorithm of an admin key: Ed25519 (64 hex digits of public key, 128 of signature) or ECDSA P-256 with SHA-256 (a
/// phone's Keystore key: 130 hex digits of public key, a canonical DER signature of at most 144).
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Alg {
    #[serde(rename = "ed25519")]
    Ed25519,
    #[serde(rename = "p256")]
    P256,
}

impl Alg {
    /// The algorithm of the public key ``text``, as 64 or 130 lower-case hex digits that make a key; none for anything else.
    pub fn of_public(text: &str) -> Option<Alg> {
        if !text.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
            return None;
        }
        match text.len() {
            64 => unhex(text).and_then(|b| <[u8; 32]>::try_from(b).ok()).filter(|b| VerifyingKey::from_bytes(b).is_ok()).map(|_| Alg::Ed25519),
            130 => person::p256_point(text).map(|_| Alg::P256),
            _ => None,
        }
    }

    /// The name this algorithm has in a person's key list.
    pub fn name(self) -> &'static str {
        match self {
            Alg::Ed25519 => ED25519,
            Alg::P256 => P256,
        }
    }

    /// Whether ``sig`` has the form of a signature of this algorithm: 64 bytes, or a canonical DER signature of a P-256 key.
    pub fn sig_shape(self, sig: &str) -> bool {
        let Some(bytes) = unhex(sig) else { return false };
        match self {
            Alg::Ed25519 => bytes.len() == 64,
            Alg::P256 => poolhouse_authority::phone_candidate::canonical_p256(&bytes),
        }
    }

    /// Whether ``sig`` is a canonical signature of ``message`` by the key ``public`` of this algorithm.
    pub fn verifies(self, public: &str, message: &[u8], sig: &str) -> bool {
        self.sig_shape(sig) && Alg::of_public(public) == Some(self) && person::verifies_with(self.name(), public, message, sig)
    }
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct AdminKey {
    /// The public key, lower-case hex.
    pub key: String,
    pub alg: Alg,
    pub role: Role,
    /// How the key is held: stated for an admin key, absent for a recovery key (which counts for nothing). The level it counts at is
    /// computed from it (`AdminKey::level`).
    pub claim: Option<Claim>,
    /// The person the key belongs to; two keys with one person count once under `unit: person`.
    pub person: String,
    /// The epoch this key was listed at.
    pub since: u64,
    /// The key's signature over `acceptance_message`, hex.
    pub acceptance: String,
    /// What the adder called the key: untrusted text, shown quoted and never what an approval rests on.
    pub label: String,
}

/// Whether ``text`` is the public key of an admin key, as lower-case hex of an Ed25519 or a P-256 key.
pub fn valid_public(text: &str) -> bool {
    Alg::of_public(text).is_some()
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

    /// The level this key counts at, computed from its claim: `Weak` for a key that states none.
    pub fn level(&self) -> Level {
        self.claim.map_or(Level::Weak, |c| c.counted())
    }

    /// The bytes this key signs to accept being listed: ``anchor`` is what the pool it joins is known by (the charter without
    /// acceptances for the first epoch, the pool id after) and ``prev`` the hash of the charter the listing follows (empty for the first).
    pub fn acceptance_message(&self, anchor: &str, prev: &str) -> Vec<u8> {
        let body = json!({
            "v": 3, "key": self.key, "alg": self.alg, "role": self.role, "claim": self.claim, "person": self.person, "since": self.since,
            "anchor": anchor, "prev": prev,
        });
        format!("{ACCEPT_DOMAIN}{}", canonical(&body)).into_bytes()
    }

    /// Whether the key itself accepted this listing.
    pub fn accepted(&self, anchor: &str, prev: &str) -> bool {
        self.alg.verifies(&self.key, &self.acceptance_message(anchor, prev), &self.acceptance)
    }

    /// `accepted`, remembering the verdicts: a verification is a pure function of the key, its acceptance and what it accepts.
    pub fn accepted_memo(&self, anchor: &str, prev: &str) -> bool {
        static MEMO: Mutex<BTreeMap<String, bool>> = Mutex::new(BTreeMap::new());
        let id = sha256_hex(&self.acceptance_message(anchor, prev).into_iter().chain(self.acceptance.bytes()).collect::<Vec<u8>>());
        if let Some(verdict) = MEMO.lock().ok().and_then(|m| m.get(&id).copied()) {
            return verdict;
        }
        let verdict = self.accepted(anchor, prev);
        if let Ok(mut memo) = MEMO.lock() {
            if memo.len() >= MEMO_MOST {
                memo.clear();
            }
            memo.insert(id, verdict);
        }
        verdict
    }

    /// The shape of the entry, not its signatures.
    pub fn check(&self) -> Result<()> {
        if Alg::of_public(&self.key) != Some(self.alg) {
            return Err(Error::Invalid("an admin key is an Ed25519 key of 64 hex digits or a P-256 key of 130, and says which".into()));
        }
        if !plain_text(&self.person, MOST_PERSON) || !self.person.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b':' | b'.')) {
            return Err(Error::Invalid("a person is named by plain letters and digits".into()));
        }
        if !self.label.is_empty() && !plain_text(&self.label, MOST_LABEL) {
            return Err(Error::Invalid("a key label is at most 40 characters with no control character".into()));
        }
        match (self.role, &self.claim) {
            (Role::Admin, Some(claim)) if claim.fits() => {}
            (Role::Admin, _) => return Err(Error::Invalid("an admin key states how it is held, in the words of the claim vocabulary".into())),
            (Role::Recovery, None) => {}
            (Role::Recovery, Some(_)) => return Err(Error::Invalid("a recovery key states no claim: it counts for nothing".into())),
        }
        if self.since == 0 || !self.alg.sig_shape(&self.acceptance) {
            return Err(Error::Invalid("an admin key carries its own acceptance".into()));
        }
        Ok(())
    }
}
