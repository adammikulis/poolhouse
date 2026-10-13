//! Governed pools for the tests: keys made from a seed, charters with their acceptances, acts signed by a seed, pools on a frozen
//! awake clock, and nodes that found and join them.

use std::sync::Arc;

use poolhouse_node::cert::{self, Identity};
use poolhouse_node::fsutil::hex;
use poolhouse_node::gov::act::{Act, ActSig, SigKind};
use poolhouse_node::gov::charter::{Charter, Unit};
use poolhouse_node::gov::keys::{AdminKey, Role, Strength};
use poolhouse_node::gov::pool::{GovPool, Ingest};
use poolhouse_node::gov::signer::RigSigner;
use poolhouse_node::membership::Policy;
use poolhouse_node::poolops::wall_ms;
use serde_json::{json, Value};
use tempfile::TempDir;

pub const HOUR: i64 = 3_600_000;
pub const DAY: i64 = 24 * HOUR;

/// A key of the pool: the seed it is made from, the person it belongs to, its role and strength.
#[derive(Clone, Copy)]
pub struct K(pub u8, pub &'static str, pub Role, pub Strength);

/// A strong admin key of ``person``.
pub fn strong(seed: u8, person: &'static str) -> K {
    K(seed, person, Role::Admin, Strength::Strong)
}

pub fn weak(seed: u8, person: &'static str) -> K {
    K(seed, person, Role::Admin, Strength::Weak)
}

pub fn recovery(seed: u8, person: &'static str) -> K {
    K(seed, person, Role::Recovery, Strength::Strong)
}

pub fn public(seed: u8) -> String {
    RigSigner::public(seed)
}

/// The listing of the key ``k`` at epoch ``since``, accepted against ``anchor``.
pub fn entry(k: K, since: u64, anchor: &str) -> AdminKey {
    let mut e = AdminKey { key: public(k.0), role: k.2, strength: k.3, person: k.1.into(), since, acceptance: String::new(), label: format!("{}'s key", k.1) };
    e.acceptance = RigSigner::sign_as(k.0, &e.acceptance_message(anchor));
    e
}

/// An epoch-1 charter of the keys, each accepting it.
pub fn charter(keys: &[K], policy: Policy, unit: Unit) -> Charter {
    let bare: Vec<AdminKey> = keys.iter().map(|k| entry(*k, 1, "")).collect();
    let mut c = Charter::new("home", policy, "", bare, wall_ms());
    c.thresholds.unit = unit;
    let anchor = c.anchor();
    c.admins = keys.iter().map(|k| entry(*k, 1, &anchor)).collect();
    c.admins.sort_by(|a, b| a.key.cmp(&b.key));
    c
}

pub fn found_act(c: &Charter) -> Act {
    Act::new(&c.pool_id(), 1, "found", c.body(), c.at_ms, "").unwrap()
}

/// A pool of the keys on a frozen clock, in a directory of its own.
pub fn pool(keys: &[K], policy: Policy) -> (TempDir, GovPool) {
    pool_of(&charter(keys, policy, Unit::Person))
}

pub fn pool_of(c: &Charter) -> (TempDir, GovPool) {
    let dir = tempfile::tempdir().unwrap();
    let g = GovPool::create(dir.path(), found_act(c)).unwrap();
    g.clock.freeze();
    (dir, g)
}

/// Sign ``act`` as the key made from ``seed``.
pub fn sign(act: &mut Act, seed: u8) {
    let sig = RigSigner::sign_as(seed, &act.sign_bytes());
    act.add_signature(ActSig { kind: SigKind::Admin, key: public(seed), sig });
}

/// An act of ``kind`` following the charter now in force, signed by ``seeds``.
pub fn make(g: &GovPool, kind: &str, body: Value, seeds: &[u8]) -> Act {
    let mut act = g.new_act(kind, body, wall_ms()).unwrap();
    seeds.iter().for_each(|s| sign(&mut act, *s));
    act
}

/// Hand an act to the pool, as a member would send it.
pub fn take(g: &mut GovPool, act: Act) -> Ingest {
    g.ingest(act, wall_ms())
}

/// Make an act and hand it to the pool.
pub fn put(g: &mut GovPool, kind: &str, body: Value, seeds: &[u8]) -> Ingest {
    let act = make(g, kind, body, seeds);
    take(g, act)
}

pub fn put_admit(g: &mut GovPool, device: &Identity, seeds: &[u8]) -> Ingest {
    let act = admit(g, device, seeds);
    take(g, act)
}

pub fn advance(g: &mut GovPool, ms: i64) {
    g.clock.advance_ms(ms);
    g.refold();
}

/// A device certificate made from ``seed``.
pub fn ident(seed: u8) -> Identity {
    let dir = tempfile::tempdir().unwrap();
    cert::load_or_create(dir.path(), &super::key(seed)).unwrap()
}

/// Sign ``act`` as the device made from ``seed``.
pub fn device_sign(act: &mut Act, seed: u8) {
    use ed25519_dalek::Signer;
    let sig = hex(&super::key(seed).sign(&act.sign_bytes()).to_bytes());
    act.add_signature(ActSig { kind: SigKind::Device, key: ident(seed).fingerprint(), sig });
}

pub fn admit_body(device: &Identity, name: &str, how: &str, via: &str) -> Value {
    json!({"cert": hex(&device.der), "name": name, "how": how, "via": via})
}

/// An `admit` of ``device`` signed by the admin keys ``seeds``.
pub fn admit(g: &GovPool, device: &Identity, seeds: &[u8]) -> Act {
    make(g, "admit", admit_body(device, "laptop", "join-code", ""), seeds)
}

pub fn is_member(g: &GovPool, device: &Identity) -> bool {
    g.devices().get(&device.fingerprint()).is_some_and(|d| d.status == poolhouse_node::membership::Status::Active)
}

pub fn is_removed(g: &GovPool, device: &Identity) -> bool {
    g.devices().get(&device.fingerprint()).is_some_and(|d| d.status == poolhouse_node::membership::Status::Revoked)
}

/// The admin keys of the charter in force, as public keys.
pub fn admins(g: &GovPool) -> Vec<String> {
    g.state().chain.latest().charter.admins.iter().map(|k| k.key.clone()).collect()
}

/// One person with one key made from ``seed``; a node founds a pool of it only once a phone enrolled the key there (`govnode::found`).
pub fn person(seed: u8, name: &str) -> Arc<RigSigner> {
    Arc::new(RigSigner::new().with_key(seed, name))
}
