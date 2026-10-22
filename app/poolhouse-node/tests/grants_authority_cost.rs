//! The fixed cost of folding a board's grants in a pool of sixteen admin keys: a signature is checked once, so a board that grew many rows
//! costs the first fold and little after it.
mod kit;

use std::time::{Duration, Instant};

use kit::grants::Spec;
use kit::govnode::{enrol_phone_key, inject_approved};
use kit::netkit::device;
use poolhouse_node::gov::signer::RigSigner;
use serde_json::json;

const KEYS: u8 = 16;
const ROWS: usize = 300;

#[test]
fn the_fold_of_many_grant_rows_in_a_pool_of_sixteen_admin_keys_checks_each_signature_once() {
    let dev = device();
    let mut signer = RigSigner::new();
    for seed in 1..=KEYS {
        signer = signer.with_key(seed, "admin");
    }
    for seed in 1..=7 {
        enrol_phone_key(&dev, seed);
    }
    dev.node.lock().unwrap().set_signer(std::sync::Arc::new(signer));
    dev.ok("pool_create", "", &dev.token, json!({"title": "home", "policy": "secure", "project": ""}));
    let listed = dev.node.lock().unwrap().members.gov().unwrap().state().chain.latest().charter.admins.len();
    assert_eq!(listed, KEYS as usize, "the charter lists sixteen admin keys");
    let (name, token) = dev.session("demo", "folder");
    for i in 0..ROWS {
        inject_approved(&dev, "demo", &format!("g-{i:016x}"), &Spec::session(&name, "sync_now"), &name);
    }
    let fold = || {
        let began = Instant::now();
        let listing = dev.ok("grants", "demo", &token, json!({}));
        assert!(listing["grants"].as_array().unwrap().iter().any(|g| g["status"] == "live"), "the rows count");
        began.elapsed()
    };
    let first = fold();
    let later: Duration = (0..9).map(|_| fold()).sum();
    assert!(later < first * 3 + Duration::from_millis(250), "nine more folds took {later:?} after a first of {first:?}: a signature is checked again on every fold");
}
