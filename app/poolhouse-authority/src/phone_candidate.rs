//! Exact phone key-possession proof. Verification is not a person decision.
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

const DOMAIN: &[u8] = b"poolhouse/phone-add/v2\n";
const HALF_ORDER: [u8; 32] = [0x7f,0xff,0xff,0xff,0x80,0,0,0,0x7f,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xde,0x73,0x7d,0x56,0xd3,0x8b,0xcf,0x42,0x79,0xdc,0xe5,0x61,0x7e,0x31,0x92,0xa8];

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Transcript {
    alg: String, at_ms: u64, auth: String, expires: u64, key: String, name: String,
    node: String, offer: String, op: String, push: String, tls: String,
}

#[derive(Debug, PartialEq, Eq)]
pub struct Verified {
    digest: [u8; 32], key: [u8; 65], offer: [u8; 32], node: [u8; 32], tls: [u8; 32], expires: u64,
}
impl Verified {
    pub fn digest(&self) -> [u8; 32] { self.digest }
    pub fn key(&self) -> &[u8; 65] { &self.key }
    pub fn offer(&self) -> [u8; 32] { self.offer }
    pub fn node(&self) -> [u8; 32] { self.node }
    pub fn tls(&self) -> [u8; 32] { self.tls }
    pub fn expires(&self) -> u64 { self.expires }
}

fn hex(s: &str) -> Option<Vec<u8>> {
    if s.len() % 2 != 0 || !s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) { return None; }
    (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i+2], 16).ok()).collect()
}

fn scalar(bytes: &[u8]) -> Option<([u8; 32], &[u8])> {
    let [2, len, rest @ ..] = bytes else { return None };
    let len = usize::from(*len);
    if len == 0 || len > 33 || rest.len() < len { return None; }
    let (value, tail) = rest.split_at(len);
    if value[0] & 0x80 != 0 || (len > 1 && value[0] == 0 && value[1] & 0x80 == 0) { return None; }
    let value = value.strip_prefix(&[0]).unwrap_or(value);
    if value.len() > 32 { return None; }
    let mut out = [0u8; 32];
    out[32-value.len()..].copy_from_slice(value);
    (out != [0; 32]).then_some((out, tail))
}

/// Strict DER and low-S form; cryptographic verification additionally checks scalar range.
pub fn canonical_p256(sig: &[u8]) -> bool {
    let [0x30, len, rest @ ..] = sig else { return false };
    if usize::from(*len) != rest.len() || *len >= 128 { return false; }
    let Some((_, rest)) = scalar(rest) else { return false };
    let Some((s, rest)) = scalar(rest) else { return false };
    rest.is_empty() && s <= HALF_ORDER
}

/// Validate a canonical transcript against independently pinned node identities and verify possession.
pub fn verify(bytes: &[u8], signature: &str, node: &str, tls: &str) -> Result<Verified, ()> {
    if bytes.len() > 2048 || signature.len() > 144 { return Err(()); }
    let json = bytes.strip_prefix(DOMAIN).ok_or(())?;
    let t: Transcript = serde_json::from_slice(json).map_err(|_| ())?;
    if serde_json::to_vec(&t).map_err(|_| ())? != json || t.alg != "p256" || t.op != "phone_add"
        || t.node != node || t.tls != tls || hex(node).filter(|v| v.len() == 32 && v.iter().any(|b| *b != 0)).is_none()
        || hex(tls).filter(|v| v.len() == 32 && v.iter().any(|b| *b != 0)).is_none()
        || t.expires == 0 || t.at_ms == 0 || t.name.is_empty() || t.name.len() > 40 || t.name.trim() != t.name
        || !t.name.bytes().all(|b| b.is_ascii_graphic() || b == b' ') || hex(&t.auth).filter(|v| v.len() == 16).is_none() { return Err(()); }
    let key: [u8; 65] = hex(&t.key).ok_or(())?.try_into().map_err(|_| ())?;
    let push = hex(&t.push).ok_or(())?;
    let offer: [u8; 32] = hex(&t.offer).ok_or(())?.try_into().map_err(|_| ())?;
    if p256::PublicKey::from_sec1_bytes(&key).is_err() || p256::PublicKey::from_sec1_bytes(&push).is_err()
        || key[0] != 4 || push.len() != 65 || push[0] != 4 { return Err(()); }
    let sig = hex(signature).ok_or(())?;
    if !canonical_p256(&sig) || ring::signature::UnparsedPublicKey::new(&ring::signature::ECDSA_P256_SHA256_ASN1, key).verify(bytes, &sig).is_err() { return Err(()); }
    Ok(Verified { digest: Sha256::digest(bytes).into(), key, offer,
        node: hex(node).ok_or(())?.try_into().map_err(|_| ())?, tls: hex(tls).ok_or(())?.try_into().map_err(|_| ())?, expires: t.expires })
}
