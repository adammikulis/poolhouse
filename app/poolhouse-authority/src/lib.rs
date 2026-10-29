//! Typed requests and serialized state transitions for the native authority.
#![deny(unsafe_code)]

pub mod certificate;
pub mod receipt;

#[cfg(target_os = "macos")]
#[allow(dead_code)]
mod auth;
#[cfg(target_os = "macos")]
#[allow(dead_code)]
mod store;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

const DOMAIN: &[u8] = b"poolhouse.native-authority.operation.v1\0";
const MAX_REQUEST: usize = 4096;
const MAX_LIFETIME: u64 = 300;
type Hash = [u8; 32];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Error {
    InvalidRequest,
    WrongBinding,
    StaleState,
    Expired,
    Unauthorized,
    Unavailable,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Binding {
    authority: Hash,
    owner_uid: u32,
    node: Hash,
    tls: Hash,
    pool: Hash,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "kind", deny_unknown_fields)]
pub enum Action {
    Bootstrap { approval_key_id: Hash, device: Hash },
    EnrollPhone { approval_key_id: Hash, device: Hash },
    RevokePhone { approval_key_id: Hash, device: Hash },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Operation {
    version: u8,
    binding: Binding,
    generation: u64,
    previous: Hash,
    nonce: Hash,
    issued_at: u64,
    expires_at: u64,
    action: Action,
}

impl Operation {
    /// Parse a bounded request with an exact field schema.
    pub fn parse(bytes: &[u8]) -> Result<Self, Error> {
        if bytes.len() > MAX_REQUEST {
            return Err(Error::InvalidRequest);
        }
        let value: Self = serde_json::from_slice(bytes).map_err(|_| Error::InvalidRequest)?;
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), Error> {
        if self.version != 1
            || self.binding.authority == [0; 32]
            || self.binding.node == [0; 32]
            || self.binding.tls == [0; 32]
            || self.binding.pool == [0; 32]
            || self.nonce == [0; 32]
            || self
                .expires_at
                .checked_sub(self.issued_at)
                .filter(|n| *n > 0 && *n <= MAX_LIFETIME)
                .is_none()
        {
            return Err(Error::InvalidRequest);
        }
        let (key, device) = match &self.action {
            Action::Bootstrap {
                approval_key_id,
                device,
            } => (approval_key_id, Some(device)),
            Action::EnrollPhone {
                approval_key_id,
                device,
            }
            | Action::RevokePhone {
                approval_key_id,
                device,
            } => (approval_key_id, Some(device)),
        };
        if *key == [0; 32] || device.is_some_and(|d| *d == [0; 32]) {
            return Err(Error::InvalidRequest);
        }
        Ok(())
    }

    /// Return the domain-separated canonical bytes for this exact request.
    pub fn signing_bytes(&self) -> Vec<u8> {
        let mut bytes = DOMAIN.to_vec();
        bytes.push(self.version);
        bytes.extend(self.binding.authority);
        bytes.extend(self.binding.owner_uid.to_be_bytes());
        for hash in [self.binding.node, self.binding.tls, self.binding.pool] {
            bytes.extend(hash);
        }
        bytes.extend(self.generation.to_be_bytes());
        bytes.extend(self.previous);
        bytes.extend(self.nonce);
        bytes.extend(self.issued_at.to_be_bytes());
        bytes.extend(self.expires_at.to_be_bytes());
        match self.action {
            Action::Bootstrap {
                approval_key_id,
                device,
            } => {
                bytes.push(0);
                bytes.extend(approval_key_id);
                bytes.extend(device);
            }
            Action::EnrollPhone {
                approval_key_id,
                device,
            } => {
                bytes.push(1);
                bytes.extend(approval_key_id);
                bytes.extend(device);
            }
            Action::RevokePhone {
                approval_key_id,
                device,
            } => {
                bytes.push(2);
                bytes.extend(approval_key_id);
                bytes.extend(device);
            }
        }
        bytes
    }

    fn digest(&self) -> Hash {
        Sha256::digest(self.signing_bytes()).into()
    }
}

// Constructors are confined to the future native authorization backend.
#[allow(dead_code)]
struct VerifiedAuthorization {
    digest: Hash,
    generation: u64,
    scope: Scope,
    signer: Hash,
    possession: Option<VerifiedPossession>,
}
#[allow(dead_code)]
struct VerifiedPossession {
    digest: Hash,
    key_id: Hash,
}
#[allow(dead_code)]
#[derive(Clone, Copy, PartialEq, Eq)]
enum Scope {
    InstallerPresence,
    ApprovalKey,
}

#[allow(dead_code)]
#[derive(Clone)]
struct State {
    binding: Binding,
    generation: u64,
    head: Hash,
    pending: Option<Operation>,
    phones: Vec<(Hash, Hash)>,
    revoked: Vec<Hash>,
}

#[allow(dead_code)]
impl State {
    fn prepare(&mut self, action: Action, now: u64) -> Result<Operation, Error> {
        if self.pending.is_some() {
            return Err(Error::Unavailable);
        }
        let mut nonce = [0; 32];
        getrandom::fill(&mut nonce).map_err(|_| Error::Unavailable)?;
        let operation = Operation {
            version: 1,
            binding: self.binding.clone(),
            generation: self.generation,
            previous: self.head,
            nonce,
            issued_at: now,
            expires_at: now.checked_add(MAX_LIFETIME).ok_or(Error::InvalidRequest)?,
            action,
        };
        operation.validate()?;
        self.pending = Some(operation.clone());
        Ok(operation)
    }

    fn commit(
        &mut self,
        operation: &Operation,
        proof: VerifiedAuthorization,
        now: u64,
    ) -> Result<(), Error> {
        operation.validate()?;
        if operation.binding != self.binding {
            return Err(Error::WrongBinding);
        }
        if operation.generation != self.generation
            || operation.previous != self.head
            || self.pending.as_ref() != Some(operation)
        {
            return Err(Error::StaleState);
        }
        if now < operation.issued_at || now >= operation.expires_at {
            return Err(Error::Expired);
        }
        if proof.digest != operation.digest() || proof.generation != self.generation {
            return Err(Error::Unauthorized);
        }
        let next = self.generation.checked_add(1).ok_or(Error::StaleState)?;
        if let Action::Bootstrap {
            approval_key_id, ..
        }
        | Action::EnrollPhone {
            approval_key_id, ..
        } = operation.action
        {
            let possession = proof.possession.as_ref().ok_or(Error::Unauthorized)?;
            if possession.digest != operation.digest()
                || possession.key_id != approval_key_id
                || self.revoked.contains(&approval_key_id)
            {
                return Err(Error::Unauthorized);
            }
        }
        match operation.action {
            Action::Bootstrap {
                approval_key_id,
                device,
            } => {
                if proof.scope != Scope::InstallerPresence
                    || self.generation != 0
                    || !self.phones.is_empty()
                    || proof.signer != approval_key_id
                {
                    return Err(Error::Unauthorized);
                }
                self.phones.push((approval_key_id, device));
            }
            Action::EnrollPhone {
                approval_key_id,
                device,
            } => {
                if proof.scope != Scope::ApprovalKey
                    || self.generation == 0
                    || !self.phones.iter().any(|(key, _)| *key == proof.signer)
                    || self
                        .phones
                        .iter()
                        .any(|(key, d)| *key == approval_key_id || *d == device)
                    || self.phones.len() >= 32
                {
                    return Err(Error::Unauthorized);
                }
                self.phones.push((approval_key_id, device));
            }
            Action::RevokePhone {
                approval_key_id,
                device,
            } => {
                if proof.scope != Scope::ApprovalKey
                    || self.generation == 0
                    || proof.signer != approval_key_id
                {
                    return Err(Error::Unauthorized);
                }
                let index = self
                    .phones
                    .iter()
                    .position(|record| *record == (approval_key_id, device))
                    .ok_or(Error::Unauthorized)?;
                if self.revoked.len() >= 1024 {
                    return Err(Error::Unavailable);
                }
                self.phones.remove(index);
                self.revoked.push(approval_key_id);
            }
        }
        self.generation = next;
        self.head = operation.digest();
        self.pending = None;
        Ok(())
    }
}

#[cfg(test)]
mod tests;
