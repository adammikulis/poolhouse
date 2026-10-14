//! Governed pools on real nodes: a node that founds a pool for a rig's person, and a pool whose clock is moved by hand.

use std::sync::Arc;

use poolhouse_node::gov::signer::RigSigner;
use poolhouse_node::peer::PeerClient;
use serde_json::json;

use super::gov::person;
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
