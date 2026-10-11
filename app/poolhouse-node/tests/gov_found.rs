//! Founding a pool from an act a person's keys signed elsewhere: which charters found one, and who may ask.

mod kit;

use kit::gov::*;
use kit::govnode::*;
use kit::grants::{enrolled, gid_of, person as grantor, Spec};
use kit::netkit::*;
use poolhouse_node::gov::charter::Unit;
use poolhouse_node::membership::Policy;
use serde_json::{json, Value};

fn founding(keys: &[K]) -> Value {
    serde_json::to_value(found_act(&charter(keys, Policy::Secure, Unit::Person))).unwrap()
}

#[test]
fn a_charter_with_no_strong_admin_key_founds_nothing_because_nobody_could_admit_a_device() {
    let a = device();
    let legacy = a.pool();
    let reply = a.call("pool_found", "", &a.token, json!({"act": founding(&[weak(ANN, "ann")])}));
    assert_eq!(reply["ok"], false, "{reply}");
    assert!(reply["error"]["say"].as_str().unwrap().contains("strong"), "{reply}");
    assert_eq!(a.pool(), legacy, "the pool of one it had is still its pool");
    assert_eq!(a.ok("pool_found", "", &a.token, json!({"act": founding(&[strong(ANN, "ann")])}))["policy"], "secure");
}

#[test]
fn pool_found_asks_for_the_grant_by_the_name_of_the_pool_it_founds() {
    let a = device();
    let (name, token) = a.session("demo", "scoped");
    let mut call = |m: &str, b: &str, t: &str, p: Value| a.call(m, b, t, p);
    enrolled(a.dir.path(), &mut call, &token);
    let reply = grantor().issue(&mut call, "demo", &token, gid_of(&name, "pool_create").as_str(), &Spec::session(&name, "pool_create").on("other"));
    assert_eq!(reply["ok"], true, "{reply}");
    let denied = a.call("pool_found", "", &token, json!({"act": founding(&[strong(ANN, "ann")])}));
    assert_eq!(denied["ok"], false, "a grant for another pool's name does not found this one: {denied}");
    let reply = grantor().issue(&mut call, "demo", &token, gid_of(&format!("{name} again"), "pool_create").as_str(), &Spec::session(&name, "pool_create").on("home"));
    assert_eq!(reply["ok"], true, "{reply}");
    let founded = a.call("pool_found", "", &token, json!({"act": founding(&[strong(ANN, "ann")])}));
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
