//! Canonical detached native certificate descriptors and issuer signatures.

use curve25519_dalek::edwards::CompressedEdwardsY;
use ed25519_dalek::{Signature, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

const DOMAIN: &[u8] = b"poolhouse.native-certificate.0.v1\0";
const MAX_DESCRIPTOR: usize = 2048;
const MAX_CERTIFICATE: usize = 2304;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Error {
    Malformed,
    WrongDescriptor,
    InvalidIssuer,
    InvalidSignature,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct DescriptorFields {
    v: u8,
    issuer_id: String,
    admission_kind: String,
    admission_ref: String,
    pool: String,
    anchor: String,
    person: String,
    account: String,
    device: String,
    key_alg: String,
    key: String,
    role: String,
    since_epoch: u64,
    acceptance_message_digest: String,
    policy: String,
    serial: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Descriptor(DescriptorFields);

impl Descriptor {
    /// Parse a bounded canonical descriptor.
    pub fn parse(bytes: &[u8]) -> Result<Self, Error> {
        let descriptor = Self(canonical(bytes, MAX_DESCRIPTOR)?);
        descriptor.validate()?;
        Ok(descriptor)
    }

    fn validate(&self) -> Result<(), Error> {
        let hashes = [&self.0.issuer_id, &self.0.admission_ref, &self.0.anchor, &self.0.person,
            &self.0.account, &self.0.device, &self.0.acceptance_message_digest, &self.0.policy, &self.0.serial];
        if self.0.v != 1 || hashes.iter().any(|text| !hexadecimal(text, 64))
            || !hexadecimal(&self.0.pool, 32) || self.0.pool != self.0.anchor[..32]
            || !matches!(self.0.admission_kind.as_str(), "genesis" | "act")
            || (self.0.admission_kind == "genesis" && self.0.admission_ref != self.0.anchor)
            || !matches!(self.0.role.as_str(), "admin" | "recovery")
            || self.0.since_epoch == 0 || self.0.key_alg != "p256"
            || !hexadecimal(&self.0.key, 130) || !self.0.key.starts_with("04")
        {
            return Err(Error::Malformed);
        }
        p256::PublicKey::from_sec1_bytes(&decode::<65>(&self.0.key)?)
            .map_err(|_| Error::Malformed)?;
        Ok(())
    }

    /// Return the exact domain-separated bytes signed by the issuer.
    pub fn signing_bytes(&self) -> Vec<u8> {
        let mut bytes = DOMAIN.to_vec();
        bytes.extend(serde_json::to_vec(&self.0).expect("descriptor strings serialize"));
        bytes
    }

    /// Return the digest of the exact signed descriptor.
    pub fn digest(&self) -> [u8; 32] {
        Sha256::digest(self.signing_bytes()).into()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct CertificateFields {
    v: u8,
    descriptor: DescriptorFields,
    signature: String,
}

#[derive(Debug, Clone)]
pub struct Certificate(CertificateFields);

impl Certificate {
    /// Parse a bounded canonical detached certificate.
    pub fn parse(bytes: &[u8]) -> Result<Self, Error> {
        let certificate = Self(canonical(bytes, MAX_CERTIFICATE)?);
        Descriptor(certificate.0.descriptor.clone()).validate()?;
        if certificate.0.v != 1 || !hexadecimal(&certificate.0.signature, 128) {
            return Err(Error::Malformed);
        }
        Ok(certificate)
    }

    /// Verify only the issuer signature over the independently expected descriptor.
    pub fn verify(&self, expected: &Descriptor, issuer: &[u8; 32]) -> Result<VerifiedIssuerSignature, Error> {
        if self.0.descriptor != expected.0 {
            return Err(Error::WrongDescriptor);
        }
        let point = CompressedEdwardsY(*issuer).decompress().ok_or(Error::InvalidIssuer)?;
        if point.compress().to_bytes() != *issuer || point.is_small_order() || !point.is_torsion_free()
            || encode(&Sha256::digest(issuer)) != expected.0.issuer_id
        {
            return Err(Error::InvalidIssuer);
        }
        let key = VerifyingKey::from_bytes(issuer).map_err(|_| Error::InvalidIssuer)?;
        let signature = Signature::from_bytes(&decode::<64>(&self.0.signature)?);
        key.verify_strict(&expected.signing_bytes(), &signature).map_err(|_| Error::InvalidSignature)?;
        Ok(VerifiedIssuerSignature { descriptor: expected.clone(), issuer: *issuer })
    }
}

#[derive(Debug, Clone)]
pub struct VerifiedIssuerSignature {
    descriptor: Descriptor,
    issuer: [u8; 32],
}

impl VerifiedIssuerSignature {
    /// Return the descriptor covered by the verified signature.
    pub fn descriptor(&self) -> &Descriptor { &self.descriptor }

    /// Return the independently supplied issuer key.
    pub fn issuer(&self) -> &[u8; 32] { &self.issuer }
}

fn canonical<T: for<'de> Deserialize<'de> + Serialize>(bytes: &[u8], bound: usize) -> Result<T, Error> {
    if bytes.is_empty() || bytes.len() > bound { return Err(Error::Malformed); }
    let value: T = serde_json::from_slice(bytes).map_err(|_| Error::Malformed)?;
    if serde_json::to_vec(&value).map_err(|_| Error::Malformed)? != bytes { return Err(Error::Malformed); }
    Ok(value)
}

fn hexadecimal(text: &str, length: usize) -> bool {
    text.len() == length && text.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn decode<const N: usize>(text: &str) -> Result<[u8; N], Error> {
    if !hexadecimal(text, N * 2) { return Err(Error::Malformed); }
    let mut bytes = [0; N];
    for (index, byte) in bytes.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&text[index * 2..index * 2 + 2], 16).map_err(|_| Error::Malformed)?;
    }
    Ok(bytes)
}

fn encode(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[cfg(test)]
mod tests;
