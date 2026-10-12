//! Android Key Attestation of a phone's approval key: the certificate chain the phone's secure hardware signed about the key, checked
//! against the roots a node trusts, and what it says about the key.
//!
//! A phone key counts as strong (docs/pools-and-serving.md, 1.3) only when the chain shows that the key was made in hardware (the
//! trusted environment or StrongBox), is bound to the person's fingerprint for every signature (no key use without authentication, no
//! time window), is a P-256 key, is the key that is being added, and was attested for this addition (the challenge). A phone that shows
//! none of this is added as weak and founds nothing.
//!
//! What is not checked: the revocation list Google publishes for attestation keys, and the validity dates of the chain.

use ring::signature::{UnparsedPublicKey, VerificationAlgorithm, ECDSA_P256_SHA256_ASN1, ECDSA_P384_SHA384_ASN1, RSA_PKCS1_2048_8192_SHA256};
use x509_parser::prelude::{FromDer, X509Certificate};

use crate::fsutil::sha256_hex;

/// SHA-256, in hex, of the SubjectPublicKeyInfo of each root of Google's published Android Key Attestation chains.
///
/// TODO(owner): fill from the roots Google publishes (https://developer.android.com/privacy-and-security/security-key-attestation,
/// "Root certificate") by hashing each root's SubjectPublicKeyInfo. None is written here from memory: while the list is empty no chain is
/// rooted, so every phone is added as weak and no pool can be founded on a real machine.
pub const GOOGLE_ROOTS: [&str; 0] = [];

const OID_KEY_DESCRIPTION: &str = "1.3.6.1.4.1.11129.2.1.17";
const DOMAIN: &str = "poolhouse/phone-attest/v1\n";
const MOST_CHAIN: usize = 6;
/// The tags of the authorization list that say how a key may be used.
const TAG_ALGORITHM: u32 = 2;
const TAG_EC_CURVE: u32 = 10;
const TAG_NO_AUTH_REQUIRED: u32 = 503;
const TAG_USER_AUTH_TYPE: u32 = 504;
const TAG_AUTH_TIMEOUT: u32 = 505;
const ALGORITHM_EC: u64 = 3;
const CURVE_P256: u64 = 1;
const AUTH_FINGERPRINT: u64 = 2;
const LEVEL_TRUSTED_ENVIRONMENT: u64 = 1;

/// What the phone asks the hardware to attest to when it makes its key for this node: the node, and the one-time code of this addition.
pub fn challenge(node_fp: &str, secret: &str) -> Vec<u8> {
    format!("{DOMAIN}{node_fp}\n{secret}").into_bytes()
}

struct Tlv<'a> {
    class: u8,
    tag: u32,
    body: &'a [u8],
}

fn tlv(b: &[u8]) -> Option<(Tlv<'_>, &[u8])> {
    let first = *b.first()?;
    let (mut at, mut tag) = (1usize, u32::from(first & 0x1f));
    if tag == 0x1f {
        tag = 0;
        loop {
            let x = *b.get(at)?;
            at += 1;
            tag = tag.checked_mul(128)?.checked_add(u32::from(x & 0x7f))?;
            if x & 0x80 == 0 {
                break;
            }
        }
    }
    let l = *b.get(at)?;
    at += 1;
    let len = if l < 0x80 {
        usize::from(l)
    } else {
        let n = usize::from(l & 0x7f);
        if n == 0 || n > 4 {
            return None;
        }
        let mut v = 0usize;
        for _ in 0..n {
            v = v << 8 | usize::from(*b.get(at)?);
            at += 1;
        }
        v
    };
    let end = at.checked_add(len)?;
    Some((Tlv { class: first >> 6, tag, body: b.get(at..end)? }, &b[end..]))
}

fn items(mut b: &[u8]) -> Option<Vec<Tlv<'_>>> {
    let mut out = Vec::new();
    while !b.is_empty() {
        let (one, rest) = tlv(b)?;
        out.push(one);
        b = rest;
    }
    Some(out)
}

fn int(b: &[u8]) -> Option<u64> {
    (!b.is_empty() && b.len() <= 8).then(|| b.iter().fold(0u64, |a, x| a << 8 | u64::from(*x)))
}

/// The authorization list in ``list``: each explicitly tagged field as its tag and the element inside it.
fn fields<'a>(list: &Tlv<'a>) -> Option<Vec<(u32, Tlv<'a>)>> {
    items(list.body)?.into_iter().filter(|f| f.class == 2).map(|f| tlv(f.body).map(|(inner, _)| (f.tag, inner))).collect()
}

fn number(list: &[(u32, Tlv<'_>)], tag: u32) -> Option<u64> {
    list.iter().find(|(t, _)| *t == tag).and_then(|(_, v)| int(v.body))
}

/// What the KeyDescription extension says: made in hardware, for this challenge, with a fingerprint needed for every use of a P-256 key.
fn described(ext: &[u8], challenge: &[u8]) -> Result<(), &'static str> {
    let (whole, _) = tlv(ext).ok_or("the attestation cannot be read")?;
    let parts = items(whole.body).filter(|p| p.len() >= 8).ok_or("the attestation cannot be read")?;
    let level = |i: usize| if parts[i].tag == 10 { int(parts[i].body) } else { None };
    if level(1).is_none_or(|l| l < LEVEL_TRUSTED_ENVIRONMENT) || level(3).is_none_or(|l| l < LEVEL_TRUSTED_ENVIRONMENT) {
        return Err("the key was not made in the phone's secure hardware");
    }
    if parts[4].body != challenge {
        return Err("the attestation was made for another addition");
    }
    let tee = fields(&parts[7]).ok_or("the attestation cannot be read")?;
    if tee.iter().any(|(t, _)| *t == TAG_NO_AUTH_REQUIRED || *t == TAG_AUTH_TIMEOUT) || number(&tee, TAG_USER_AUTH_TYPE).is_none_or(|t| t & AUTH_FINGERPRINT == 0) {
        return Err("the key can be used without the person's fingerprint for each signature");
    }
    if number(&tee, TAG_ALGORITHM) != Some(ALGORITHM_EC) || number(&tee, TAG_EC_CURVE) != Some(CURVE_P256) {
        return Err("the key is not a P-256 key");
    }
    Ok(())
}

fn signed_by(child: &X509Certificate, parent: &X509Certificate) -> bool {
    let verifier: &dyn VerificationAlgorithm = match child.signature_algorithm.algorithm.to_id_string().as_str() {
        "1.2.840.10045.4.3.2" => &ECDSA_P256_SHA256_ASN1,
        "1.2.840.10045.4.3.3" => &ECDSA_P384_SHA384_ASN1,
        "1.2.840.113549.1.1.11" => &RSA_PKCS1_2048_8192_SHA256,
        _ => return false,
    };
    UnparsedPublicKey::new(verifier, &parent.public_key().subject_public_key.data).verify(child.tbs_certificate.as_ref(), &child.signature_value.data).is_ok()
}

/// Whether ``chain`` (certificates in DER, the key's first and the root last) is rooted at one of ``roots`` (SHA-256 hex of a root's key),
/// every certificate is signed by the next, and the first is about the P-256 key ``point`` and says what `described` needs for ``challenge``.
/// The error is a short reason for the log, never shown as a sentence.
pub fn verify(chain: &[Vec<u8>], roots: &[String], point: &[u8], challenge: &[u8]) -> Result<(), &'static str> {
    if chain.len() < 2 || chain.len() > MOST_CHAIN {
        return Err("the chain has the wrong length");
    }
    let certs: Vec<X509Certificate> = chain.iter().map(|d| X509Certificate::from_der(d).ok().filter(|(rest, _)| rest.is_empty()).map(|(_, c)| c)).collect::<Option<_>>().ok_or("a certificate cannot be read")?;
    if !certs.windows(2).all(|w| signed_by(&w[0], &w[1])) {
        return Err("a certificate is not signed by the next one");
    }
    let root = certs.last().map(|c| sha256_hex(c.public_key().raw)).unwrap_or_default();
    if !roots.contains(&root) {
        return Err("the chain does not end at a root this node trusts");
    }
    let leaf = &certs[0];
    if leaf.public_key().subject_public_key.data.as_ref() != point {
        return Err("the attestation is about another key");
    }
    let ext = leaf.iter_extensions().find(|e| e.oid.to_id_string() == OID_KEY_DESCRIPTION).ok_or("the certificate carries no attestation")?;
    described(ext.value, challenge)
}
