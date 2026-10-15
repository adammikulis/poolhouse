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
    revocations_digest: [u8; 32],
    revocations_fetched_at: SystemTime,
    revocations_valid_until: SystemTime,
    checked_at: SystemTime,
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
    pub fn revocations_digest(&self) -> &[u8; 32] {
        &self.revocations_digest
    }
    pub fn revocations_fetched_at(&self) -> SystemTime {
        self.revocations_fetched_at
    }
    pub fn revocations_valid_until(&self) -> SystemTime {
        self.revocations_valid_until
    }
    pub fn checked_at(&self) -> SystemTime {
        self.checked_at
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

fn minimum_version(text: Option<&str>) -> Result<u64> {
    let text = text.ok_or(AttestationError::Application)?;
    if text.is_empty() || text.starts_with('0') || !text.bytes().all(|b| b.is_ascii_digit()) {
        return Err(AttestationError::Application);
    }
    text.parse::<u64>()
        .ok()
        .filter(|v| *v <= i32::MAX as u64)
        .ok_or(AttestationError::Application)
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
        minimum_version(option_env!("POOLHOUSE_PHONE_MIN_VERSION_CODE"))?,
        chain::GOOGLE_ROOTS,
    )
}

fn verify_with(
    chain: &[Vec<u8>],
    expected: &ExpectedAttestation,
    now: SystemTime,
    revocations: &FreshRevocations,
    signer: &[u8; 32],
    minimum_version: u64,
    roots: &[&[u8]],
) -> Result<VerifiedAttestation> {
    revocations.current(now)?;
    let extension = chain::check(chain, &expected.public, now, revocations, roots)?;
    let level = policy::check(&extension, &expected.challenge, signer, minimum_version)?;
    let mut digest = Sha256::new();
    for certificate in chain {
        digest.update((certificate.len() as u64).to_be_bytes());
        digest.update(certificate);
    }
    let mut policy = Sha256::new();
    policy.update(b"poolhouse/android-pool-key-policy/v1\napp.poolhouse.phone\n");
    policy.update(signer);
    policy.update(minimum_version.to_be_bytes());
    for root in roots {
        policy.update(Sha256::digest(root));
    }
    let (revocations_digest, revocations_fetched_at, revocations_valid_until) =
        revocations.evidence();
    Ok(VerifiedAttestation {
        public: expected.public,
        level,
        chain_digest: digest.finalize().into(),
        policy_digest: policy.finalize().into(),
        revocations_digest,
        revocations_fetched_at,
        revocations_valid_until,
        checked_at: now,
    })
}

#[cfg(test)]
mod provisioning_tests;
#[cfg(test)]
mod tests;
