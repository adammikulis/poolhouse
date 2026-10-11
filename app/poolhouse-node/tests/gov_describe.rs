//! What a person is shown when asked to sign an act: the kind, and what it names, in a sentence that carries the key, the numbers and
//! the device, so a person cannot be asked to sign one thing under the name of another.

mod kit;

use kit::gov::*;
use kit::govnode::*;
use kit::netkit::*;
use serde_json::{json, Value};

fn summary(dev: &Dev, kind: &str, body: Value) -> String {
    dev.ok("pool_draft", "", &dev.token, json!({"kind": kind, "body": body}))["summary"].as_str().unwrap().to_string()
}

#[test]
fn an_admin_add_names_the_person_the_strength_and_the_key() {
    let a = device();
    let id = found(&a, person(ANN, "ann"), "secure");
    let added = entry(strong(BEN, "ben"), 2, &id);
    let say = summary(&a, "admin_add", json!({"entry": added}));
    assert!(say.contains("ben") && say.contains("strong") && say.contains(&added.fingerprint()[..12]), "{say}");
    let weak_one = entry(weak(BEN, "ben"), 2, &id);
    assert!(summary(&a, "admin_add", json!({"entry": weak_one})).contains("weak"));
}

#[test]
fn an_admin_remove_names_the_key_and_its_person() {
    let a = device();
    found(&a, person(ANN, "ann"), "secure");
    let say = summary(&a, "admin_remove", json!({"key": public(ANN), "cut": []}));
    let current = a.node.lock().unwrap().members.gov().unwrap().state().chain.latest().charter.admins[0].fingerprint();
    assert!(say.contains("ann") && say.contains(&current[..12]), "{say}");
    let stranger = summary(&a, "admin_remove", json!({"key": public(99), "cut": []}));
    assert!(stranger.contains("does not list"), "{stranger}");
}

#[test]
fn a_threshold_a_hold_and_a_mode_say_the_numbers_they_set() {
    let a = device();
    found(&a, person(ANN, "ann"), "secure");
    let threshold = summary(&a, "set_threshold", json!({"routine": 2, "sensitive": 3, "unit": "key"}));
    assert!(threshold.contains("routine 2") && threshold.contains("sensitive 3") && threshold.contains("key"), "{threshold}");
    let holds = summary(&a, "set_holds", json!({"admin_add_h": 1, "dispute_d": 30}));
    assert!(holds.contains("adding an admin 1 hour") && holds.contains("a dispute 30 days"), "{holds}");
    assert!(summary(&a, "set_mode", json!({"mode": "members"})).contains("members"));
}

#[test]
fn an_admit_and_a_removal_name_the_device_and_its_certificate() {
    let a = device();
    found(&a, person(ANN, "ann"), "secure");
    let guest = ident(40);
    let body = admit_body(&guest, "guest-laptop", "join-code", "");
    let say = summary(&a, "admit", body);
    assert!(say.contains("guest-laptop") && say.contains(&guest.fingerprint()[..12]), "{say}");
    let removal = summary(&a, "remove", json!({"fingerprint": guest.fingerprint()}));
    assert!(removal.contains(&guest.fingerprint()[..12]), "a removal of a device this pool does not know names its certificate: {removal}");
}
