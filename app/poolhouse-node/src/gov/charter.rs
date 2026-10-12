//! The charter: the signed document that names a pool's admin keys, thresholds, holds and join policy.
//!
//! The pool id is the first 16 bytes of the SHA-256 of the epoch-1 charter, 32 hex digits, so a copy of a pool with other admins is
//! another pool and a stranger cannot make a pool with this id and other admins. The join policy is part of the body and never
//! changes inside a pool. Epoch 1 is the founding charter; each later epoch is the charter the acts of `chain` produce.

use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::error::{Error, Result};
use crate::fsutil::sha256_hex;
use crate::membership::Policy;
use crate::row::canonical;

use super::keys::{AdminKey, Role, Strength};
use super::MAX_ADMINS;

/// Which threshold an act is held to.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Class {
    Routine,
    Sensitive,
}

/// What a threshold counts: distinct people, or distinct keys.
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Unit {
    Person,
    Key,
}

#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    Admins,
    Members,
}

/// How many signers an act of each class needs; `sensitive` 0 means one while the set holds one person and two once it holds two.
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Thresholds {
    pub routine: u32,
    pub sensitive: u32,
    pub unit: Unit,
}

/// How long each lever that takes a pool is held before it takes effect, in the unit its name says.
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Holds {
    pub admin_add_h: u64,
    pub admin_remove_h: u64,
    pub handoff_min: u64,
    pub dispute_d: u64,
}

impl Default for Thresholds {
    fn default() -> Thresholds {
        Thresholds { routine: 1, sensitive: 0, unit: Unit::Person }
    }
}

impl Default for Holds {
    fn default() -> Holds {
        Holds { admin_add_h: 24, admin_remove_h: 24, handoff_min: 15, dispute_d: 7 }
    }
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Charter {
    pub v: u32,
    pub epoch: u64,
    pub name: String,
    pub policy: Policy,
    pub mode: Mode,
    /// The project key this pool belongs to, or empty.
    pub project: String,
    /// Sorted by key.
    pub admins: Vec<AdminKey>,
    pub thresholds: Thresholds,
    pub holds: Holds,
    pub recover_delay_days: u64,
    pub succession: Option<Value>,
    pub from: Option<Value>,
    pub at_ms: u64,
}

fn bad<T>(why: &str) -> Result<T> {
    Err(Error::Invalid(why.into()))
}

impl Charter {
    /// An epoch-1 charter with the default thresholds and holds; the keys are sorted.
    pub fn new(name: &str, policy: Policy, project: &str, mut admins: Vec<AdminKey>, at_ms: u64) -> Charter {
        admins.sort_by(|a, b| a.key.cmp(&b.key));
        Charter {
            v: 1, epoch: 1, name: name.into(), policy, mode: Mode::Admins, project: project.into(), admins, thresholds: Thresholds::default(),
            holds: Holds::default(), recover_delay_days: 7, succession: None, from: None, at_ms,
        }
    }

    pub fn body(&self) -> Value {
        serde_json::to_value(self).unwrap_or(Value::Null)
    }

    /// The hash of this charter, 64 hex digits: what an act's `prev` names.
    pub fn hash(&self) -> String {
        sha256_hex(canonical(&self.body()).as_bytes())
    }

    /// The pool id this charter makes when it is the first: from the charter before any key accepted it, so no signature changes it.
    pub fn pool_id(&self) -> String {
        self.anchor()[..32].to_string()
    }

    /// The hash of this charter with every acceptance blanked: what the founding keys accept.
    pub fn anchor(&self) -> String {
        let mut bare = self.clone();
        bare.admins.iter_mut().for_each(|k| k.acceptance = String::new());
        bare.hash()
    }

    pub fn admin(&self, key: &str) -> Option<&AdminKey> {
        self.admins.iter().find(|k| k.key == key)
    }

    /// The keys that count toward a threshold: admin role and strong.
    pub fn strong(&self) -> impl Iterator<Item = &AdminKey> {
        self.admins.iter().filter(|k| k.role == Role::Admin && k.strength == Strength::Strong)
    }

    pub fn is_strong(&self, key: &str) -> bool {
        self.strong().any(|k| k.key == key)
    }

    /// How many distinct people hold a strong key.
    pub fn people(&self) -> usize {
        self.strong().map(|k| k.person.as_str()).collect::<BTreeSet<_>>().len()
    }

    /// Whether no strong admin key is listed.
    pub fn weak_only(&self) -> bool {
        self.strong().next().is_none()
    }

    /// How many signers an act of ``class`` needs.
    pub fn needed(&self, class: Class) -> usize {
        match (class, self.thresholds.sensitive) {
            (Class::Routine, _) => self.thresholds.routine as usize,
            (Class::Sensitive, 0) => self.people().clamp(1, 2),
            (Class::Sensitive, n) => n as usize,
        }
    }

    fn counted(&self, keys: &[&str], all: bool) -> usize {
        let held: Vec<&AdminKey> = self.admins.iter().filter(|k| keys.contains(&k.key.as_str()) && k.role == Role::Admin && (all || k.strength == Strength::Strong)).collect();
        match self.thresholds.unit {
            Unit::Person => held.iter().map(|k| k.person.as_str()).collect::<BTreeSet<_>>().len(),
            Unit::Key => held.iter().map(|k| k.key.as_str()).collect::<BTreeSet<_>>().len(),
        }
    }

    /// How many of ``keys`` count: the strong admin keys of this charter among them, by person or by key.
    pub fn count(&self, keys: &[&str]) -> usize {
        self.counted(keys, false)
    }

    /// Like `count`, weak admin keys included: only the end of a delegate is signed by them.
    pub fn count_any(&self, keys: &[&str]) -> usize {
        self.counted(keys, true)
    }

    /// Whether the thresholds can be met by the keys listed.
    pub fn reachable(&self) -> bool {
        self.needed(Class::Routine) <= self.strong_units() && self.needed(Class::Sensitive) <= self.strong_units()
    }

    fn strong_units(&self) -> usize {
        match self.thresholds.unit {
            Unit::Person => self.people(),
            Unit::Key => self.strong().count(),
        }
    }

    /// Whether this is a charter a pool can start from: the shape, the keys and each key's acceptance.
    pub fn check_founding(&self) -> Result<()> {
        self.check_form()?;
        let anchor = self.anchor();
        if !self.admins.iter().all(|k| k.accepted(&anchor)) {
            return bad("every admin key accepts the charter it is listed in");
        }
        self.check_numbers()
    }

    /// Whether this charter is of the form a pool starts from, before any key has accepted it.
    pub fn check_form(&self) -> Result<()> {
        if self.v != 1 || self.epoch != 1 || self.mode != Mode::Admins || self.succession.is_some() || self.from.is_some() || self.at_ms == 0 {
            return bad("this build founds a pool of epoch 1 governed by admins, with no succession and no earlier pool");
        }
        if self.name.is_empty() || self.name.chars().count() > 40 || !self.name.chars().all(|c| c.is_alphanumeric() || matches!(c, ' ' | '.' | '_' | '-')) {
            return bad("a pool's name is at most 40 letters, digits, spaces and dots");
        }
        if !self.project.is_empty() && !(self.project.len() == 16 && self.project.bytes().all(|b| b.is_ascii_hexdigit())) {
            return bad("a pool's project is a project key");
        }
        self.check_keys(1)?;
        self.check_numbers()
    }

    /// The keys: sorted and distinct, each well formed and listed at ``since``, at most one recovery key and one admin at least.
    pub fn check_keys(&self, since: u64) -> Result<()> {
        if self.admins.is_empty() || self.admins.len() > MAX_ADMINS || !self.admins.windows(2).all(|w| w[0].key < w[1].key) {
            return bad("a charter names one to sixteen admin keys, sorted and distinct");
        }
        for k in &self.admins {
            k.check()?;
        }
        if self.admins.iter().any(|k| k.since > since) || self.admins.iter().filter(|k| k.role == Role::Recovery).count() > 1 || !self.admins.iter().any(|k| k.role == Role::Admin) {
            return bad("a charter names an admin key and at most one recovery key");
        }
        Ok(())
    }

    /// The thresholds, holds and delays are in range and the thresholds can be met.
    pub fn check_numbers(&self) -> Result<()> {
        let (t, h) = (&self.thresholds, &self.holds);
        if t.routine == 0 || t.routine > 16 || t.sensitive > 16 || !(1..=720).contains(&h.admin_add_h) || !(1..=720).contains(&h.admin_remove_h) || !(1..=1440).contains(&h.handoff_min) {
            return bad("a threshold is one to sixteen and a hold is in range");
        }
        if !(1..=30).contains(&h.dispute_d) || !(1..=30).contains(&self.recover_delay_days) {
            return bad("a dispute and a recovery last one to thirty days");
        }
        if !self.weak_only() && !self.reachable() {
            return bad("the thresholds need more people than the strong keys listed");
        }
        Ok(())
    }
}
