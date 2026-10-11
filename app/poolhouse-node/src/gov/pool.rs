//! One governed pool on disk: the acts it holds, when this device first saw each, and the state folded from them.
//!
//! `pools/<id>/` holds `charter.json` (the founding act), `acts.json` (every other act accepted, with the signatures merged),
//! `seen.json` (the awake time of the first sight of each act, and the awake total at the last checkpoint) and `refused.json`
//! (the last refusals, each with its reason). An act is accepted when its shape, pool, time and signatures are right and its
//! signers are keys this pool has listed; whether it takes effect is the fold's (`chain`, `roster`), recomputed from scratch
//! each time, so nothing a member says is believed twice.

use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::path::{Path, PathBuf};
use std::sync::Arc;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::error::{Error, Result};
use crate::fsutil::{private_dir, sha256_hex, write_atomic};
use crate::grants::spec::SKEW_MS;
use crate::membership::Device;

use super::act::{Act, SigKind};
use super::body::{self, Body};
use super::chain::{self, Chain, Seen};
use super::charter::Charter;
use super::clock::Awake;
use super::keys::{AdminKey, Role, Strength};
use super::roster::{self, Roster};
use super::{ADMIN_ACTS_PER_KEYS, HAVE_ACTS, MAX_ACTS, MAX_ADMIN_ACTS, MAX_ADMITS, MAX_REFUSALS, PAGE_ACTS, PAGE_BYTES, SETTLED};

const CHARTER: &str = "charter.json";
const ACTS: &str = "acts.json";
const SEEN: &str = "seen.json";
const REFUSED: &str = "refused.json";

/// An act that was not accepted, and why.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Refusal {
    pub id: String,
    pub kind: String,
    pub why: String,
}

/// What became of an act handed to the pool.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Ingest {
    Accepted,
    /// Already held, with no signature it did not have.
    Known,
    /// Already held; signatures were added.
    Merged,
    /// Held back until the epoch it names, or the device that signed it, is known.
    Waiting,
    Refused(String),
}

/// Where an act stands.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ActStatus {
    Effective,
    Pending,
    Short(usize, usize),
    Void(String),
    Stale,
    Unknown,
}

pub struct State {
    pub chain: Chain,
    pub roster: Roster,
}

/// What a device that joins is handed (`GovPool::join_payload`).
pub struct JoinPayload {
    pub found: Act,
    pub settled: Vec<Act>,
    pub delegates: Vec<Act>,
    pub acts: Vec<Act>,
}

pub struct GovPool {
    dir: PathBuf,
    pub id: String,
    pub genesis: Charter,
    found: Act,
    acts: BTreeMap<String, Act>,
    seen: Seen,
    /// When each admin-set act first had all the signatures it needs on this device; its hold runs from then.
    ready: Seen,
    refusals: VecDeque<Refusal>,
    pub clock: Arc<Awake>,
    state: State,
    folded_at: i64,
    saved_at: i64,
}

fn bad<T>(why: &str) -> Result<T> {
    Err(Error::Invalid(why.into()))
}

fn read_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<Option<T>> {
    match std::fs::read(path) {
        Ok(bytes) => serde_json::from_slice(&bytes).map(Some).map_err(|e| Error::Damaged(format!("{} unreadable: {e}", path.display()))),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(e.into()),
    }
}

/// The charter a founding act carries, when the act is one a pool can start from.
pub fn founding(act: &Act) -> Result<Charter> {
    act.check_shape()?;
    let Ok(Body::Found(charter)) = body::parse(act) else { return bad("a pool starts from a founding act") };
    charter.check_founding()?;
    if act.kind != "found" || act.epoch != 1 || act.pool != charter.pool_id() || !act.prev.is_empty() || !act.signatures.is_empty() {
        return bad("a founding act names the pool its charter makes and carries no signature of its own");
    }
    Ok(*charter)
}

impl GovPool {
    fn assemble(dir: PathBuf, found: Act, clock: Arc<Awake>) -> Result<GovPool> {
        let genesis = founding(&found)?;
        let id = genesis.pool_id();
        let empty = Chain::new(&genesis);
        let mut pool = GovPool {
            dir, id, genesis, found, acts: BTreeMap::new(), seen: Seen::new(), ready: Seen::new(), refusals: VecDeque::new(), clock,
            state: State { chain: empty, roster: Roster::default() }, folded_at: 0, saved_at: 0,
        };
        pool.refold();
        Ok(pool)
    }

    /// Start a pool from its founding act under ``root`` (`pools/<id>/`).
    pub fn create(root: &Path, found: Act) -> Result<GovPool> {
        let id = founding(&found)?.pool_id();
        let dir = root.join("pools").join(&id);
        private_dir(&root.join("pools"))?;
        private_dir(&dir)?;
        let mut pool = GovPool::assemble(dir, found, Arc::new(Awake::new(0)))?;
        write_atomic(&pool.dir.join(CHARTER), &serde_json::to_vec(&pool.found)?)?;
        pool.save()?;
        Ok(pool)
    }

    /// Open the pool kept in ``dir``.
    pub fn load(dir: &Path) -> Result<GovPool> {
        let found: Act = read_json(&dir.join(CHARTER))?.ok_or_else(|| Error::Damaged("a pool without its charter".into()))?;
        let saved: Option<Value> = read_json(&dir.join(SEEN))?;
        let awake = saved.as_ref().and_then(|v| v["awake_ms"].as_i64()).unwrap_or(0);
        let mut pool = GovPool::assemble(dir.into(), found, Arc::new(Awake::new(awake)))?;
        let acts: Vec<Act> = read_json::<Value>(&dir.join(ACTS))?.and_then(|v| serde_json::from_value(v["acts"].clone()).ok()).unwrap_or_default();
        pool.acts = acts.into_iter().filter(|a| a.check_shape().is_ok() && a.pool == pool.id).map(|a| (a.id(), a)).collect();
        pool.seen = saved.as_ref().and_then(|v| serde_json::from_value(v["seen"].clone()).ok()).unwrap_or_default();
        pool.ready = saved.and_then(|v| serde_json::from_value(v["ready"].clone()).ok()).unwrap_or_default();
        pool.refusals = read_json(&dir.join(REFUSED))?.unwrap_or_default();
        pool.refold();
        Ok(pool)
    }

    /// Write what changed to disk, with the awake total as of now.
    pub fn save(&mut self) -> Result<()> {
        self.saved_at = self.clock.now_ms();
        write_atomic(&self.dir.join(ACTS), &serde_json::to_vec(&json!({"acts": self.acts.values().collect::<Vec<_>>()}))?)?;
        write_atomic(&self.dir.join(SEEN), &serde_json::to_vec(&json!({"awake_ms": self.clock.now_ms(), "seen": self.seen, "ready": self.ready}))?)?;
        write_atomic(&self.dir.join(REFUSED), &serde_json::to_vec(&self.refusals)?)
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    /// Fold the state again as it stands now.
    pub fn refold(&mut self) {
        let now = self.clock.now_ms();
        let chain = chain::build(&self.genesis, &self.id, &self.acts, (&self.seen, &self.ready), now);
        for id in &chain.validated {
            self.ready.entry(id.clone()).or_insert(now);
        }
        let roster = roster::build(&chain, &self.acts, &self.seen, now);
        self.state = State { chain, roster };
        self.folded_at = now;
    }

    /// Fold again when a second or more of awake time has passed since the last fold, so a hold that has run takes effect; whether
    /// it did.
    pub fn tick(&mut self) -> bool {
        let due = self.clock.now_ms().saturating_sub(self.folded_at) >= 1000;
        if due {
            self.refold();
        }
        due
    }

    /// Whether a minute of awake time has passed since the total was written, so a restart gives back little of it.
    pub fn checkpoint_due(&self) -> bool {
        self.clock.now_ms().saturating_sub(self.saved_at) >= 60_000
    }

    pub fn state(&self) -> &State {
        &self.state
    }

    pub fn found_act(&self) -> &Act {
        &self.found
    }

    pub fn acts(&self) -> &BTreeMap<String, Act> {
        &self.acts
    }

    pub fn refusals(&self) -> Vec<Refusal> {
        self.refusals.iter().cloned().collect()
    }

    /// The members as the node's pool record holds them.
    pub fn devices(&self) -> BTreeMap<String, Device> {
        self.state.roster.devices.clone()
    }

    /// The charter and hash a new act follows.
    pub fn context(&self) -> (u64, String) {
        let latest = self.state.chain.latest();
        (latest.n, latest.hash.clone())
    }

    /// An act of ``kind`` with no signature yet, following the charter now in force.
    pub fn new_act(&self, kind: &str, body: Value, at_ms: u64) -> Result<Act> {
        let (epoch, prev) = self.context();
        Act::new(&self.id, epoch, kind, body, at_ms, &prev)
    }

    fn refuse(&mut self, act: &Act, why: &str) -> Ingest {
        self.refusals.push_back(Refusal { id: act.id(), kind: act.kind.chars().take(24).collect(), why: why.into() });
        while self.refusals.len() > MAX_REFUSALS {
            self.refusals.pop_front();
        }
        Ingest::Refused(why.into())
    }

    /// Every public key this pool has listed, in any epoch.
    fn listed(&self, key: &str) -> bool {
        self.state.chain.epochs.iter().any(|e| e.charter.admin(key).is_some())
    }

    fn cert_of(&self, fingerprint: &str) -> Option<Vec<u8>> {
        self.state.roster.devices.get(fingerprint).and_then(|d| d.der())
    }

    /// The kinds of signature each kind of act takes, and what must be true of its signers.
    fn signers_fit(&self, act: &Act) -> std::result::Result<(), String> {
        let (admin, device) = (act.admin_keys(), act.device_keys());
        let named = self.state.chain.state_for(act).map(|e| &e.charter);
        if named.is_some_and(|c| !admin.is_empty() && admin.iter().all(|k| c.admin(k).is_none())) {
            return Err("none of the keys that signed is an admin of this pool at the epoch the act names".into());
        }
        let counts = |key: &&str| named.is_some_and(|c| c.admin(key).is_some_and(|k| k.role == Role::Admin && k.strength == Strength::Strong));
        match act.kind.as_str() {
            "admit" if admin.is_empty() && device.is_empty() => Err("an admit is signed".into()),
            "leave" if !admin.is_empty() || device.is_empty() => Err("a device leaves by its own signature".into()),
            "admit" if !admin.is_empty() && !admin.iter().any(counts) => Err("a weak key, or the recovery key, admits no device".into()),
            "admit" if admin.is_empty() && !self.may_admit_by_device(act) => Err("a member that is not a delegate admits nobody in a secure pool, and in an open one only a device it checked is on its network".into()),
            "admit" | "leave" => Ok(()),
            _ if !device.is_empty() => Err("only a device's own act is signed by a device".into()),
            "delegate_end" if !admin.is_empty() => Ok(()),
            "veto" if admin.iter().any(|k| named.is_some_and(|c| c.admin(k).is_some_and(|x| x.strength == Strength::Strong))) => Ok(()),
            "veto" => Err("a key that is not strong cannot veto".into()),
            _ if admin.is_empty() => Err("this act is signed by an admin".into()),
            _ if admin.iter().all(|k| named.is_some_and(|c| c.admin(k).is_some_and(|x| x.role == Role::Recovery))) => Err("the recovery key signs no immediate act".into()),
            _ if admin.iter().any(counts) => Ok(()),
            _ => Err("a weak key counts toward nothing but the end of a delegate".into()),
        }
    }

    /// Whether a device may sign this admit now: a live delegate may; in an open pool a member may when it says `segment`.
    fn may_admit_by_device(&self, act: &Act) -> bool {
        let open = self.state.chain.latest().charter.policy == crate::membership::Policy::Open && act.body["via"] == "segment";
        open || (act.body["via"].as_str().unwrap_or("").is_empty() && act.device_keys().iter().any(|d| self.state.roster.delegates.contains_key(*d)))
    }

    fn verify_signatures(&self, act: &Act) -> std::result::Result<(), Ingest> {
        for sig in &act.signatures {
            let der = match sig.kind {
                SigKind::Admin if !self.listed(&sig.key) => return Err(Ingest::Refused("that key is not an admin of this pool".into())),
                SigKind::Admin => None,
                SigKind::Device => match self.cert_of(&sig.key) {
                    Some(d) => Some(d),
                    None => return Err(Ingest::Waiting),
                },
            };
            if !act.verifies(sig, der.as_deref()) {
                return Err(Ingest::Refused("a signature does not verify".into()));
            }
        }
        Ok(())
    }

    fn room_for(&mut self, act: &Act) -> std::result::Result<(), String> {
        if self.acts.len() >= MAX_ACTS {
            return Err("this pool holds as many acts as it will".into());
        }
        let count = |pool: &GovPool, f: fn(&Act) -> bool| pool.acts.values().filter(|a| f(a)).count();
        if act.kind == "admit" && count(self, |a| a.kind == "admit") >= MAX_ADMITS {
            return Err("this pool holds as many admits as it will".into());
        }
        if act.is_chain() {
            self.room_for_admin_act(act)?;
        }
        Ok(())
    }

    /// The admin-set acts not yet in effect are at most 256, and the same keys may have signed at most 16 of them: a key that
    /// floods the pool with acts replaces its own oldest, and never the acts of other keys.
    fn room_for_admin_act(&mut self, act: &Act) -> std::result::Result<(), String> {
        let keys = act.admin_keys();
        let live = |pool: &GovPool| -> Vec<(i64, String, bool)> {
            pool.acts.iter().filter(|(id, a)| a.is_chain() && !pool.state.chain.applied.contains(*id))
                .map(|(id, a)| (pool.seen.get(id).copied().unwrap_or(0), id.clone(), a.admin_keys().iter().any(|k| keys.contains(k)))).collect()
        };
        let held = live(self);
        if held.len() >= MAX_ADMIN_ACTS {
            return Err("this pool holds as many acts that change its admins as it will".into());
        }
        let mine: Vec<&(i64, String, bool)> = held.iter().filter(|(_, _, shared)| *shared).collect();
        if mine.len() >= ADMIN_ACTS_PER_KEYS {
            let oldest = mine.iter().min().map(|(_, id, _)| id.clone());
            oldest.into_iter().for_each(|id| self.forget(&id));
        }
        Ok(())
    }

    /// Whether the key with this fingerprint is listed in the charter now in force and accepted being listed: what a grant scoped to
    /// the pools an admin key administers (`admin:<fingerprint>`) matches.
    pub fn admin_scope(&self, fingerprint: &str) -> bool {
        let anchor = |k: &AdminKey| if k.since == 1 { self.genesis.anchor() } else { self.id.clone() };
        self.state.chain.latest().charter.admins.iter().any(|k| k.fingerprint() == fingerprint && k.accepted(&anchor(k)))
    }

    fn forget(&mut self, id: &str) {
        self.acts.remove(id);
        self.seen.remove(id);
        self.ready.remove(id);
    }

    /// Hand one act to the pool, ``wall_ms`` being this device's wall clock (read only to refuse an act dated in the future).
    pub fn ingest(&mut self, act: Act, wall_ms: u64) -> Ingest {
        let out = self.take(act, wall_ms, false);
        self.refold();
        out
    }

    fn take(&mut self, act: Act, wall_ms: u64, settled: bool) -> Ingest {
        let id = act.id();
        if let Err(e) = act.check_shape() {
            return self.refuse(&act, &e.to_string());
        }
        if act.pool != self.id {
            return self.refuse(&act, "that act is for another pool");
        }
        if act.kind == "found" {
            return if id == self.found.id() { Ingest::Known } else { self.refuse(&act, "this pool was founded by another act") };
        }
        if act.at_ms > wall_ms.saturating_add(SKEW_MS) {
            return self.refuse(&act, "that act is dated in the future");
        }
        if let Err(e) = body::parse(&act) {
            return self.refuse(&act, &e.to_string());
        }
        if self.acts.get(&id).is_some_and(|held| act.signatures.iter().all(|s| held.signatures.iter().any(|h| h.kind == s.kind && h.key == s.key))) {
            return Ingest::Known;
        }
        match self.verify_signatures(&act) {
            Ok(()) => {}
            Err(Ingest::Waiting) => return Ingest::Waiting,
            Err(Ingest::Refused(why)) => return self.refuse(&act, &why),
            Err(other) => return other,
        }
        if let Some(held) = self.acts.get_mut(&id) {
            let added = act.signatures.iter().filter(|s| held.add_signature((*s).clone())).count();
            return if added > 0 { Ingest::Merged } else { Ingest::Known };
        }
        if act.epoch > self.state.chain.latest().n {
            return Ingest::Waiting;
        }
        if self.state.chain.state_for(&act).is_none() {
            return self.refuse(&act, "that act follows a charter this pool never had");
        }
        if let Err(why) = self.signers_fit(&act).and_then(|()| self.room_for(&act)) {
            return self.refuse(&act, &why);
        }
        self.seen.insert(id.clone(), if settled { SETTLED } else { self.clock.now_ms() });
        if settled {
            self.ready.insert(id.clone(), SETTLED);
        }
        self.acts.insert(id, act);
        Ingest::Accepted
    }

    /// Hand many acts to the pool, in the order that lets each find the keys and devices it needs; the acts held back are tried
    /// again as the others are accepted. ``settled`` marks acts taken inside a join this device made. The pool is folded again
    /// only when an act that follows needs what an accepted one changed.
    pub fn ingest_many(&mut self, acts: Vec<Act>, wall_ms: u64, settled: bool) -> Vec<(String, Ingest)> {
        let mut queue: Vec<(String, Act)> = acts.into_iter().map(|a| (a.id(), a)).collect();
        queue.sort_by_key(|(id, a)| (a.epoch, !a.is_chain(), a.device_keys().len(), a.at_ms, id.clone()));
        let mut done: BTreeMap<String, Ingest> = BTreeMap::new();
        let (mut chain_moved, mut roster_moved) = (false, false);
        for _ in 0..8 {
            let (mut again, mut progress) = (Vec::new(), false);
            for (id, act) in queue {
                if chain_moved || (roster_moved && !act.device_keys().is_empty()) {
                    self.refold();
                    (chain_moved, roster_moved) = (false, false);
                }
                let out = self.take(act.clone(), wall_ms, settled);
                if matches!(out, Ingest::Accepted | Ingest::Merged) {
                    progress = true;
                    roster_moved = true;
                    chain_moved |= act.is_chain() || matches!(act.kind.as_str(), "cancel" | "veto");
                }
                if out == Ingest::Waiting {
                    again.push((id.clone(), act));
                }
                done.insert(id, out);
            }
            queue = again;
            if queue.is_empty() || !progress {
                break;
            }
        }
        self.refold();
        done.into_iter().collect()
    }

    /// Where the act ``id`` stands.
    pub fn status(&self, id: &str) -> ActStatus {
        let (c, r) = (&self.state.chain, &self.state.roster);
        if c.applied.contains(id) {
            return ActStatus::Effective;
        }
        if c.pending.as_ref().is_some_and(|p| p.acts.iter().any(|a| a == id)) {
            return ActStatus::Pending;
        }
        if let Some(why) = c.void.get(id).or_else(|| r.void.get(id)) {
            return ActStatus::Void(why.clone());
        }
        if let Some((have, need, _)) = c.short.get(id).or_else(|| r.short.get(id)) {
            return ActStatus::Short(*have, *need);
        }
        match self.acts.get(id) {
            Some(a) if a.is_chain() => ActStatus::Stale,
            Some(_) => ActStatus::Effective,
            None => ActStatus::Unknown,
        }
    }

    /// What this device holds of an act, as the member it asks names it: the id of the act and a tag of who has signed it, so a
    /// signature that arrives later on an act already held is still passed on.
    pub fn token(id: &str, act: &Act) -> String {
        let mut keys: Vec<String> = act.signatures.iter().map(|s| format!("{:?}{}", s.kind, s.key)).collect();
        keys.sort();
        format!("{id}{}", &sha256_hex(keys.join(",").as_bytes())[..16])
    }

    /// The tokens of the acts held, most recently seen first, at most ``HAVE_ACTS``.
    pub fn have(&self) -> Vec<String> {
        let mut held: Vec<(i64, &String)> = self.acts.keys().map(|id| (self.seen.get(id).copied().unwrap_or(0), id)).collect();
        held.sort_by(|a, b| b.cmp(a));
        let mut out: Vec<String> = held.into_iter().take(HAVE_ACTS - 1).filter_map(|(_, id)| self.acts.get(id).map(|a| GovPool::token(id, a))).collect();
        out.push(GovPool::token(&self.found.id(), &self.found));
        out
    }

    fn weight(act: &Act) -> usize {
        serde_json::to_vec(act).map_or(usize::MAX, |b| b.len())
    }

    /// Page ``page`` of the acts this pool holds that ``have`` does not name: at most 256 acts and 2 MiB a page, the acts that
    /// change the admins first. Whether another page follows.
    pub fn page(&self, have: &BTreeSet<String>, page: usize) -> (Vec<Act>, bool) {
        let mut list: Vec<&Act> = self.acts.iter().filter(|(id, a)| !have.contains(&GovPool::token(id, a))).map(|(_, a)| a).collect();
        list.sort_by_key(|a| (a.epoch, !a.is_chain(), a.device_keys().len(), a.at_ms, a.id()));
        let mut all: Vec<&Act> = Vec::new();
        if !have.contains(&GovPool::token(&self.found.id(), &self.found)) {
            all.push(&self.found);
        }
        all.extend(list);
        let mut pages: Vec<Vec<Act>> = vec![Vec::new()];
        let mut bytes = 0;
        for act in all {
            let w = GovPool::weight(act);
            let last = pages.last_mut().expect("one page at least");
            if last.len() >= PAGE_ACTS || bytes + w > PAGE_BYTES {
                pages.push(Vec::new());
                bytes = 0;
            }
            bytes += w;
            pages.last_mut().expect("one page at least").push(act.clone());
        }
        let more = page + 1 < pages.len();
        (pages.get(page).cloned().unwrap_or_default(), more)
    }

    /// What a device that joins (``joiner``, by the fingerprint of its certificate) is handed: the founding act, the admin-set acts
    /// that are in effect here (their holds were over before it arrived), the acts of the delegates that are live here (which run
    /// from the moment the joiner first sees them), the act that admitted the joiner and the other acts that stand, as many as fit
    /// in ``budget`` bytes.
    pub fn join_payload(&self, budget: usize, joiner: &str) -> JoinPayload {
        let (chain, roster) = (&self.state.chain, &self.state.roster);
        let settled: Vec<Act> = chain.applied.iter().filter_map(|id| self.acts.get(id)).cloned().collect();
        let live = |a: &Act| a.kind == "delegate" && a.body["fingerprint"].as_str().is_some_and(|d| roster.delegates.contains_key(d));
        let delegates: Vec<Act> = self.acts.values().filter(|a| live(a)).take(PAGE_ACTS).cloned().collect();
        let mut acts: Vec<Act> = roster.admitted_by.get(joiner).and_then(|id| self.acts.get(id)).cloned().into_iter().collect();
        let mut bytes = acts.iter().map(GovPool::weight).sum::<usize>();
        let standing = |id: &String, a: &Act| !a.is_chain() && !matches!(a.kind.as_str(), "cancel" | "veto" | "delegate" | "delegate_end") && self.status(id) == ActStatus::Effective;
        for a in self.acts.iter().filter(|(id, a)| standing(id, a)).map(|(_, a)| a) {
            bytes += GovPool::weight(a);
            if bytes > budget || acts.len() >= PAGE_ACTS {
                break;
            }
            acts.push(a.clone());
        }
        JoinPayload { found: self.found.clone(), settled, delegates, acts }
    }
}
