//! The algorithm of an admin key: Ed25519, or ECDSA over P-256 with SHA-256 (a phone's hardware key).
//!
//! The algorithm is named by the charter entry that lists the key and by every signature, and it must match the key's encoding:
//! an Ed25519 key is 64 lower-case hex digits (32 bytes), a P-256 key is 130 (an uncompressed point, `04` and both coordinates),
//! so no key can be read under the other algorithm. A signature that names another algorithm than the one its key is listed under
//! is refused, so a signature made for one algorithm is never checked as another.

use ring::agreement::{agree_ephemeral, EphemeralPrivateKey, UnparsedPublicKey, ECDH_P256};
use ring::rand::SystemRandom;
use serde::{Deserialize, Serialize};

use crate::fsutil::unhex;
use crate::grants::person::{p256_point, verifies, verifies_p256};

/// The longest DER ECDSA signature over P-256: a sequence of two 33-byte integers.
const MOST_DER: usize = 72;
const LEAST_DER: usize = 8;

#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Alg {
    #[serde(rename = "ed25519")]
    Ed25519,
    #[serde(rename = "p256-ecdsa")]
    P256Ecdsa,
}

fn lower_hex(text: &str) -> bool {
    text.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn on_curve(point: &[u8]) -> bool {
    let Ok(own) = EphemeralPrivateKey::generate(&ECDH_P256, &SystemRandom::new()) else { return false };
    agree_ephemeral(own, &UnparsedPublicKey::new(&ECDH_P256, point), |_| ()).is_ok()
}

impl Alg {
    pub fn name(self) -> &'static str {
        match self {
            Alg::Ed25519 => "ed25519",
            Alg::P256Ecdsa => "p256-ecdsa",
        }
    }

    /// The algorithm a public key's encoding says, when it is a well formed key of one: an Ed25519 key of 64 lower-case hex digits or a
    /// P-256 point of 130 that lies on the curve.
    pub fn of_key(text: &str) -> Option<Alg> {
        if !lower_hex(text) {
            return None;
        }
        match text.len() {
            64 if unhex(text).and_then(|b| <[u8; 32]>::try_from(b).ok()).is_some_and(|b| ed25519_dalek::VerifyingKey::from_bytes(&b).is_ok()) => Some(Alg::Ed25519),
            130 if p256_point(text).is_some_and(|p| on_curve(&p)) => Some(Alg::P256Ecdsa),
            _ => None,
        }
    }

    /// Whether ``sig`` has the form a signature of this algorithm has: 128 lower-case hex digits for Ed25519, a DER sequence of at most
    /// 72 bytes for P-256.
    pub fn sig_shape(self, sig: &str) -> bool {
        if !lower_hex(sig) {
            return false;
        }
        match self {
            Alg::Ed25519 => sig.len() == 128,
            Alg::P256Ecdsa => unhex(sig).is_some_and(|b| (LEAST_DER..=MOST_DER).contains(&b.len()) && b[0] == 0x30 && usize::from(b[1]) + 2 == b.len()),
        }
    }

    /// Whether ``sig`` is the signature of the key ``public`` of this algorithm over ``message``.
    pub fn verifies(self, public: &str, message: &[u8], sig: &str) -> bool {
        Alg::of_key(public) == Some(self) && self.sig_shape(sig) && match self {
            Alg::Ed25519 => verifies(public, message, sig),
            Alg::P256Ecdsa => verifies_p256(public, message, sig),
        }
    }
}
