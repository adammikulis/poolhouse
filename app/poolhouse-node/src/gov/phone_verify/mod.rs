//! Phone-owned governance evidence and signing requests.

mod checkpoint;
mod request;
#[cfg(test)]
mod tests;

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::error::{Error, Result};
use crate::fsutil::private_dir;
use super::act::Act;
use super::pool::{founding, GovPool, Ingest};
use super::{MAX_ACTS, MAX_ACT_BYTES};

pub use request::{Request, Verified};
use checkpoint::Checkpoint;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Binding {
    pub anchor: String,
    pub node: String,
    pub tls: String,
    pub public: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Proof {
    pub found: Act,
    pub acts: Vec<Act>,
}

pub struct Engine {
    root: PathBuf,
    checkpoint: Checkpoint,
    pool: Option<GovPool>,
    poisoned: bool,
}

pub(super) fn bad<T>(why: &str) -> Result<T> {
    Err(Error::Invalid(why.into()))
}

impl Binding {
    fn check(&self) -> Result<()> {
        if !super::act::valid_id(&self.anchor) || !super::act::valid_id(&self.node) || !super::act::valid_id(&self.tls) || super::keys::Alg::of_public(&self.public).is_none() {
            return bad("This phone key is not bound to that pool and computer.");
        }
        Ok(())
    }
}

impl Engine {
    /// Pin locally verified founding terms before their biometric signature.
    pub fn pin(root: &Path, binding: Binding) -> Result<()> {
        binding.check()?;
        private_dir(root)?;
        Checkpoint::pin(root, binding)
    }

    /// Open an existing phone-owned governance checkpoint.
    pub fn open(root: &Path, binding: &Binding) -> Result<Engine> {
        binding.check()?;
        let checkpoint = Checkpoint::read(root, binding)?;
        let pool = checkpoint.load(root)?;
        Ok(Engine { root: root.into(), checkpoint, pool, poisoned: false })
    }

    /// Verify evidence and persist the phone's observations before returning signing bytes.
    pub fn review(&mut self, proof: Proof, request: Request, wall_ms: u64) -> Result<Verified> {
        if self.poisoned {
            return bad("This phone's pool checkpoint must be checked before signing.");
        }
        self.checkpoint.check_current(&self.root)?;
        let genesis = founding(&proof.found)?;
        if genesis.anchor() != self.checkpoint.binding.anchor || genesis.first.device().map(|(_, f)| f) != Some(self.checkpoint.binding.node.clone()) {
            return bad("The pool's founding terms do not match this phone's pin.");
        }
        if proof.acts.len() > MAX_ACTS || proof.acts.iter().any(|a| serde_json::to_vec(a).map_or(true, |v| v.len() > MAX_ACT_BYTES)) {
            return bad("The pool evidence is too large to verify.");
        }
        if self.pool.is_none() {
            self.pool = Some(GovPool::create(&self.root, proof.found)?);
        }
        let pool = self.pool.as_mut().expect("the founding act created a pool");
        let held = pool.acts().clone();
        let supplied = proof.acts.clone();
        let outcomes = pool.ingest_many(proof.acts, wall_ms, false);
        let refused: std::collections::BTreeSet<_> = outcomes.iter().filter(|(_, result)| matches!(result, Ingest::Refused(_))).map(|(id, _)| id.clone()).collect();
        let mut recovered = std::collections::BTreeSet::new();
        for act in supplied {
            if refused.contains(&act.id()) && pool.observe_verified_chain(act.clone(), wall_ms) {
                recovered.insert(act.id());
            }
        }
        let evidence_error = outcomes.iter().any(|(id, result)| matches!(result, Ingest::Waiting) || (matches!(result, Ingest::Refused(_)) && !recovered.contains(id)));
        if held.iter().any(|(id, act)| pool.acts().get(id).is_none_or(|now| act.signatures.iter().any(|s| !now.signatures.contains(s)))) {
            self.poisoned = true;
            return bad("The phone cannot discard governance evidence it has already observed.");
        }
        pool.tick();
        let verified = request::verify(pool, &self.checkpoint.binding, request, wall_ms);
        if let Err(error) = self.checkpoint.commit(&self.root, pool) {
            self.poisoned = true;
            return Err(error);
        }
        if evidence_error {
            return bad("The phone needs verifiable governance evidence before signing.");
        }
        verified
    }

    /// Recheck the same request against the phone's current checkpoint.
    pub fn recheck(&mut self, verified: &Verified, wall_ms: u64) -> Result<()> {
        if self.poisoned {
            return bad("This phone's pool checkpoint must be checked before signing.");
        }
        self.checkpoint.check_current(&self.root)?;
        let pool = self.pool.as_mut().ok_or_else(|| Error::Invalid("The phone has not verified this pool.".into()))?;
        pool.tick();
        let current = request::verify(pool, &self.checkpoint.binding, verified.request.clone(), wall_ms)?;
        if current.bytes != verified.bytes || current.card != verified.card {
            return bad("The pool changed while this request was open. Read it again.");
        }
        self.checkpoint.commit(&self.root, pool)
    }
}
