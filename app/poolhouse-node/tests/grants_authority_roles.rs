//! A pool of strong keys A, B and C, a recovery key D and a weak key W, each a phone's key on the device: which of them make a grant count or
//! end one, and what a removed admin leaves behind.
mod kit;

use ed25519_dalek::{Signer, SigningKey};
use kit::gov::{charter, found_act, recovery, sign, strong, weak, HOUR};
use kit::govnode::{approved, approved_row, charter_for, enrol_phone_key, pass};
use kit::grants::{Person, Spec};
use kit::netkit::{device, Dev};
use kit::{code, ok};
use poolhouse_node::fsutil::hex;
use poolhouse_node::gov::act::Act;
use poolhouse_node::gov::charter::{Charter, Mode, Unit};
use poolhouse_node::grants::authority::closed_reason;
use poolhouse_node::membership::Policy;
use poolhouse_node::row::Kind;
use serde_json::{json, Value};

const A: u8 = 1;
const B: u8 = 2;
const C: u8 = 3;
const D: u8 = 4;
const W: u8 = 5;

fn holder(seed: u8) -> Person {
    Person { key: SigningKey::from_bytes(&[seed; 32]) }
}

struct Seeded {
    dev: Dev,
    name: String,
    token: String,
}

impl Seeded {
    /// A pool of A, B and C (strong), D (recovery) and W (weak), every key enrolled on the device as a phone's, and a session `lead` of board `demo`.
    fn new() -> Seeded {
        let dev = device();
        for seed in [A, B, C, D, W] {
            enrol_phone_key(&dev, seed);
        }
        let keys = [strong(A, "ann"), strong(B, "ben"), strong(C, "cy"), recovery(D, "di"), weak(W, "wil")];
        dev.ok("pool_found", "", &dev.token, json!({"act": found_act(&charter_for(&dev, &keys, Policy::Secure))}));
        let (name, token) = dev.session("demo", "lead");
        Seeded { dev, name, token }
    }

    /// The key ``by`` approves ``action`` for the session; the gid it was written under.
    fn approve(&self, by: u8, action: &str) -> String {
        let who = holder(by);
        approved(&mut |m, b, t, p| self.dev.call(m, b, t, p), "demo", &self.token, &[action], &|made| who.decision(made));
        self.gid(action)
    }

    fn grants(&self) -> Value {
        self.dev.ok("grants", "demo", &self.token, json!({}))
    }

    /// The gid of the grant of ``action`` to the session.
    fn gid(&self, action: &str) -> String {
        let all = self.grants();
        let mine: Vec<&Value> = all["grants"].as_array().unwrap().iter().filter(|g| g["action"] == action && g["to"]["name"] == self.name.as_str()).collect();
        assert_eq!(mine.len(), 1, "{all}");
        mine[0]["gid"].as_str().unwrap().to_string()
    }

    /// The status and the reason of the grant ``gid``.
    fn state(&self, gid: &str) -> (String, String) {
        let found = self.grants()["grants"].as_array().unwrap().iter().find(|g| g["gid"] == gid).cloned().expect("the grant is listed");
        (found["status"].as_str().unwrap().into(), found["why"].as_str().unwrap().into())
    }

    /// Append to the log the issue row of ``action`` that the key ``by`` signed as an approval, built by hand.
    fn plant(&self, by: u8, gid: &str, action: &str) {
        let who = holder(by);
        let body = approved_row(&self.dev.fp(), "demo", gid, &Spec::session(&self.name, action), &who.fingerprint(), &|bytes| hex(&who.key.sign(bytes).to_bytes()));
        self.dev.node.lock().unwrap().host("demo").unwrap().board.append_person(Kind::Grant, &self.name, Value::Object(body)).unwrap();
    }

    /// The reply to the key ``by`` ending the grant ``gid`` through the call.
    fn revoke(&self, by: u8, gid: &str) -> Value {
        self.dev.call("grant_revoke", "demo", &self.token, holder(by).revoke_params("demo", gid, "ended"))
    }

    /// Append the revoke of ``gid`` that the key ``by`` signed as a row.
    fn plant_revoke(&self, by: u8, gid: &str) {
        let mut row = holder(by).revoke_params("demo", gid, "ended");
        row["op"] = json!("revoke");
        row["board"] = json!("demo");
        self.dev.node.lock().unwrap().host("demo").unwrap().board.append_person(Kind::Grant, &self.name, row).unwrap();
    }

    /// An act of ``kind`` signed by the keys ``seeds``, handed to the pool.
    fn act(&self, kind: &str, body: Value, seeds: &[u8]) -> Value {
        let draft = self.dev.ok("pool_draft", "", &self.dev.token, json!({"kind": kind, "body": body}));
        let mut act: Act = serde_json::from_value(draft["act"].clone()).unwrap();
        seeds.iter().for_each(|s| sign(&mut act, *s));
        self.dev.ok("pool_submit", "", &self.dev.token, json!({"act": act}))
    }

    fn authority(&self) -> Vec<String> {
        let mut keys: Vec<String> = serde_json::from_value(self.grants()["authority"].clone()).unwrap();
        keys.sort();
        keys
    }
}

fn sorted(seeds: &[u8]) -> Vec<String> {
    let mut keys: Vec<String> = seeds.iter().map(|s| holder(*s).fingerprint()).collect();
    keys.sort();
    keys
}

#[test]
fn the_strong_keys_make_a_grant_count_and_the_recovery_key_and_the_weak_key_do_not() {
    let s = Seeded::new();
    assert_eq!(s.authority(), sorted(&[A, B, C]), "the recovery key and the weak key are no authority key, though this device knows both as phones'");
    for (by, gid) in [(B, "g-00000000000000e1"), (C, "g-00000000000000e2")] {
        s.plant(by, gid, "sync_now");
        assert_eq!(s.state(gid).0, "live", "an approval of a strong key counts");
    }
    s.plant(D, "g-00000000000000e3", "note_verify");
    assert!(s.state("g-00000000000000e3").1.contains("the recovery key signs no grant"), "{:?}", s.state("g-00000000000000e3"));
    s.plant(W, "g-00000000000000e4", "task_authority");
    assert!(s.state("g-00000000000000e4").1.contains("not strong"), "{:?}", s.state("g-00000000000000e4"));
    assert_eq!(s.state("g-00000000000000e4").0, "ignored");
}

#[test]
fn the_recovery_key_ends_no_grant_where_the_weak_key_does() {
    let s = Seeded::new();
    let gid = s.approve(A, "sync_now");
    let by_recovery = s.revoke(D, &gid);
    assert_eq!(code(&by_recovery), "denied", "{by_recovery}");
    assert!(by_recovery["error"]["message"].as_str().unwrap().contains("Only an admin key of this pool ends a grant"), "{by_recovery}");
    s.plant_revoke(D, &gid);
    assert_eq!(s.state(&gid).0, "live", "a revoke row of the recovery key is ignored");
    ok(s.revoke(W, &gid));
    assert_eq!(s.state(&gid).0, "revoked", "a weak admin key only narrows, so it ends a grant");
}

#[test]
fn a_charter_the_members_run_or_with_no_strong_key_closes_the_pool_to_every_grant() {
    let admins = charter(&[strong(A, "ann"), weak(W, "ann")], Policy::Secure, Unit::Person);
    assert_eq!(closed_reason(&admins), None);
    let mut members: Charter = admins.clone();
    members.mode = Mode::Members;
    assert!(closed_reason(&members).is_some_and(|why| why.contains("not run by its admins")));
    let no_strong = charter(&[weak(W, "ann")], Policy::Secure, Unit::Person);
    assert!(closed_reason(&no_strong).is_some_and(|why| why.contains("no strong admin key")));
}

#[test]
fn a_removed_admins_grants_stop_counting_and_the_revoke_it_signed_before_still_holds() {
    let s = Seeded::new();
    let (own, other) = (s.approve(A, "sync_now"), s.approve(B, "note_verify"));
    ok(s.revoke(A, &other));
    assert_eq!(s.state(&other).0, "revoked");
    s.act("admin_remove", json!({"key": kit::gov::public(A)}), &[B, C]);
    assert_eq!(s.state(&own).0, "live", "a removal waits out its hold");
    pass(&s.dev, 25 * HOUR);
    assert_eq!(s.authority(), sorted(&[B, C]));
    let (state, why) = s.state(&own);
    assert_eq!(state, "ignored");
    assert!(why.contains("not an admin of this pool now"), "{why}");
    assert_eq!(s.state(&other).0, "revoked", "what a removed admin ended stays ended");
    s.dev.node.lock().unwrap().persons.remove(&holder(A).fingerprint(), kit::grants::now_ms()).unwrap();
    assert_eq!(s.state(&other).0, "revoked", "and so does it when this device forgets the key");
}

/// The reply to the key ``by`` cutting the key ``subject`` off, through the call.
fn distrust_by(s: &Seeded, by: u8, subject: u8) -> Value {
    let fingerprint = holder(subject).fingerprint();
    let (pool, since) = {
        let node = s.dev.node.lock().unwrap();
        let gov = node.members.gov().unwrap();
        (gov.id.clone(), gov.state().chain.latest().charter.admins.iter().find(|k| k.fingerprint() == fingerprint).map_or(0, |k| k.since))
    };
    let mut body = serde_json::Map::new();
    for (k, v) in [("op", json!("distrust")), ("board", json!("pool")), ("key", json!(fingerprint)), ("since", json!(since)), ("pool", json!(pool)),
                   ("person", json!(holder(by).fingerprint())), ("at_ms", json!(kit::grants::now_ms())), ("reason", json!("its key was lost"))] {
        body.insert(k.into(), v);
    }
    s.dev.call("grant_distrust", "", &s.dev.token, holder(by).sign_params(body))
}

#[test]
fn the_recovery_key_cuts_no_key_off_where_the_weak_key_does() {
    let s = Seeded::new();
    let by_recovery = distrust_by(&s, D, A);
    assert_eq!(code(&by_recovery), "denied", "{by_recovery}");
    assert_eq!(s.authority(), sorted(&[A, B, C]), "a distrust row of the recovery key cut nothing off");
    ok(distrust_by(&s, W, A));
    assert_eq!(s.authority(), sorted(&[B, C]), "the weak admin key cut A off");
}
