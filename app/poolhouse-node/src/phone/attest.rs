//! Hardware attestation of a distinct phone governance key.

mod chain;
mod der;
mod policy;
mod revocations;

pub use revocations::FreshRevocations;

use sha2::{Digest, Sha256};
use std::time::SystemTime;

type Result<T> = std::result::Result<T, AttestationError>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AttestationError {
    Bounds,
    Encoding,
    Chain,
    Unsupported,
    KeyPolicy,
    Hardware,
    Boot,
    Application,
    Revocations,
    Revoked,
}

impl std::fmt::Display for AttestationError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("This phone could not prove that its governance key meets the pool's hardware security requirements.")
    }
}
impl std::error::Error for AttestationError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HardwareLevel {
    TrustedEnvironment,
    StrongBox,
}

pub struct ExpectedAttestation {
    pub public: [u8; 65],
    pub challenge: [u8; 32],
}

pub struct VerifiedAttestation {
    public: [u8; 65],
    level: HardwareLevel,
    chain_digest: [u8; 32],
    policy_digest: [u8; 32],
}

impl VerifiedAttestation {
    pub fn public(&self) -> &[u8; 65] {
        &self.public
    }
    pub fn hardware_level(&self) -> HardwareLevel {
        self.level
    }
    pub fn chain_digest(&self) -> &[u8; 32] {
        &self.chain_digest
    }
    pub fn policy_digest(&self) -> &[u8; 32] {
        &self.policy_digest
    }
}

fn signer() -> Result<[u8; 32]> {
    parse_signer(option_env!("POOLHOUSE_PHONE_SIGNER_SHA256"))
}

fn parse_signer(text: Option<&str>) -> Result<[u8; 32]> {
    let text = text.ok_or(AttestationError::Application)?;
    if text.len() != 64
        || !text
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(AttestationError::Application);
    }
    let bytes = crate::fsutil::unhex(text).ok_or(AttestationError::Application)?;
    bytes.try_into().map_err(|_| AttestationError::Application)
}

/// Verify the key, challenge, hardware policy, certificate path and fresh revocation status.
pub fn verify(
    chain: &[Vec<u8>],
    expected: &ExpectedAttestation,
    now: SystemTime,
    revocations: &FreshRevocations,
) -> Result<VerifiedAttestation> {
    verify_with(
        chain,
        expected,
        now,
        revocations,
        &signer()?,
        chain::GOOGLE_ROOTS,
    )
}

fn verify_with(
    chain: &[Vec<u8>],
    expected: &ExpectedAttestation,
    now: SystemTime,
    revocations: &FreshRevocations,
    signer: &[u8; 32],
    roots: &[&[u8]],
) -> Result<VerifiedAttestation> {
    revocations.current(now)?;
    let extension = chain::check(chain, &expected.public, now, revocations, roots)?;
    let level = policy::check(&extension, &expected.challenge, signer)?;
    let mut digest = Sha256::new();
    for certificate in chain {
        digest.update((certificate.len() as u64).to_be_bytes());
        digest.update(certificate);
    }
    let mut policy = Sha256::new();
    policy.update(b"poolhouse/android-pool-key-policy/v1\napp.poolhouse.phone\n");
    policy.update(signer);
    for root in roots {
        policy.update(Sha256::digest(root));
    }
    Ok(VerifiedAttestation {
        public: expected.public,
        level,
        chain_digest: digest.finalize().into(),
        policy_digest: policy.finalize().into(),
    })
}

#[cfg(test)]
mod tests;
