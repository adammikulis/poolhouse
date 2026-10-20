//! The charter: the signed document that names a pool's admin keys, thresholds, holds, join policy and first device.
//!
//! The pool id is the first 16 bytes of the anchor, 32 hex digits: the SHA-256 of the epoch-1 charter with the signatures of its keys
//! left out, so a copy of a pool with other admins is another pool, a stranger cannot make a pool with this id and other admins, and
//! one set of terms is one pool however its keys happen to sign it. The join policy is part of the body and never changes inside a
//! pool. The first device (its certificate and name) is part of the body too: the keys that accept the charter accept that device as the
//! pool's first member, so founding is one signature and no later admit. Epoch 1 is the founding charter; each later epoch is the
//! charter the acts of `chain` produce.

use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::cert::{cert_fingerprint, device_public, parses};
use crate::error::{Error, Result};
use crate::fsutil::{sha256_hex, unhex};
use crate::membership::Policy;
use crate::row::canonical;

use super::act::{Act, WINDOW_MS};
use super::keys::{AdminKey, Level, Role};
use super::{DATA_WINDOW_MS, MAX_ADMINS};

/// The version of the charter this build reads and writes; a pool of another version is not one this build can run.
pub const VERSION: u32 = 4;
/// What a device says of a charter that lists no admin key above `Weak`: nobody could admit a device under it.
pub const NO_COUNTING_KEY: &str = "A pool needs a key that is approved from a computer or a phone, and that charter lists none.";
/// What a device that meets a charter of another version says.
pub const OTHER_VERSION: &str = "this pool was made by another version of Poolhouse and cannot be used: make a new pool";

/// Which threshold and which floor an act is held to: `Routine` (a cancel, ending a delegate), `Sensitive` (a removal, admitting a device,
/// a grant of a sensitive action) and `Governance` (a change to who governs the pool, its thresholds, holds, floors or delegate).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Class {
    Routine,
    Sensitive,
    Governance,
}

/// The class whose floor the keys that sign an act of ``kind`` must meet; none for a kind that a device, a key of any level or the key a
/// removal names signs.
pub fn floor_class(kind: &str) -> Option<Class> {
    match kind {
        "admit" | "remove" => Some(Class::Sensitive),
        "cancel" | "delegate" | "admin_add" | "admin_remove" | "set_threshold" | "set_holds" | "set_mode" | "set_floors" | "recovery_replace" => Some(Class::Governance),
        _ => None,
    }
}

/// The data acts that cannot be undone once done.
pub const IRREVERSIBLE_DATA_ACTS: [&str; 2] = ["file_delete", "shard_consent"];

/// How long the data act ``action``, approved by keys at ``levels``, waits for a key to cancel it: a day when it cannot be undone and the
/// highest level that approved it is `Computer`, none otherwise (a typed yes is the developer's terminal path and a `Native` or `Phone` key
/// is above the computer).
pub fn data_act_wait_ms(action: &str, levels: &[Level]) -> i64 {
    if IRREVERSIBLE_DATA_ACTS.contains(&action) && levels.iter().max() == Some(&Level::Computer) { DATA_WINDOW_MS } else { 0 }
}

/// The lowest level at which an admin key counts for each class of act. A floor is never `Weak`.
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Floors {
    pub routine: Level,
    pub sensitive: Level,
    pub governance: Level,
}

impl Floors {
    pub fn of(&self, class: Class) -> Level {
        match class {
            Class::Routine => self.routine,
            Class::Sensitive => self.sensitive,
            Class::Governance => self.governance,
        }
    }

    /// The floors of a pool founded by ``admins``: the lowest level among its admin keys for routine acts, the highest for sensitive acts
    /// and governance; none when no admin key is above `Weak`.
    pub fn default_for(admins: &[AdminKey]) -> Option<Floors> {
        let levels: Vec<Level> = admins.iter().filter(|k| k.role == Role::Admin && k.assurance > Level::Weak).map(|k| k.assurance).collect();
        let (lowest, highest) = (levels.iter().min().copied()?, levels.iter().max().copied()?);
        Some(Floors { routine: lowest, sensitive: highest, governance: highest })
    }
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

/// The first device of a pool: the certificate (hex DER) and the name the founding keys put in it.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct First {
    pub cert: String,
    pub name: String,
}

/// The longest name a device carries in a charter.
pub const MOST_DEVICE_NAME: usize = 40;

impl First {
    /// The first device of the device whose certificate is ``der``, called ``name``.
    pub fn of(der: &[u8], name: &str) -> First {
        First { cert: crate::fsutil::hex(der), name: name.chars().filter(|c| !c.is_control()).take(MOST_DEVICE_NAME).collect() }
    }

    /// The certificate as DER and its fingerprint, when it is a device certificate of an Ed25519 key spelled in lower-case hex, so one
    /// device is one charter text.
    pub fn device(&self) -> Option<(Vec<u8>, String)> {
        let der = unhex(&self.cert).filter(|d| parses(d) && device_public(d).is_ok() && crate::fsutil::hex(d) == self.cert)?;
        let fingerprint = cert_fingerprint(&der);
        Some((der, fingerprint))
    }

    fn check(&self) -> Result<()> {
        if self.device().is_none() {
            return bad("a pool's first device is named by its certificate, which holds an Ed25519 key");
        }
        if self.name.is_empty() || self.name.chars().count() > MOST_DEVICE_NAME || self.name.chars().any(char::is_control) {
            return bad("a pool's first device has a name of at most 40 characters");
        }
        Ok(())
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
    /// The device the pool is founded on, which the pool admits as its first member with `how: found`.
    pub first: First,
    /// Sorted by key.
    pub admins: Vec<AdminKey>,
    pub thresholds: Thresholds,
    pub holds: Holds,
    pub floors: Floors,
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
    pub fn new(name: &str, policy: Policy, project: &str, mut admins: Vec<AdminKey>, first: First, at_ms: u64) -> Charter {
        admins.sort_by(|a, b| a.key.cmp(&b.key));
        let floors = Floors::default_for(&admins).unwrap_or(Floors { routine: Level::Computer, sensitive: Level::Computer, governance: Level::Computer });
        Charter {
            v: VERSION, epoch: 1, name: name.into(), policy, mode: Mode::Admins, project: project.into(), first, admins, thresholds: Thresholds::default(),
            holds: Holds::default(), floors, recover_delay_days: 7, succession: None, from: None, at_ms,
        }
    }

    pub fn body(&self) -> Value {
        serde_json::to_value(self).unwrap_or(Value::Null)
    }

    /// The charter ``value`` holds: one of another version says so in one sentence, before a field of it is read.
    pub fn read(value: &Value) -> Result<Charter> {
        if value["v"].as_u64() != Some(u64::from(VERSION)) {
            return bad(OTHER_VERSION);
        }
        serde_json::from_value(value.clone()).map_err(|e| Error::Invalid(format!("that is not a charter: {}", e.to_string().chars().take(80).collect::<String>())))
    }

    fn hashed(&self, blank: impl Fn(&AdminKey) -> bool) -> String {
        let mut bare = self.clone();
        bare.admins.iter_mut().filter(|k| blank(k)).for_each(|k| k.acceptance = String::new());
        sha256_hex(canonical(&bare.body()).as_bytes())
    }

    /// The hash of this charter, 64 hex digits: what an act's `prev` names. The acceptances of the keys the pool was founded with
    /// are left out, so every copy of one founding charter is one charter whatever its keys signed with; a later key's acceptance
    /// is in the act that listed it.
    pub fn hash(&self) -> String {
        self.hashed(|k| k.since == 1)
    }

    /// The pool id this charter makes when it is the first: the first 32 digits of its anchor.
    pub fn pool_id(&self) -> String {
        self.anchor()[..32].to_string()
    }

    /// The hash of this charter with every acceptance blanked: what the founding keys accept.
    pub fn anchor(&self) -> String {
        self.hashed(|_| true)
    }

    /// The act that founds the pool of this charter: made of nothing but the charter, so every device that holds the charter makes the same one.
    pub fn found_act(&self) -> Act {
        Act {
            v: super::act::VERSION, pool: self.pool_id(), epoch: 1, kind: "found".into(), body: self.body(), at_ms: self.at_ms, not_after: self.at_ms.saturating_add(WINDOW_MS),
            nonce: self.anchor()[..32].into(), prev: String::new(), signatures: Vec::new(),
        }
    }

    pub fn admin(&self, key: &str) -> Option<&AdminKey> {
        self.admins.iter().find(|k| k.key == key)
    }

    /// Whether the admin key ``key`` counts for ``class``: an admin role key at or above the floor of the class.
    pub fn counts(&self, key: &AdminKey, class: Class) -> bool {
        key.role == Role::Admin && key.assurance >= self.floors.of(class)
    }

    /// The keys that count for ``class``.
    pub fn counting(&self, class: Class) -> impl Iterator<Item = &AdminKey> {
        self.admins.iter().filter(move |k| self.counts(k, class))
    }

    /// Whether the key ``public`` is listed and counts for ``class``.
    pub fn counts_key(&self, public: &str, class: Class) -> bool {
        self.admin(public).is_some_and(|k| self.counts(k, class))
    }

    /// Whether a listed admin key is at or above ``level``.
    pub fn holds_level(&self, level: Level) -> bool {
        self.admins.iter().any(|k| k.role == Role::Admin && k.assurance >= level)
    }

    /// How many distinct people hold a key that counts for ``class``.
    pub fn people(&self, class: Class) -> usize {
        self.counting(class).map(|k| k.person.as_str()).collect::<BTreeSet<_>>().len()
    }

    /// How many signers an act of ``class`` needs; governance acts take the sensitive threshold.
    pub fn needed(&self, class: Class) -> usize {
        match (class, self.thresholds.sensitive) {
            (Class::Routine, _) => self.thresholds.routine as usize,
            (_, 0) => self.people(class).clamp(1, 2),
            (_, n) => n as usize,
        }
    }

    fn counted(&self, class: Option<Class>, keys: &[&str]) -> usize {
        let held: Vec<&AdminKey> = self.admins.iter().filter(|k| keys.contains(&k.key.as_str()) && k.role == Role::Admin && class.is_none_or(|c| self.counts(k, c))).collect();
        match self.thresholds.unit {
            Unit::Person => held.iter().map(|k| k.person.as_str()).collect::<BTreeSet<_>>().len(),
            Unit::Key => held.iter().map(|k| k.key.as_str()).collect::<BTreeSet<_>>().len(),
        }
    }

    /// How many of ``keys`` count for ``class``: the admin keys of this charter among them at or above its floor, by person or by key.
    pub fn count(&self, class: Class, keys: &[&str]) -> usize {
        self.counted(Some(class), keys)
    }

    /// Like `count`, keys below every floor included: only the end of a delegate is signed by them.
    pub fn count_any(&self, keys: &[&str]) -> usize {
        self.counted(None, keys)
    }

    /// How many of the keys that count for ``class`` the thresholds can draw on, by person or by key.
    pub fn units(&self, class: Class) -> usize {
        match self.thresholds.unit {
            Unit::Person => self.people(class),
            Unit::Key => self.counting(class).count(),
        }
    }

    /// Whether the thresholds can be met by the keys listed, at the floor of each class.
    pub fn reachable(&self) -> bool {
        [Class::Routine, Class::Sensitive, Class::Governance].into_iter().all(|c| self.needed(c) <= self.units(c))
    }

    /// Whether this is a charter a pool can start from: the shape, the keys and each key's acceptance.
    pub fn check_founding(&self) -> Result<()> {
        if self.v != VERSION {
            return bad(OTHER_VERSION);
        }
        if self.epoch != 1 || self.mode != Mode::Admins || self.succession.is_some() || self.from.is_some() || self.at_ms == 0 {
            return bad("this build founds a pool of epoch 1 governed by admins, with no succession and no earlier pool");
        }
        if self.name.is_empty() || self.name.chars().count() > 40 || !self.name.chars().all(|c| c.is_alphanumeric() || matches!(c, ' ' | '.' | '_' | '-')) {
            return bad("a pool's name is at most 40 letters, digits, spaces and dots");
        }
        if !(self.project.is_empty() || (self.project.len() == 16 && self.project.bytes().all(|b| b.is_ascii_hexdigit()))) {
            return bad("a pool's project is a project key");
        }
        self.first.check()?;
        self.check_keys(1)?;
        let anchor = self.anchor();
        if !self.admins.iter().all(|k| k.accepted(&anchor, "")) {
            return bad("every admin key accepts the charter it is listed in");
        }
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

    /// The floors: none is `Weak`, and a listed admin key holds each, so the pool can act at every class.
    pub fn check_floors(&self) -> Result<()> {
        let f = &self.floors;
        if [f.routine, f.sensitive, f.governance].contains(&Level::Weak) {
            return bad("a floor is never a typed yes: a pool needs a key that counts");
        }
        if !self.admins.iter().any(|k| k.role == Role::Admin && k.assurance > Level::Weak) {
            return bad(NO_COUNTING_KEY);
        }
        if ![Class::Routine, Class::Sensitive, Class::Governance].into_iter().all(|c| self.holds_level(f.of(c))) {
            return bad("no listed key holds that level, so no change would count again");
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
        self.check_floors()?;
        if !self.reachable() {
            return bad("the thresholds need more people than the keys that count at these floors");
        }
        Ok(())
    }
}
