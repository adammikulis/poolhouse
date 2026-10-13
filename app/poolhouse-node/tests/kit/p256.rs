//! A P-256 key for the charter and act tests: it signs as a phone must (strict DER, `s` in the lower half of the group) or, on
//! request, the high-`s` twin of the same signature that is as valid to the curve and that a pool refuses.

use poolhouse_node::fsutil::hex;
use ring::rand::SystemRandom;
use ring::signature::{EcdsaKeyPair, KeyPair, ECDSA_P256_SHA256_FIXED_SIGNING};

/// The order of the P-256 group.
const ORDER: [u8; 32] = [
    0xff, 0xff, 0xff, 0xff, 0x00, 0x00, 0x00, 0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xbc, 0xe6, 0xfa, 0xad, 0xa7, 0x17, 0x9e, 0x84, 0xf3, 0xb9, 0xca, 0xc2, 0xfc, 0x63,
    0x25, 0x51,
];

pub struct PhoneKey {
    signing: EcdsaKeyPair,
    /// The uncompressed point, 130 hex digits.
    pub public: String,
}

/// ``order - s`` as 32 big-endian bytes.
fn negate(s: &[u8; 32]) -> [u8; 32] {
    let (mut out, mut borrow) = ([0u8; 32], 0i16);
    for i in (0..32).rev() {
        let d = i16::from(ORDER[i]) - i16::from(s[i]) - borrow;
        out[i] = (d & 0xff) as u8;
        borrow = i16::from(d < 0);
    }
    out
}

fn der_integer(value: &[u8; 32]) -> Vec<u8> {
    let digits: Vec<u8> = value.iter().copied().skip_while(|b| *b == 0).collect();
    let mut body = if digits.first().is_some_and(|b| b & 0x80 != 0) { vec![0] } else { Vec::new() };
    body.extend(digits);
    [vec![0x02, body.len() as u8], body].concat()
}

/// The DER form of the signature ``r`` and ``s``.
pub fn der(r: &[u8; 32], s: &[u8; 32]) -> Vec<u8> {
    let body = [der_integer(r), der_integer(s)].concat();
    [vec![0x30, body.len() as u8], body].concat()
}

impl PhoneKey {
    pub fn new() -> PhoneKey {
        let rng = SystemRandom::new();
        let pkcs8 = EcdsaKeyPair::generate_pkcs8(&ECDSA_P256_SHA256_FIXED_SIGNING, &rng).unwrap();
        let signing = EcdsaKeyPair::from_pkcs8(&ECDSA_P256_SHA256_FIXED_SIGNING, pkcs8.as_ref(), &rng).unwrap();
        let public = hex(signing.public_key().as_ref());
        PhoneKey { signing, public }
    }

    fn scalars(&self, message: &[u8]) -> ([u8; 32], [u8; 32]) {
        let raw = self.signing.sign(&SystemRandom::new(), message).unwrap();
        let (r, s) = raw.as_ref().split_at(32);
        (r.try_into().unwrap(), s.try_into().unwrap())
    }

    /// The canonical signature (hex, DER) over ``message``: `s` is the smaller of the two it could be.
    pub fn sign(&self, message: &[u8]) -> String {
        let (r, s) = self.scalars(message);
        let low = if negate(&s) < s { negate(&s) } else { s };
        hex(&der(&r, &low))
    }

    /// The other valid signature over ``message``, with `s` in the upper half of the group.
    pub fn sign_high(&self, message: &[u8]) -> String {
        let (r, s) = self.scalars(message);
        let high = if negate(&s) > s { negate(&s) } else { s };
        hex(&der(&r, &high))
    }
}

impl Default for PhoneKey {
    fn default() -> PhoneKey {
        PhoneKey::new()
    }
}
