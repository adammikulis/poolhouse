//! The node's part of governance: founding a pool, admitting, removing, leaving, and the acts a person's key signs.
//!
//! An agent asks; the signature of a person's key (put to a `Signer`) is what makes an act true for everyone. A device signs by its
//! own key only where the table of acts says so: an `admit` of an open pool's member or of a live delegate, and its own `leave`.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

use ed25519_dalek::{Signer as _, SigningKey};
use serde_json::{json, Value};

use crate::cert::{cert_fingerprint, valid_fingerprint};
use crate::error::{Error, Result};
use crate::fsutil::hex;
use crate::grants::spec::SKEW_MS;
use crate::membership::{Device, Policy, Pool, Status};
use crate::node::Node;
use crate::poolops::wall_ms;

use super::act::{Act, ActSig, SigKind};
use super::charter::{Charter, Class, Unit};
use super::describe::{describe, quoted};
use super::keys::{Alg, AdminKey, Role, Strength};
use super::pool::{ActStatus, GovPool, Ingest};
use super::signer::{KeyBinding, Signer, SignerKey};

const MOST_REPLIES: usize = 8;
/// How long a person's no to a kind of act is respected before the same kind is asked again, and the window in which a person is
/// asked at most `MOST_ASKS` times: requests do not go on until the person accepts from exhaustion.
const COOLDOWN: std::time::Duration = std::time::Duration::from_secs(10 * 60);
const MOST_ASKS: usize = 12;

fn no_pool() -> Error {
    Error::say("denied", "This device is not in a pool yet; make one, or join one.", "approval", "ask-owner")
}

fn unknown_epoch() -> Error {
    declined("That act follows a charter this device does not hold.")
}

fn no_signer() -> Error {
    Error::say("denied", "I need your OK for that, and nothing here can ask you yet.", "approval", "ask-owner")
}

fn not_admin() -> Error {
    Error::say("denied", "None of the keys I can ask is an admin of this pool.", "approval", "ask-owner")
}

fn declined(why: &str) -> Error {
    Error::say("denied", why, "none", "stop")
}

/// How many signers an act of ``kind`` needs: a class, or one strong admin (`None`).
fn class_of(kind: &str) -> Result<Option<Class>> {
    match kind {
        "admit" | "veto" => Ok(None),
        "remove" | "cancel" | "delegate_end" => Ok(Some(Class::Routine)),
        "admin_add" | "admin_remove" | "set_threshold" | "set_holds" | "set_mode" | "delegate" => Ok(Some(Class::Sensitive)),
        _ => Err(Error::Invalid("a person's key signs an admit, a removal, a change of admins or thresholds, a delegate, a cancel or a veto".into())),
    }
}

/// The keys of ``signer`` that count in ``charter``, one per person (or per key under `unit: key`), at most ``need``.
fn keys_for(signer: &dyn Signer, charter: &Charter, need: usize, weak_ok: bool, skip: &[&str]) -> Vec<SignerKey> {
    let mut seen: BTreeSet<String> = BTreeSet::new();
    let mut chosen = Vec::new();
    for key in signer.keys().into_iter().filter(|k| !skip.contains(&k.public.as_str())) {
        let Some(listed) = charter.admin(&key.public).filter(|l| l.role == Role::Admin && (weak_ok || charter.is_strong(&key.public))) else { continue };
        let who = if charter.thresholds.unit == Unit::Person { listed.person.clone() } else { key.public.clone() };
        if seen.len() < need && seen.insert(who) {
            chosen.push(key);
        }
    }
    chosen
}

fn sign_with(act: &mut Act, signer: &dyn Signer, keys: &[SignerKey], summary: &str) -> Result<()> {
    for key in keys {
        let sig = signer.sign(&key.public, &act.sign_bytes(), summary)?;
        act.add_signature(ActSig { kind: SigKind::Admin, key: key.public.clone(), sig });
    }
    Ok(())
}

fn device_sign(act: &mut Act, key: &SigningKey, fingerprint: &str) {
    let sig = hex(&key.sign(&act.sign_bytes()).to_bytes());
    act.add_signature(ActSig { kind: SigKind::Device, key: fingerprint.into(), sig });
}

/// The founding act of a call: the act itself, or the charter a person's keys accepted (the act is made of it); not both, not neither.
pub fn founding_act(act: Option<&Value>, charter: Option<&Value>) -> Result<Value> {
    match (act, charter) {
        (Some(act), None) => Ok(act.clone()),
        (None, Some(charter)) => {
            let c: Charter = serde_json::from_value(charter.clone()).map_err(|e| Error::Invalid(format!("that is not a charter: {}", e.to_string().chars().take(80).collect::<String>())))?;
            Ok(json!(c.found_act()))
        }
        _ => Err(Error::Invalid("a pool is founded by an act or by a charter, one of them".into())),
    }
}

/// The founding act in ``act`` and the charter it carries, as far as the charter itself says: nothing of this device is read.
fn charter_of(act: &Value) -> Result<(Act, Charter)> {
    let found: Act = serde_json::from_value(act.clone()).map_err(|e| Error::Invalid(format!("that is not an act: {}", e.to_string().chars().take(80).collect::<String>())))?;
    let charter = super::pool::founding(&found).map_err(|e| declined(&e.to_string()))?;
    Ok((found, charter))
}

/// The name of the pool an act a person's keys signed elsewhere would found, for the grant that lets a session found it; what this device
/// derives about the keys is not read, so a session that holds no grant learns none of it.
pub fn founding_name(act: &Value) -> Result<String> {
    Ok(charter_of(act)?.1.name)
}

const NO_STRONG_KEY: &str = "A pool needs a strong key, one that asks for your presence each time it signs, and that charter lists none.";
const UNCONFIRMED_STRONG_KEY: &str = "That charter calls a key strong that is not a phone's key on this device, so I will not make that pool.";

fn reply(g: &GovPool, id: &str, kept: Ingest) -> Result<Value> {
    if let Ingest::Refused(why) = kept {
        return Err(declined(&why));
    }
    let (status, have, need, why) = match g.status(id) {
        ActStatus::Effective => ("effective", 0, 0, String::new()),
        ActStatus::Pending => ("pending", 0, 0, "it takes effect when its hold has run".into()),
        ActStatus::Short(h, n) => ("needs-signers", h, n, format!("it needs {n} signers and has {h}")),
        ActStatus::Void(why) => ("void", 0, 0, why),
        ActStatus::Stale => ("stale", 0, 0, "another act has taken the place it followed".into()),
        ActStatus::Unknown => ("waiting", 0, 0, "it waits for the acts it follows".into()),
    };
    Ok(json!({"act": id, "status": status, "have": have, "need": need, "why": why}))
}

/// Make ``act`` by ``kind`` and ``body``, sign it with the keys ``signer`` holds that count, and hand it to the pool.
fn propose(g: &mut GovPool, signer: Option<&Arc<dyn Signer>>, kind: &str, body: Value, wall: u64) -> Result<Value> {
    let class = class_of(kind)?;
    let mut act = g.new_act(kind, body, wall)?;
    let signer = signer.ok_or_else(no_signer)?;
    let charter = g.state().chain.state_for(&act).ok_or_else(unknown_epoch)?.charter.clone();
    let named = if kind == "veto" { g.acts().get(act.body["act"].as_str().unwrap_or("")).and_then(|a| a.body["key"].as_str().map(str::to_string)) } else { None };
    let mut keys = keys_for(signer.as_ref(), &charter, class.map_or(1, |c| charter.needed(c)), kind == "delegate_end", &[]);
    if let Some(key) = named {
        keys = signer.keys().into_iter().filter(|k| k.public == key).collect();
    }
    if keys.is_empty() {
        return Err(not_admin());
    }
    let summary = describe(&act, g);
    sign_with(&mut act, signer.as_ref(), &keys, &summary)?;
    let id = act.id();
    let kept = g.ingest(act, wall);
    reply(g, &id, kept)
}

impl Node {
    /// The strength of ``public`` in a charter this device founds: strong for a key a phone enrolled on this device, weak for every other.
    pub fn key_strength(&self, public: &str) -> Strength {
        if self.persons.is_phone_key(public) { Strength::Strong } else { Strength::Weak }
    }

    /// The founding act in ``act`` and its charter, when the strong keys it lists are keys this device derives as strong and at
    /// least one is an admin key: without one nobody could admit a device.
    fn founding_of(&self, act: &Value) -> Result<(Act, Charter)> {
        let (found, charter) = charter_of(act)?;
        let confirmed = |k: &&AdminKey| k.strength == Strength::Strong && self.key_strength(&k.key) == Strength::Strong;
        if !charter.admins.iter().filter(confirmed).any(|k| k.role == Role::Admin) {
            return Err(declined(NO_STRONG_KEY));
        }
        if charter.admins.iter().any(|k| k.strength == Strength::Strong && !confirmed(&k)) {
            return Err(declined(UNCONFIRMED_STRONG_KEY));
        }
        Ok((found, charter))
    }

    /// Connect the signer that answers for a person.
    pub fn set_signer(&mut self, signer: Arc<dyn Signer>) {
        self.signer = Some(signer);
    }

    /// A rig's stand-in for a device key the operating system's keystore holds: only such a key may belong to a delegate.
    pub fn rig_bind_device_key(&mut self) {
        self.key_binding = KeyBinding::Keystore;
    }

    /// Run ``f``, which asks a person to sign an act of ``kind``, unless that person said no to this kind a moment ago; a no starts
    /// the wait.
    fn asking<T>(&mut self, kind: &str, f: impl FnOnce(&mut Node) -> Result<T>) -> Result<T> {
        if self.declined.get(kind).is_some_and(|at| at.elapsed() < COOLDOWN) {
            return Err(Error::say("denied", "You said no a moment ago, so I will not ask again for ten minutes.", "time", "wait"));
        }
        if !self.rates.permits("pool-ask", MOST_ASKS, COOLDOWN) {
            return Err(Error::say("denied", "I have asked you a lot in the last few minutes, so I will wait before asking again.", "time", "wait"));
        }
        self.rates.record("pool-ask", COOLDOWN);
        let out = f(self);
        if let Err(Error::Say { needs: "approval", then: "stop", .. }) = &out {
            self.declined.insert(kind.to_string(), std::time::Instant::now());
        }
        out
    }

    /// Run ``f`` on the governed pool, then bring this device's record of the members up to date, write what changed and note each
    /// member added, removed or left on the pool board.
    pub fn with_gov<T>(&mut self, f: impl FnOnce(&mut GovPool, u64) -> Result<T>) -> Result<T> {
        let before: BTreeMap<String, Status> = self.members.devices().into_iter().map(|d| (d.fingerprint, d.status)).collect();
        let gov = self.members.gov_mut().ok_or_else(no_pool)?;
        let out = f(gov, wall_ms());
        gov.save()?;
        self.members.refresh();
        self.apply_log_cuts("")?;
        self.note_roster(&before)?;
        out
    }

    fn note_roster(&mut self, before: &BTreeMap<String, Status>) -> Result<()> {
        let now: Vec<(Device, String)> = self.members.devices().into_iter().map(|d| {
            let by = self.members.gov().and_then(|g| g.state().roster.signed_by.get(&d.fingerprint).cloned()).unwrap_or_default();
            (d, by)
        }).collect();
        for (d, by) in now.into_iter().filter(|(d, _)| before.get(&d.fingerprint) != Some(&d.status)) {
            let event = match d.status {
                Status::Active if by.starts_with("delegate") => "delegate_admit",
                Status::Active => "member_added",
                Status::Revoked => "member_removed",
                Status::Left => "member_left",
            };
            self.record_event(event, &d.fingerprint, &format!("by {}", if by.is_empty() { d.by.clone() } else { by }))?;
        }
        Ok(())
    }

    /// Refold the pool as it stands now, so a hold that has run takes effect; true when the members changed.
    pub fn gov_tick(&mut self) -> Result<bool> {
        let before: BTreeMap<String, Status> = self.members.devices().into_iter().map(|d| (d.fingerprint, d.status)).collect();
        let Some(gov) = self.members.gov_mut() else { return Ok(false) };
        let due = gov.tick();
        if gov.checkpoint_due() {
            gov.save()?;
        }
        if due {
            self.members.refresh();
            self.apply_log_cuts("")?;
            self.note_roster(&before)?;
        }
        Ok(due)
    }

    /// Whether this device can admit a device now: only in a pool, and then with a signer whose key is an admin, or as a live delegate.
    pub fn can_admit(&self) -> Result<()> {
        let g = self.members.gov().ok_or_else(no_pool)?;
        if self.key_binding == KeyBinding::Keystore && g.state().roster.delegates.contains_key(&self.cert.fingerprint()) {
            return Ok(());
        }
        match &self.signer {
            None => Err(no_signer()),
            Some(s) if keys_for(s.as_ref(), &g.state().chain.latest().charter, 1, false, &[]).is_empty() => Err(not_admin()),
            Some(_) => Ok(()),
        }
    }

    /// Found a pool of this device: the charter names the keys the signer holds, each accepting it, and the device is its first
    /// member. A device in no pool yet may.
    pub fn pool_create(&mut self, name: &str, policy: Policy, project: &str) -> Result<Value> {
        if !self.members.is_unfounded() {
            return Err(declined("This device already runs a pool; leave it before making another."));
        }
        let signer = self.signer.clone().ok_or_else(no_signer)?;
        let now = wall_ms();
        let held = signer.keys();
        let admins: Vec<AdminKey> = held.iter().map(|k| AdminKey { key: k.public.clone(), alg: Alg::of_public(&k.public).unwrap_or(Alg::Ed25519), role: Role::Admin, strength: self.key_strength(&k.public), person: k.person.clone(), since: 1, acceptance: String::new(), label: k.label.clone() }).collect();
        if admins.is_empty() {
            return Err(no_signer());
        }
        if !admins.iter().any(|k| k.strength == Strength::Strong) {
            return Err(declined("A pool needs a key that asks for your presence each time it signs, and none is connected yet."));
        }
        let mut charter = Charter::new(name, policy, project, admins, now);
        let anchor = charter.anchor();
        for key in charter.admins.iter_mut() {
            key.acceptance = signer.sign(&key.key, &key.acceptance_message(&anchor, ""), &format!("Make the pool {}.", quoted(name)))?;
        }
        charter.check_founding().map_err(|e| declined(&e.to_string()))?;
        let id = self.start_pool(charter.found_act())?;
        self.gov_admit(&self.cert.der.clone(), &crate::poolops::host_name(), "found")?;
        self.record_event("pool_created", &id, &format!("{} ({})", name.chars().take(40).collect::<String>(), policy.name()))?;
        Ok(json!({"pool": id, "name": name, "policy": policy.name(), "epoch": 1}))
    }

    /// Admit the device with certificate ``der``: by an admin's signature, or by this device's own where it may (an open pool's
    /// member that checked the device is on its segment, `how` = `open`; a live delegate).
    pub fn gov_admit(&mut self, der: &[u8], name: &str, how: &str) -> Result<Device> {
        let fp = cert_fingerprint(der);
        match self.members.get(&fp) {
            Some(d) if d.status == Status::Active => return Ok(d.clone()),
            Some(d) if d.status == Status::Revoked => return Err(declined(&format!("{} was removed from this pool and does not come back; it needs a new certificate.", quoted(&d.name)))),
            _ => {}
        }
        let (signer, binding, key, me) = (self.signer.clone(), self.key_binding, self.key.clone(), self.cert.fingerprint());
        let name: String = name.chars().filter(|c| !c.is_control()).take(80).collect();
        let body = json!({"cert": hex(der), "name": name, "how": how, "via": if how == "open" { "segment" } else { "" }});
        let admit = move |n: &mut Node| n.with_gov(|g, wall| {
            let mut act = g.new_act("admit", body, wall)?;
            let (charter, roster) = (g.state().chain.latest().charter.clone(), &g.state().roster);
            let live = roster.devices.get(&me).is_some_and(|d| d.status == Status::Active);
            let delegate = live && binding == KeyBinding::Keystore && roster.delegates.contains_key(&me);
            if how == "open" {
                if charter.policy != Policy::Open || !live {
                    return Err(declined("This pool takes a device only when a person approves it."));
                }
                device_sign(&mut act, &key, &me);
            } else if delegate {
                device_sign(&mut act, &key, &me);
            } else {
                let signer = signer.as_ref().ok_or_else(no_signer)?;
                let keys = keys_for(signer.as_ref(), &charter, 1, false, &[]);
                if keys.is_empty() {
                    return Err(not_admin());
                }
                let summary = describe(&act, g);
                sign_with(&mut act, signer.as_ref(), &keys, &summary)?;
            }
            let id = act.id();
            let kept = g.ingest(act, wall);
            reply(g, &id, kept).map(|_| ())
        });
        // a device signing by its own key asks no person, so it takes nothing from the asks a person is spared; the peer limits how many it takes in
        if how == "open" { admit(self)? } else { self.asking("admit", admit)? }
        match self.members.get(&fp) {
            Some(d) if d.status == Status::Active => Ok(d.clone()),
            _ => Err(declined("That device was not admitted: the pool's rules did not allow it.")),
        }
    }

    /// Remove a member: a signed act of the pool's admins, sticky.
    pub fn gov_remove(&mut self, fingerprint: &str) -> Result<Device> {
        if !valid_fingerprint(fingerprint) {
            return Err(Error::Invalid("a device is removed by its fingerprint, 64 hex digits".into()));
        }
        if let Some(done) = self.members.get(fingerprint).filter(|d| d.status == Status::Revoked) {
            return Ok(done.clone());
        }
        let (signer, body) = (self.signer.clone(), self.with_cut("remove", json!({"fingerprint": fingerprint})));
        self.asking("remove", |n| n.with_gov(|g, wall| propose(g, signer.as_ref(), "remove", body, wall).map(|_| ())))?;
        match self.members.get(fingerprint) {
            Some(d) if d.status == Status::Revoked => Ok(d.clone()),
            _ => Err(declined("That removal needs more signatures before it takes effect.")),
        }
    }

    /// This device leaves the pool by its own signature.
    pub fn pool_leave(&mut self) -> Result<Value> {
        let (key, me) = (self.key.clone(), self.cert.fingerprint());
        self.with_gov(|g, wall| {
            let mut act = g.new_act("leave", json!({"fingerprint": me}), wall)?;
            device_sign(&mut act, &key, &me);
            let id = act.id();
            let kept = g.ingest(act, wall);
            reply(g, &id, kept)
        })
    }

    /// Make an act of ``kind`` for the pool and sign it with the keys this device's signer holds that count.
    pub fn pool_propose(&mut self, kind: &str, body: Value) -> Result<Value> {
        let signer = self.signer.clone();
        let body = self.with_cut(kind, body);
        self.asking(kind, |n| n.with_gov(|g, wall| propose(g, signer.as_ref(), kind, body, wall)))
    }

    /// Add this device's signer's signatures to the act ``id`` the pool holds.
    pub fn pool_cosign(&mut self, id: &str) -> Result<Value> {
        let signer = self.signer.clone().ok_or_else(no_signer)?;
        self.asking("cosign", |n| n.with_gov(|g, wall| {
            let mut act = g.acts().get(id).cloned().ok_or_else(|| Error::Invalid("this device holds no such act".into()))?;
            let charter = g.state().chain.state_for(&act).ok_or_else(unknown_epoch)?.charter.clone();
            let class = class_of(&act.kind)?;
            let signed = act.admin_keys().into_iter().map(str::to_string).collect::<Vec<_>>();
            let keys = keys_for(signer.as_ref(), &charter, class.map_or(1, |c| charter.needed(c)), act.kind == "delegate_end", &signed.iter().map(String::as_str).collect::<Vec<_>>());
            if keys.is_empty() {
                return Err(not_admin());
            }
            let summary = describe(&act, g);
            sign_with(&mut act, signer.as_ref(), &keys, &summary)?;
            let kept = g.ingest(act, wall);
            reply(g, id, kept)
        }))
    }

    /// Hand the pool an act a person's key signed elsewhere (the typed-yes path, a phone, another device's signer).
    pub fn pool_submit(&mut self, act: &Value) -> Result<Value> {
        let act: Act = serde_json::from_value(act.clone()).map_err(|e| Error::Invalid(format!("that is not an act: {}", e.to_string().chars().take(80).collect::<String>())))?;
        let id = act.id();
        self.with_gov(|g, wall| {
            let kept = g.ingest(act, wall);
            reply(g, &id, kept)
        })
    }

    /// Take acts a member sent (``settled``: inside a join this device made), and how each was received.
    pub fn gov_ingest_many(&mut self, acts: Vec<Act>, settled: bool) -> Result<Vec<(String, Ingest)>> {
        self.with_gov(|g, wall| Ok(g.ingest_many(acts, wall, settled)))
    }

    /// Become a member of the governed pool ``found`` founded, which the device that admitted this one described: the admin-set acts
    /// in effect there (``settled``) and the rest that stand. This device must be in it, and must not be in a pool of its own.
    pub fn adopt_governed(&mut self, found: Act, settled: Vec<Act>, delegates: Vec<Act>, rest: Vec<Act>) -> Result<()> {
        if !self.members.can_join_other() {
            return Err(Error::Denied("a device that is in a pool does not change pool".into()));
        }
        let wall = wall_ms();
        let mut pool = GovPool::create(&self.dir, found)?;
        pool.ingest_many(settled, wall, true);
        pool.ingest_many(delegates, wall, false);
        pool.ingest_many(rest, wall, true);
        if !pool.devices().get(&self.cert.fingerprint()).is_some_and(|d| d.status == Status::Active) {
            let dir = pool.dir().to_path_buf();
            drop(pool);
            let _ = std::fs::remove_dir_all(dir);
            return Err(Error::Denied("the pool does not list this device".into()));
        }
        pool.save()?;
        self.members = Pool::governed(pool);
        Ok(())
    }

    /// Start the governed pool of the founding act ``found``, which this device may do while it is in no pool.
    fn start_pool(&mut self, found: Act) -> Result<String> {
        if !self.members.is_unfounded() {
            return Err(declined("This device already runs a pool; leave it before making another."));
        }
        let wall = wall_ms();
        if found.at_ms > wall.saturating_add(SKEW_MS) || found.not_after.saturating_add(SKEW_MS) < wall {
            return Err(Error::say("denied", "That charter was signed too long ago or is dated ahead: draft it again and ask your person to sign it again.", "approval", "retry"));
        }
        let pool = GovPool::create(&self.dir, found)?;
        let id = pool.id.clone();
        self.members = Pool::governed(pool);
        Ok(id)
    }

    /// Found the pool of an act a person's keys signed elsewhere (each key's acceptance is inside its charter). The device is not
    /// a member until an admit signed for it is submitted.
    pub fn pool_found(&mut self, act: &Value) -> Result<Value> {
        let (found, charter) = self.founding_of(act)?;
        let id = self.start_pool(found)?;
        self.record_event("pool_created", &id, &format!("{} ({})", charter.name.chars().take(40).collect::<String>(), charter.policy.name()))?;
        Ok(json!({"pool": id, "name": charter.name, "policy": charter.policy.name(), "epoch": 1}))
    }

    /// The charter of a pool of ``keys`` (each `{key, person, label}`, an admin, strong when this device derives it so) with every
    /// acceptance blank, and the bytes each key signs to accept it: for keys that sign elsewhere, which hand the accepted charter back
    /// through `pool_found`.
    pub fn pool_draft_found(&self, name: &str, policy: Policy, project: &str, keys: &Value) -> Result<Value> {
        let listed = keys.as_array().filter(|k| !k.is_empty() && k.len() <= super::MAX_ADMINS).ok_or_else(|| Error::Invalid("a pool names one to sixteen admin keys".into()))?;
        let text = |k: &Value, field: &str| k[field].as_str().unwrap_or("").to_string();
        let mut admins = Vec::new();
        for k in listed {
            if !k.as_object().is_some_and(|o| o.keys().all(|f| matches!(f.as_str(), "key" | "person" | "label"))) {
                return Err(Error::Invalid("a key is named by its key, person and label; how strong it is, this device says".into()));
            }
            let key = text(k, "key");
            admins.push(AdminKey { alg: Alg::of_public(&key).unwrap_or(Alg::Ed25519), strength: self.key_strength(&key), key, role: Role::Admin, person: text(k, "person"), since: 1, acceptance: String::new(), label: text(k, "label") });
        }
        if let Some(bad) = admins.iter().find(|k| !super::keys::valid_public(&k.key)) {
            return Err(Error::Invalid(format!("{} is not a public key", bad.key.chars().take(16).collect::<String>())));
        }
        let charter = Charter::new(name, policy, project, admins, wall_ms());
        let anchor = charter.anchor();
        let accept: Vec<Value> = charter.admins.iter().map(|k| json!({"key": k.key, "alg": k.alg, "sign_bytes": hex(&k.acceptance_message(&anchor, ""))})).collect();
        Ok(json!({"charter": charter.body(), "anchor": anchor, "accept": accept, "summary": format!("Make the pool {}.", quoted(name))}))
    }

    /// The act of ``kind`` this device's pool would take next, with the bytes a person's key signs and what the person is shown: for
    /// a key that signs elsewhere and hands the act back through `pool_submit`.
    pub fn pool_draft(&mut self, kind: &str, body: Value) -> Result<Value> {
        let body = self.with_cut(kind, body);
        let g = self.members.gov().ok_or_else(no_pool)?;
        let act = g.new_act(kind, body, wall_ms())?;
        super::body::parse(&act).map_err(|e| declined(&e.to_string()))?;
        Ok(json!({"act": act, "sign_bytes": hex(&act.sign_bytes()), "summary": describe(&act, g)}))
    }

    /// An `admin_remove` keeps every act of that key this device holds, whatever the caller names: a cut the caller chose would strike what the person was never shown. A `remove` names the logs of the device that
    /// this device holds, whatever the caller says.
    fn with_cut(&self, kind: &str, mut body: Value) -> Value {
        if kind == "remove" {
            body["cut"] = json!(self.logs_of(body["fingerprint"].as_str().unwrap_or("")));
        }
        if kind == "admin_remove" {
            let key = body["key"].as_str().unwrap_or("").to_string();
            let cut: Vec<String> = self.members.gov().map(|g| g.acts().iter().filter(|(_, a)| a.admin_keys().contains(&key.as_str())).map(|(id, _)| id.clone()).collect()).unwrap_or_default();
            body["cut"] = json!(cut);
        }
        body
    }

    /// How this device's pool is governed, as `pool_status` shows it.
    pub fn gov_view(&self) -> Value {
        let Some(g) = self.members.gov() else {
            let old = &self.members.retired;
            let say = if old.is_empty() { "This device is a pool of one with no admins; ask me to make a pool when you want others to join it.".to_string() } else { format!("{} The old file is {old}.", crate::membership::OLD_FORMAT) };
            return json!({"mode": "unfounded", "say": say, "old_format_file": old});
        };
        let (s, now) = (g.state(), g.clock.now_ms());
        let latest = s.chain.latest();
        let c = &latest.charter;
        let admins: Vec<Value> = c.admins.iter().map(|k| json!({"fingerprint": k.fingerprint(), "role": k.role, "strength": k.strength, "person": k.person, "label": k.label, "since": k.since})).collect();
        let secs = |at: i64| (at.saturating_sub(now) / 1000).max(0);
        let pending = s.chain.pending.as_ref().map(|p| json!({"acts": p.acts, "takes_effect_in_s": secs(p.effective_ms)}));
        let dispute = s.chain.dispute.as_ref().map(|d| json!({"contested": d.contested, "why": d.why, "ends_in_s": secs(d.ends_ms), "say": format!("Two admins disagree about {}. Changes to who runs this pool are paused.", quoted(&c.name))}));
        let waiting: Vec<Value> = s.chain.short.iter().chain(s.roster.short.iter()).take(MOST_REPLIES).map(|(id, (have, need, why))| json!({"act": id, "have": have, "need": need, "why": why})).collect();
        let delegates: Vec<&String> = s.roster.delegates.keys().collect();
        json!({
            "mode": "governed", "name": c.name, "epoch": latest.n, "charter_hash": latest.hash, "admins": admins, "weak_keys_only": c.weak_only(),
            "thresholds": c.thresholds, "holds": c.holds, "pending": pending, "dispute": dispute, "waiting_for_signers": waiting, "delegates": delegates,
            "refused": g.refusals().iter().rev().take(MOST_REPLIES).collect::<Vec<_>>(),
        })
    }
}
