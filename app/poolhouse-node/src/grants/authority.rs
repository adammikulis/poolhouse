//! Who may sign what: the one place that turns the charter in force and this device's keys into the authority a signature has.
//!
//! An ungoverned device (a pool of one) counts any key enrolled on it. In a governed pool an action that reaches beyond this device
//! (`spec::pool_wide`) counts only when a key the charter in force lists as a strong admin key signed it, the key accepted being
//! listed, and this device knows the key as a phone's. A revocation counts from any admin-role key the pool listed in any epoch.
//! The snapshot is built from the node at each call and kept nowhere.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::Mutex;

use crate::gov::charter::{Charter, Mode};
use crate::fsutil::sha256_hex;
use crate::gov::keys::{Role, Strength};
use crate::node::Node;

use super::person::verifies_with;
use super::spec::pool_wide;

const NOT_LISTED: &str = "signed by a key that is not an admin of this pool now";
const RECOVERY: &str = "the recovery key signs no grant";
const WEAK: &str = "signed by a key that is not strong";
const UNACCEPTED: &str = "that admin key never accepted being listed";
const UNKNOWN_PHONE: &str = "this device does not know that key as a phone's";
const DISTRUSTED: &str = "that admin key is distrusted";
const NOT_ADMINS: &str = "this pool is not run by its admins";
const NO_STRONG: &str = "this pool lists no strong admin key";
const BAD_SIGNATURE: &str = "the signature is not that admin key's";
/// Why a key the charter in force lists is not removed from this device by the device's own acts.
pub const LISTED: &str = "That key is an admin key of this pool: it leaves through an admin removal.";
/// Why a grant row for a pool-wide action carries no approval.
pub const NO_APPROVAL: &str = "a pool's grants come from an approval on a phone";

/// A key as the charter holds it: its algorithm and public half.
#[derive(Clone)]
struct Material {
    alg: String,
    public: String,
}

/// How many verdicts of signature checks are remembered.
const MEMO_MOST: usize = 8192;

impl Material {
    /// Whether ``sig`` over ``bytes`` is this key's, remembering the verdict: a check is a pure function of the key, the bytes and the signature.
    fn signed(&self, bytes: &[u8], sig: &str) -> bool {
        static MEMO: Mutex<BTreeMap<String, bool>> = Mutex::new(BTreeMap::new());
        let id = sha256_hex(&[self.alg.as_bytes(), self.public.as_bytes(), sig.as_bytes(), bytes].join(&0u8));
        if let Some(verdict) = MEMO.lock().ok().and_then(|m| m.get(&id).copied()) {
            return verdict;
        }
        let verdict = verifies_with(&self.alg, &self.public, bytes, sig);
        if let Ok(mut memo) = MEMO.lock() {
            if memo.len() >= MEMO_MOST {
                memo.clear();
            }
            memo.insert(id, verdict);
        }
        verdict
    }
}

#[derive(Default)]
struct Pool {
    /// Why no key can sign a grant of the pool, when the charter itself rules it out.
    closed: Option<&'static str>,
    /// The fingerprints the charter in force lists, with any role.
    listed: BTreeSet<String>,
    /// The keys that stand as strong admin keys of the charter in force, whether or not this device knows them as a phone's.
    stands: BTreeMap<String, Material>,
    /// The keys of `stands` this device knows as a phone's: the authority keys.
    issuers: BTreeMap<String, Material>,
    /// Why each key the charter in force lists is not an authority key.
    barred: BTreeMap<String, &'static str>,
    /// The admin-role keys listed in any epoch.
    narrowers: BTreeMap<String, Material>,
}

/// Why no key can sign a grant of a pool run by ``charter``, when the charter itself rules it out: its members run the pool, or it lists no strong
/// admin key.
pub fn closed_reason(charter: &Charter) -> Option<&'static str> {
    if charter.mode != Mode::Admins {
        Some(NOT_ADMINS)
    } else if charter.weak_only() {
        Some(NO_STRONG)
    } else {
        None
    }
}

/// What a signature counts for on this node at this moment.
pub struct Authority {
    pool: Option<Pool>,
}

impl Authority {
    /// The authority of the node now: the charter in force and the keys this device holds.
    pub fn of(node: &Node) -> Authority {
        let Some(gov) = node.members.gov() else { return Authority { pool: None } };
        let charter = &gov.state().chain.latest().charter;
        let accepted = gov.authority_keys();
        let distrusted = super::distrust::set(node);
        let mut pool = Pool { closed: closed_reason(charter), ..Pool::default() };
        for k in &charter.admins {
            let (fp, material) = (k.fingerprint(), Material { alg: k.alg.name().into(), public: k.key.clone() });
            pool.listed.insert(fp.clone());
            let bar = if k.role == Role::Recovery {
                Some(RECOVERY)
            } else if k.strength != Strength::Strong {
                Some(WEAK)
            } else if !accepted.iter().any(|a| a.key == k.key) {
                Some(UNACCEPTED)
            } else if distrusted.contains(&fp) {
                Some(DISTRUSTED)
            } else {
                None
            };
            if let Some(why) = bar {
                pool.barred.insert(fp, why);
                continue;
            }
            if node.persons.get(&fp).is_some_and(|p| p.phone && p.alg == material.alg && p.public == material.public) {
                pool.issuers.insert(fp.clone(), material.clone());
            } else {
                pool.barred.insert(fp.clone(), UNKNOWN_PHONE);
            }
            pool.stands.insert(fp, material);
        }
        pool.narrowers = gov.narrowing_keys().into_iter().map(|k| (k.fingerprint(), Material { alg: k.alg.name().into(), public: k.key.clone() })).collect();
        Authority { pool: Some(pool) }
    }

    /// Whether this device is in a pool.
    pub fn governed(&self) -> bool {
        self.pool.is_some()
    }

    /// Whether a grant of ``action`` needs an admin key of the pool: the device is governed and the action reaches beyond it.
    pub fn pool_wide(&self, action: &str) -> bool {
        self.pool.is_some() && pool_wide(action)
    }

    /// Why ``sig`` over ``bytes`` by ``person`` makes no grant of the pool, or `None` when it does.
    pub fn issue_refusal(&self, person: &str, bytes: &[u8], sig: &str) -> Option<&'static str> {
        let pool = self.pool.as_ref()?;
        if let Some(why) = pool.closed {
            return Some(why);
        }
        match pool.issuers.get(person) {
            Some(m) if m.signed(bytes, sig) => None,
            Some(_) => Some(BAD_SIGNATURE),
            None => Some(pool.barred.get(person).copied().unwrap_or(NOT_LISTED)),
        }
    }

    /// Whether ``sig`` over ``bytes`` is the signature of an admin-role key the pool listed in any epoch, which ends a grant of the pool.
    pub fn narrows(&self, person: &str, bytes: &[u8], sig: &str) -> bool {
        self.pool.as_ref().is_some_and(|p| p.narrowers.get(person).is_some_and(|m| m.signed(bytes, sig)))
    }

    /// Whether the key ``fingerprint`` may decide a request: any key on an ungoverned device, an authority key of the pool otherwise.
    pub fn decides(&self, fingerprint: &str) -> bool {
        self.pool.as_ref().is_none_or(|p| p.closed.is_none() && p.issuers.contains_key(fingerprint))
    }

    /// Whether the charter in force lists ``fingerprint``, with any role.
    pub fn lists(&self, fingerprint: &str) -> bool {
        self.pool.as_ref().is_some_and(|p| p.listed.contains(fingerprint))
    }

    /// Whether the key ``public`` (fingerprint ``fingerprint``) stands as a strong admin key of the charter in force, so a phone that holds it is
    /// an authority key once this device enrols it.
    pub fn would_hold(&self, fingerprint: &str, public: &str) -> bool {
        self.pool.as_ref().is_some_and(|p| p.closed.is_none() && p.stands.get(fingerprint).is_some_and(|m| m.public == public))
    }

    /// The fingerprints of the authority keys of the pool now.
    pub fn authority(&self) -> Vec<String> {
        self.pool.as_ref().filter(|p| p.closed.is_none()).map_or_else(Vec::new, |p| p.issuers.keys().cloned().collect())
    }

    /// Whether any authority key is held on this device.
    pub fn any(&self) -> bool {
        self.pool.as_ref().is_some_and(|p| p.closed.is_none() && !p.issuers.is_empty())
    }

    /// Whether ``fingerprint`` is a key whose revocation of a grant of the pool counts.
    pub fn may_revoke(&self, fingerprint: &str) -> bool {
        self.pool.as_ref().is_none_or(|p| p.narrowers.contains_key(fingerprint))
    }
}
