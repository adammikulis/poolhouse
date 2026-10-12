//! The algorithm of an admin key: Ed25519, or ECDSA over P-256 with SHA-256 (a phone's hardware key).
//!
//! The algorithm is named by the charter entry that lists the key and by every signature, and it must match the key's encoding:
//! an Ed25519 key is 64 lower-case hex digits (32 bytes), a P-256 key is 130 (an uncompressed point, `04` and both coordinates),
//! so no key can be read under the other algorithm. A signature that names another algorithm than the one its key is listed under
//! is refused, so a signature made for one algorithm is never checked as another.

use ring::agreement::{agree_ephemeral, EphemeralPrivateKey, UnparsedPublicKey, ECDH_P256};
use ring::rand::SystemRandom;
use serde::{Deserialize, Serialize};

use crate::fsutil::{hex, unhex};
use crate::grants::person::{p256_point, verifies, verifies_p256};

/// The longest DER ECDSA signature over P-256: a sequence of two 33-byte integers.
const MOST_DER: usize = 72;
const LEAST_DER: usize = 8;
/// The order of the P-256 group, big-endian: a signature (r, s) and (r, order - s) both verify, so only the one with the smaller s is a signature here.
const ORDER: &str = "FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551";

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

fn order() -> [u8; 32] {
    unhex(ORDER).and_then(|b| <[u8; 32]>::try_from(b).ok()).expect("the group order is 32 bytes")
}

fn mirror(s: &[u8; 32]) -> [u8; 32] {
    let (n, mut out, mut borrow) = (order(), [0u8; 32], 0i16);
    for i in (0..32).rev() {
        let d = i16::from(n[i]) - i16::from(s[i]) - borrow;
        borrow = i16::from(d < 0);
        out[i] = u8::try_from(d.rem_euclid(256)).expect("a byte");
    }
    out
}

/// A DER integer at ``at`` in ``b``, minimal, positive and not zero, as 32 big-endian bytes, and where the next item starts.
fn integer(b: &[u8], at: usize) -> Option<([u8; 32], usize)> {
    let len = usize::from(*b.get(at + 1)?);
    let body = b.get(at + 2..at + 2 + len)?;
    if *b.get(at)? != 2 || len == 0 || len > 33 || body[0] & 0x80 != 0 || (len > 1 && body[0] == 0 && body[1] & 0x80 == 0) {
        return None;
    }
    let value = if body[0] == 0 { &body[1..] } else { body };
    let mut out = [0u8; 32];
    out.get_mut(32usize.checked_sub(value.len())?..)?.copy_from_slice(value);
    (out != [0u8; 32]).then_some((out, at + 2 + len))
}

fn der(r: &[u8; 32], s: &[u8; 32]) -> Vec<u8> {
    let int = |v: &[u8; 32]| {
        let mut bytes = v[v.iter().position(|b| *b != 0).unwrap_or(31)..].to_vec();
        if bytes[0] & 0x80 != 0 {
            bytes.insert(0, 0);
        }
        [vec![2, u8::try_from(bytes.len()).expect("at most 33")], bytes].concat()
    };
    let body = [int(r), int(s)].concat();
    [vec![0x30, u8::try_from(body.len()).expect("at most 70")], body].concat()
}

/// The (r, s) of a strict DER ECDSA signature: a sequence of two minimal positive integers and nothing after them.
fn parts(sig: &str) -> Option<([u8; 32], [u8; 32])> {
    let b = unhex(sig)?;
    if !(LEAST_DER..=MOST_DER).contains(&b.len()) || b[0] != 0x30 || usize::from(b[1]) + 2 != b.len() {
        return None;
    }
    let (r, next) = integer(&b, 2)?;
    let (s, end) = integer(&b, next)?;
    (end == b.len()).then_some((r, s))
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

    /// Whether ``sig`` has the form a signature of this algorithm has: 128 lower-case hex digits for Ed25519; for P-256 the one spelling
    /// there is, strict minimal DER with the smaller of the two s values that verify.
    pub fn sig_shape(self, sig: &str) -> bool {
        if !lower_hex(sig) {
            return false;
        }
        match self {
            Alg::Ed25519 => sig.len() == 128,
            Alg::P256Ecdsa => Alg::canonical_p256(sig).as_deref() == Some(sig),
        }
    }

    /// The same P-256 signature in its one spelling: strict DER with the smaller s. None when ``sig`` is not a strict DER signature at all.
    pub fn canonical_p256(sig: &str) -> Option<String> {
        let (r, s) = parts(sig)?;
        let low = mirror(&s);
        Some(hex(&der(&r, if low < s { &low } else { &s })))
    }

    /// The other signature that verifies over the same message as the P-256 signature ``sig``: its s replaced by the order less s.
    pub fn mirrored_p256(sig: &str) -> Option<String> {
        let (r, s) = parts(sig)?;
        Some(hex(&der(&r, &mirror(&s))))
    }

    /// Whether ``sig`` is the signature of the key ``public`` of this algorithm over ``message``.
    pub fn verifies(self, public: &str, message: &[u8], sig: &str) -> bool {
        Alg::of_key(public) == Some(self) && self.sig_shape(sig) && match self {
            Alg::Ed25519 => verifies(public, message, sig),
            Alg::P256Ecdsa => verifies_p256(public, message, sig),
        }
    }
}
