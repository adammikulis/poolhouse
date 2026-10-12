//! What makes a phone's key strong: the chain its secure hardware signed about the key, checked against a root the node trusts. A phone
//! that shows none, or one that says less, is added all the same and founds nothing; the last admin phone is not removed.

mod kit;

use std::sync::Arc;

use kit::attest::{Claims, Root};
use kit::phone::{FakePhone, Rig};
use poolhouse_node::fsutil::unhex;
use poolhouse_node::phone::attest::{challenge, verify};
use serde_json::{json, Value};

fn trusting(root: &Arc<Root>) -> (Rig, String) {
    let rig = Rig::new();
    rig.node.lock().unwrap().attest_roots.push(root.pin.clone());
    let (_, token) = rig.session("pool", "agent-one");
    (rig, token)
}

fn asked(rig: &Rig, token: &str) -> Value {
    rig.call("pool_ask_found", "", token, json!({"title": "home", "policy": "secure", "project": ""}))
}

fn attested_flag(rig: &Rig, phone: &FakePhone) -> bool {
    rig.node.lock().unwrap().phones.get(&phone.id).unwrap().attested
}

fn proves_nothing(asked: &Value) {
    assert_eq!(asked["ok"], false, "{asked}");
    let say = asked["error"]["say"].as_str().unwrap();
    assert!(say.contains("cannot prove its key stays in its secure hardware") && say.len() <= 160, "{say}");
}

#[test]
fn a_phone_whose_chain_is_rooted_and_says_the_key_needs_the_fingerprint_is_added_strong_and_founds_a_pool() {
    let root = Arc::new(Root::new());
    let (rig, token) = trusting(&root);
    let mut phone = FakePhone::attested("Pixel", &root, Claims::default());
    rig.add(&mut phone, None);
    assert!(attested_flag(&rig, &phone));
    assert_eq!(asked(&rig, &token)["ok"], true);
}

#[test]
fn a_phone_that_shows_no_chain_is_added_weak_and_cannot_found() {
    let root = Arc::new(Root::new());
    let (rig, token) = trusting(&root);
    let mut phone = FakePhone::new("Pixel");
    rig.add(&mut phone, None);
    assert!(!attested_flag(&rig, &phone), "no chain, no proof");
    proves_nothing(&asked(&rig, &token));
}

#[test]
fn a_chain_that_says_less_than_hardware_that_asks_for_the_fingerprint_each_time_adds_a_weak_phone() {
    let root = Arc::new(Root::new());
    let default = Claims::default();
    let worse = [
        ("made in software", Claims { hardware: false, ..default }),
        ("usable with no authentication", Claims { no_auth_required: true, ..default }),
        ("usable for a while after one authentication", Claims { auth_timeout: true, ..default }),
        ("no fingerprint among the authentications", Claims { fingerprint: false, ..default }),
        ("not a P-256 key", Claims { p256: false, ..default }),
        ("made for another addition", Claims { for_another_addition: true, ..default }),
    ];
    for (what, claims) in worse {
        let (rig, token) = trusting(&root);
        let mut phone = FakePhone::attested("Pixel", &root, claims);
        rig.add(&mut phone, None);
        assert!(!attested_flag(&rig, &phone), "{what}");
        proves_nothing(&asked(&rig, &token));
    }
}

#[test]
fn a_chain_that_ends_at_a_root_the_node_does_not_trust_adds_a_weak_phone() {
    let (rig, token) = trusting(&Arc::new(Root::new()));
    let mut phone = FakePhone::attested("Pixel", &Arc::new(Root::new()), Claims::default());
    rig.add(&mut phone, None);
    assert!(!attested_flag(&rig, &phone));
    proves_nothing(&asked(&rig, &token));
}

#[test]
fn the_roots_a_shipped_node_trusts_are_the_ones_google_publishes_and_none_is_written_yet() {
    let rig = Rig::new();
    assert!(rig.node.lock().unwrap().attest_roots.is_empty(), "no root is pinned until the owner fills them in (phone/attest.rs)");
}

#[test]
fn a_chain_is_refused_when_it_is_about_another_key_was_tampered_with_or_was_made_for_another_challenge() {
    let root = Root::new();
    let pins = vec![root.pin.clone()];
    let phone = FakePhone::new("Pixel");
    let point = unhex(&phone.public).unwrap();
    let ask = challenge("node", "secret");
    let chain = |claims| root.chain(&phone.pkcs8, &ask, claims).iter().map(|c| unhex(c).unwrap()).collect::<Vec<_>>();
    assert_eq!(verify(&chain(Claims::default()), &pins, &point, &ask), Ok(()));
    assert_eq!(verify(&chain(Claims::default()), &pins, &unhex(&FakePhone::new("Other").public).unwrap(), &ask), Err("the attestation is about another key"));
    assert_eq!(verify(&chain(Claims::default()), &pins, &point, &challenge("node", "another secret")), Err("the attestation was made for another addition"));
    let mut tampered = chain(Claims::default());
    let last = tampered[0].len() - 1;
    tampered[0][last] ^= 1;
    assert_eq!(verify(&tampered, &pins, &point, &ask), Err("a certificate is not signed by the next one"));
    assert_eq!(verify(&chain(Claims::default())[..1], &pins, &point, &ask), Err("the chain has the wrong length"));
    let mut swapped = chain(Claims::default());
    swapped.reverse();
    assert!(verify(&swapped, &pins, &point, &ask).is_err());
}

#[test]
fn the_only_admin_phone_of_a_pool_is_not_removed_but_another_phone_is() {
    let root = Arc::new(Root::new());
    let (rig, token) = trusting(&root);
    let mut first = FakePhone::attested("Pixel", &root, Claims::default());
    rig.add(&mut first, None);
    let mut spare = FakePhone::new("Spare");
    rig.add(&mut spare, Some(&first));
    let id = asked(&rig, &token)["result"]["id"].as_str().unwrap().to_string();
    let view = rig.get(&first, &id);
    let bytes = unhex(view["challenge"]["action"]["message"].as_str().unwrap()).unwrap();
    let body = json!({"id": id, "decision": "approve", "key": first.fingerprint(), "sig": first.sign(&bytes), "text_sha256": poolhouse_node::fsutil::sha256_hex(view["text"].as_str().unwrap().as_bytes())});
    assert_eq!(rig.phone("POST", "decide", "", body).1["ok"], true);
    let refused = rig.call("phone_remove", "", &token, json!({"phone": first.id}));
    assert_eq!(refused["ok"], false, "{refused}");
    assert!(refused["error"]["message"].as_str().unwrap().contains("only admin of your pool"), "{refused}");
    let allowed = rig.call("phone_remove", "", &token, json!({"phone": spare.id}));
    assert_eq!(allowed["ok"], true, "a phone that is not the pool's admin may be removed: {allowed}");
}

#[test]
fn a_chain_put_in_after_the_phone_signed_its_addition_is_refused() {
    let root = Arc::new(Root::new());
    let (rig, _token) = trusting(&root);
    let phone = FakePhone::new("Pixel");
    let secret = rig.offer();
    let mut body = phone.add_body(&rig.device(), &secret);
    body["attestation"] = json!(root.chain(&phone.pkcs8, &challenge(&rig.device(), &secret), Claims::default()));
    let (_, reply) = rig.phone("POST", "add", "", body);
    assert_eq!(reply["ok"], false, "{reply}");
    assert!(reply["error"]["message"].as_str().unwrap().contains("did not prove it holds its key"), "{reply}");
}
