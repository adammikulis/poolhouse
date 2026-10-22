//! Governed pools on real nodes: a node that founds a pool for a rig's person, and a pool whose clock is moved by hand.

use std::sync::Arc;

use ed25519_dalek::{Signer, SigningKey};
use poolhouse_node::gov::signer::RigSigner;
use poolhouse_node::grants::authority::Authority;
use poolhouse_node::grants::spec::ACTIONS;
use poolhouse_node::peer::PeerClient;
use serde_json::{json, Value};

use super::gov::person;
use super::grants::{Call, Person, Spec, HOUR};
use super::netkit::{device, Dev};

pub const ANN: u8 = 1;
pub const BEN: u8 = 2;
/// The seed of the test person's grant key, which a node process founds pools of once `plant_phone_person` enrolled it.
pub const PERSON: u8 = 0x70;

/// The listing of ``k`` at epoch ``since`` that follows the charter now in force on the pool of ``dev``.
pub fn entry_for(dev: &Dev, k: super::gov::K, since: u64) -> poolhouse_node::gov::keys::AdminKey {
    let node = dev.node.lock().unwrap();
    super::gov::entry_after(k, since, node.members.gov().unwrap())
}

pub fn sync(dev: &Dev) {
    dev.ok("sync_now", "", &dev.token, json!({}));
}

pub fn link(from: &Dev, to: &Dev) -> PeerClient {
    PeerClient::connect(&from.net.me, to.addr(), Some(&to.fp())).unwrap()
}

/// `a` founds a secure pool for ann; `b` and `c` join it with codes from `a` and then hear of one another.
pub fn pool_of_three() -> (Dev, Dev, Dev) {
    let (a, b, c) = (device(), device(), device());
    found(&a, person(ANN, "ann"), "secure");
    a.join_with_code(&b);
    a.join_with_code(&c);
    sync(&b);
    sync(&c);
    (a, b, c)
}

/// The certificate of the node whose state directory is ``state`` (a process the test started).
pub fn cert_of(state: &std::path::Path) -> Vec<u8> {
    std::fs::read(state.join(poolhouse_node::cert::FILE)).unwrap()
}

/// Found a pool of the test person's key on a node with no signer, reached by ``call(method, params)`` (a process the test started;
/// the call answers the result and fails the test when it is refused): the charter puts the node (certificate ``own``) in the pool first,
/// and an admit the key signed is submitted for each of ``others`` (certificates), so a window opened with `presigned` lets them in.
pub fn found_remote(call: &mut dyn FnMut(&str, serde_json::Value) -> serde_json::Value, own: &[u8], policy: poolhouse_node::membership::Policy, others: &[Vec<u8>]) -> String {
    use super::gov::{charter_of, first_of, found_act, sign, strong};
    let charter = charter_of(&[strong(PERSON, "ann")], policy, poolhouse_node::gov::charter::Unit::Person, poolhouse_node::poolops::wall_ms(), first_of(own));
    let founded = call("pool_found", json!({"act": found_act(&charter)}));
    for der in others {
        let body = json!({"cert": poolhouse_node::fsutil::hex(der), "name": "dev", "how": "join-code", "via": ""});
        let draft = call("pool_draft", json!({"kind": "admit", "body": body}));
        let mut act: poolhouse_node::gov::act::Act = serde_json::from_value(draft["act"].clone()).unwrap();
        sign(&mut act, PERSON);
        call("pool_submit", json!({"act": act}));
    }
    founded["pool"].as_str().unwrap().to_string()
}

/// A charter of ``keys`` that puts the device ``dev`` in the pool first, each key accepting it.
pub fn charter_for(dev: &Dev, keys: &[super::gov::K], policy: poolhouse_node::membership::Policy) -> poolhouse_node::gov::charter::Charter {
    super::gov::charter_of(keys, policy, poolhouse_node::gov::charter::Unit::Person, poolhouse_node::poolops::wall_ms(), super::gov::first_of(&dev.net.me.der))
}

/// A node on this device that signs for ``signer`` and founds a pool.
pub fn found(dev: &Dev, signer: Arc<RigSigner>, policy: &str) -> String {
    found_for(dev, signer, policy, "")
}

/// Like `found`, for the project ``project`` (a project key, or empty).
pub fn found_for(dev: &Dev, signer: Arc<RigSigner>, policy: &str, project: &str) -> String {
    enrol_as_phone_keys(dev, &signer);
    dev.node.lock().unwrap().set_signer(signer);
    let reply = dev.ok("pool_create", "", &dev.token, json!({"title": "home", "policy": policy, "project": project}));
    if !dev.token.is_empty() {
        grant(dev, "demo", &dev.token, &[]);
    }
    reply["pool"].as_str().unwrap().to_string()
}

/// Enrol every key ``signer`` holds on the node of ``dev`` as a phone's key, which is what makes a key strong there.
pub fn enrol_as_phone_keys(dev: &Dev, signer: &RigSigner) {
    use poolhouse_node::gov::signer::Signer;
    let mut node = dev.node.lock().unwrap();
    for key in signer.keys() {
        node.persons.add_phone_key(poolhouse_node::grants::person::ED25519, &key.public, &key.label, poolhouse_node::poolops::wall_ms()).unwrap();
    }
}

/// Enrol the key made from ``seed`` on the node of ``dev`` as a phone's key.
pub fn enrol_phone_key(dev: &Dev, seed: u8) {
    enrol_as_phone_keys(dev, &RigSigner::new().with_key(seed, "phone"));
}

/// Write the state directory ``state`` of a node process not yet started with the test person's key (the grant key) enrolled as a phone's:
/// the process founds pools of that key, which signs outside it.
pub fn plant_phone_person(state: &std::path::Path) {
    let key = super::grants::person();
    std::fs::create_dir_all(state).unwrap();
    let entry = json!({"public": key.public(), "label": "a test phone", "at_ms": 1, "alg": "ed25519", "phone": true});
    std::fs::write(state.join("persons.json"), json!({ key.fingerprint(): entry }).to_string()).unwrap();
}

/// Freeze the awake clock of the device's pool and move it on by ``ms``.
pub fn pass(dev: &Dev, ms: i64) {
    let mut n = dev.node.lock().unwrap();
    n.members.gov().unwrap().clock.advance_ms(ms);
    n.gov_tick().unwrap();
}

/// Ask for ``actions`` (any resource, an hour, as often as needed) as the session ``token`` of ``board``, and have ``decide`` answer each request
/// from another session: the grants a pool's phone approves. ``decide`` builds the `approval_decide` params from the request as the asking session read it.
pub fn approved(call: Call, board: &str, token: &str, actions: &[&str], decide: &dyn Fn(&Value) -> Value) {
    static COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    for chunk in actions.chunks(poolhouse_node::approvals::challenge::MOST_ASKS) {
        let asks: Vec<Value> = chunk.iter().map(|a| json!({"action": a, "resource": "*", "lasts_ms": HOUR})).collect();
        let made = call("approval_request", board, token, json!({"asks": asks}));
        assert_eq!(made["ok"], true, "ask for {chunk:?}: {made}");
        let id = format!("approver-{}", COUNTER.fetch_add(1, std::sync::atomic::Ordering::SeqCst));
        let carrier = call("register", board, "", json!({"model": "claude-sonnet-5-5", "harness": "claude-code", "session": id}));
        let carrier = carrier["result"]["token"].as_str().expect("the approving session registers").to_string();
        let done = call("approval_decide", "", &carrier, decide(&made["result"]));
        assert_eq!(done["ok"], true, "approve {chunk:?}: {done}");
    }
}

/// The seed among the first 256 whose key has the fingerprint ``fingerprint``.
fn seed_of(fingerprint: &str) -> Option<u8> {
    (0..=255u8).find(|s| poolhouse_node::gov::keys::fingerprint_of(&RigSigner::public(*s)) == fingerprint)
}

/// The authority key of the device's pool that a seed makes, as a person; the founder's (`ANN`) is enrolled here as a phone's when the device holds
/// none yet.
pub fn authority_person(dev: &Dev) -> Person {
    let mut keys = Authority::of(&dev.node.lock().unwrap()).authority();
    if keys.is_empty() {
        enrol_phone_key(dev, ANN);
        keys = Authority::of(&dev.node.lock().unwrap()).authority();
    }
    let seed = keys.iter().find_map(|f| seed_of(f)).expect("an authority key made from a seed");
    Person { key: SigningKey::from_bytes(&[seed; 32]) }
}

/// The pool's phone approves ``only`` (every action when empty) for the session ``token`` of ``board``, as one request for each eight.
pub fn grant(dev: &Dev, board: &str, token: &str, only: &[&str]) {
    let approver = authority_person(dev);
    let actions: Vec<&str> = ACTIONS.iter().copied().filter(|a| only.is_empty() || only.contains(a)).collect();
    approved(&mut |m, b, t, p| dev.call(m, b, t, p), board, token, &actions, &|made| approver.decision(made));
}

/// The issue row of ``spec`` as an approval writes it, for the node ``node`` and the key ``signer`` (the fingerprint the row names) that
/// ``sign`` answers for: built by hand, so a test can place it on a device that did not approve it or sign it with a key that is no admin's.
pub fn approved_row(node: &str, board: &str, gid: &str, spec: &Spec, signer: &str, sign: &dyn Fn(&[u8]) -> String) -> serde_json::Map<String, Value> {
    let (at, start) = (super::grants::now_ms(), super::grants::now_ms().checked_add_signed(spec.from_ms).unwrap());
    let lasts = (spec.until_ms - spec.from_ms) as u64;
    let mut ask = json!({"action": spec.action, "device": spec.device, "gid": gid, "lasts_ms": lasts, "resource": spec.resource, "review": spec.review});
    if let Some(n) = spec.uses {
        ask["uses"] = json!(n);
    }
    let mut body = serde_json::Map::new();
    for (k, v) in [("op", json!("issue")), ("board", json!(board)), ("gid", json!(gid)), ("to", spec.to.clone()), ("action", json!(spec.action)),
                   ("scope", json!({"board": board, "device": spec.device, "resource": spec.resource})), ("at_ms", json!(start)),
                   ("until_ms", json!(start + lasts)), ("person", json!(signer)),
                   ("approval", json!({"id": format!("ap-{:016x}", at), "node": node, "nonce": format!("{:032x}", at), "created_ms": start - 1000, "expires_ms": start + 600_000,
                                       "text": "The pool's phone approved this grant.", "grants": [ask], "index": 0,
                                       "context": poolhouse_node::approvals::context::Context::default().object(), "decision": "approve"}))] {
        body.insert(k.into(), v);
    }
    if let Some(n) = spec.uses {
        body.insert("uses".into(), json!(n));
    }
    if spec.review {
        body.insert("conditions".into(), json!({"review": true}));
    }
    let sig = sign(&poolhouse_node::approvals::challenge::grant_bytes(&body));
    body.insert("sig".into(), json!(sig));
    body
}

/// Append to the log of ``board`` on ``dev`` an issue row of ``spec`` that the pool's authority key approved.
pub fn inject_approved(dev: &Dev, board: &str, gid: &str, spec: &Spec, actor: &str) {
    let approver = authority_person(dev);
    let body = approved_row(&dev.fp(), board, gid, spec, &approver.fingerprint(), &|bytes| poolhouse_node::fsutil::hex(&approver.key.sign(bytes).to_bytes()));
    dev.node.lock().unwrap().host(board).unwrap().board.append_person(poolhouse_node::row::Kind::Grant, actor, Value::Object(body)).unwrap();
}

/// The test person (the key a node process founds pools of) approves ``only`` (every action when empty) for the session ``token`` of ``board`` on the
/// node process whose state directory is ``state``.
pub fn approve_process(state: &std::path::Path, board: &str, token: &str, only: &[&str]) {
    let mut call = |m: &str, b: &str, t: &str, p: Value| match poolhouse_node::client::Client::connect(state).and_then(|mut c| c.call(m, b, t, p)) {
        Ok(v) => json!({"ok": true, "result": v}),
        Err(e) => json!({"ok": false, "error": {"code": e.code(), "message": e.to_string()}}),
    };
    let actions: Vec<&str> = ACTIONS.iter().copied().filter(|a| only.is_empty() || only.contains(a)).collect();
    approved(&mut call, board, token, &actions, &|made| super::grants::person().decision(made));
}
