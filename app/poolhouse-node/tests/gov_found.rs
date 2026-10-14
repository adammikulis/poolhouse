//! Founding a pool from an act a person's keys signed elsewhere: which charters found one, and who may ask.

mod kit;

use kit::gov::*;
use kit::govnode::*;
use kit::grants::{enrolled, gid_of, person as grantor, Spec};
use kit::netkit::*;
use poolhouse_node::fsutil::unhex;
use poolhouse_node::gov::signer::RigSigner;
use poolhouse_node::membership::Policy;
use serde_json::{json, Value};

/// The founding act of a charter of ``keys`` that puts the device ``a`` in the pool first.
fn founding(a: &Dev, keys: &[K]) -> Value {
    serde_json::to_value(found_act(&charter_for(a, keys, Policy::Secure))).unwrap()
}

#[test]
fn a_charter_with_no_strong_admin_key_founds_nothing_because_nobody_could_admit_a_device() {
    let a = device();
    let before = a.pool();
    let reply = a.call("pool_found", "", &a.token, json!({"act": founding(&a, &[weak(ANN, "ann")])}));
    assert_eq!(reply["ok"], false, "{reply}");
    assert!(reply["error"]["say"].as_str().unwrap().contains("strong"), "{reply}");
    assert_eq!(a.pool(), before, "it is still in no pool");
    enrol_phone_key(&a, ANN);
    assert_eq!(a.ok("pool_found", "", &a.token, json!({"act": founding(&a, &[strong(ANN, "ann")])}))["policy"], "secure");
}

#[test]
fn pool_found_asks_for_the_grant_by_the_name_of_the_pool_it_founds() {
    let a = device();
    enrol_phone_key(&a, ANN);
    let (name, token) = a.session("demo", "scoped");
    let mut call = |m: &str, b: &str, t: &str, p: Value| a.call(m, b, t, p);
    enrolled(a.dir.path(), &mut call, &token);
    let reply = grantor().issue(&mut call, "demo", &token, gid_of(&name, "pool_create").as_str(), &Spec::session(&name, "pool_create").on("other"));
    assert_eq!(reply["ok"], true, "{reply}");
    let denied = a.call("pool_found", "", &token, json!({"act": founding(&a, &[strong(ANN, "ann")])}));
    assert_eq!(denied["ok"], false, "a grant for another pool's name does not found this one: {denied}");
    let reply = grantor().issue(&mut call, "demo", &token, gid_of(&format!("{name} again"), "pool_create").as_str(), &Spec::session(&name, "pool_create").on("home"));
    assert_eq!(reply["ok"], true, "{reply}");
    let founded = a.call("pool_found", "", &token, json!({"act": founding(&a, &[strong(ANN, "ann")])}));
    assert_eq!(founded["ok"], true, "{founded}");
}

#[test]
fn a_join_whose_history_does_not_fit_the_frame_is_refused_whole_and_says_so() {
    let (_d, mut g) = pool(&[strong(1, "ann")], Policy::Secure);
    for n in 0..4u64 {
        put(&mut g, "set_holds", json!({"dispute_d": 8 + n}), &[1]);
    }
    let joiner = ident(40);
    put_admit(&mut g, &joiner, &[1]);
    assert!(g.state().chain.latest().n >= 5, "each longer dispute is in force at once");
    assert!(g.join_payload(1 << 20, &joiner.fingerprint()).is_ok());
    let refused = g.join_payload(600, &joiner.fingerprint());
    assert!(refused.is_err_and(|e| e.to_string().contains("too long")), "a history that does not fit is not cut short");
}

fn drafted(a: &Dev, policy: &str, keys: &[(u8, &str)]) -> Value {
    keys.iter().for_each(|(seed, _)| enrol_phone_key(a, *seed));
    let listed: Vec<Value> = keys.iter().map(|(seed, person)| json!({"key": public(*seed), "person": person, "label": format!("{person}'s key")})).collect();
    a.ok("pool_draft_found", "", &a.token, json!({"title": "home", "policy": policy, "project": "", "keys": listed}))
}

/// The charter of a draft with each key's acceptance signed outside the node by the key made from its seed.
fn accepted(draft: &Value, seeds: &[u8]) -> Value {
    let mut charter = draft["charter"].clone();
    for entry in draft["accept"].as_array().unwrap() {
        let key = entry["key"].as_str().unwrap();
        let seed = *seeds.iter().find(|s| public(**s) == key).unwrap();
        let sig = RigSigner::sign_as(seed, &unhex(entry["sign_bytes"].as_str().unwrap()).unwrap());
        charter["admins"].as_array_mut().unwrap().iter_mut().find(|k| k["key"] == key).unwrap()["acceptance"] = json!(sig);
    }
    charter
}

#[test]
fn a_node_drafts_a_charter_whose_acceptances_are_signed_outside_it_and_founds_the_pool_of_it() {
    let a = device();
    let draft = drafted(&a, "open", &[(ANN, "ann"), (BEN, "ben")]);
    assert_eq!(draft["charter"]["admins"].as_array().unwrap().len(), 2, "{draft}");
    assert!(draft["accept"].as_array().unwrap().iter().all(|e| e["sign_bytes"].as_str().is_some_and(|b| !b.is_empty())), "{draft}");
    let founded = a.ok("pool_found", "", &a.token, json!({"charter": accepted(&draft, &[ANN, BEN])}));
    assert_eq!(founded["policy"], "open", "{founded}");
    assert_eq!(a.status()["governance"]["admins"].as_array().unwrap().len(), 2);
    assert_eq!(a.pool(), founded["pool"].as_str().unwrap());
}

#[test]
fn a_charter_a_key_did_not_accept_founds_nothing() {
    let a = device();
    let before = a.pool();
    let draft = drafted(&a, "secure", &[(ANN, "ann"), (BEN, "ben")]);
    let mut charter = accepted(&draft, &[ANN, BEN]);
    charter["admins"].as_array_mut().unwrap().iter_mut().find(|k| k["key"] == public(BEN)).unwrap()["acceptance"] = json!(RigSigner::sign_as(ANN, b"not what ben accepts"));
    let refused = a.call("pool_found", "", &a.token, json!({"charter": charter}));
    assert_eq!(refused["ok"], false, "{refused}");
    assert_eq!(a.pool(), before, "it is still in no pool");
    let both = a.call("pool_found", "", &a.token, json!({"charter": accepted(&draft, &[ANN, BEN]), "act": founding(&a, &[strong(ANN, "ann")])}));
    assert_eq!(both["ok"], false, "a charter and an act together name two things to found: {both}");
}

#[test]
fn a_draft_names_no_key_that_is_not_an_ed25519_key() {
    let a = device();
    let bad = a.call("pool_draft_found", "", &a.token, json!({"title": "home", "policy": "secure", "project": "", "keys": [{"key": "ab".repeat(32), "person": "ann", "label": ""}]}));
    assert_eq!(bad["ok"], false, "{bad}");
    let none = a.call("pool_draft_found", "", &a.token, json!({"title": "home", "policy": "secure", "project": "", "keys": []}));
    assert_eq!(none["ok"], false, "{none}");
}
