//! What a person's signature is good for once it is made: one node, one use, one terminal. A key that was removed is not
//! enrolled again, so nothing it signed, whatever time the entry claims, counts.
#![cfg(unix)]

mod kit;

use kit::grants::{agent, anchor_of, enrolled, fresh_nonce, my_anchor, now_ms, person, third, Person, Spec};
use kit::procs::*;
use kit::*;
use poolhouse_node::grants::{check, Scope};
use poolhouse_node::identity::Holder;
use poolhouse_node::node::Node;
use poolhouse_node::row::Kind;
use serde_json::{json, Value};
use tempfile::tempdir;

/// A node that holds the test person's key and ``extra`` keys approved by it; a session of board `demo` and the node's device.
fn keyed(dir: &std::path::Path, extra: &[&Person]) -> (Node, String, String) {
    let mut n = node(dir);
    let (name, token) = session(&mut n, "demo", "a");
    enrolled(dir, &mut caller(&mut n), &token);
    let device = n.cert.fingerprint();
    for key in extra {
        ok(req(&mut n, "person_enroll", "", &token, key.enrolment("another key", Some((&person(), &device)))));
    }
    (n, name, token)
}

#[test]
fn a_removal_is_signed_for_one_node_and_is_used_once() {
    let (a, b) = (tempdir().unwrap(), tempdir().unwrap());
    let (mut na, _, token_a) = keyed(a.path(), &[&agent(), &third()]);
    let (mut nb, _, token_b) = keyed(b.path(), &[&agent(), &third()]);
    let (device_a, device_b) = (na.cert.fingerprint(), nb.cert.fingerprint());
    let (first, second, other) = (person(), agent(), third());

    let signed_for_a = first.unenroll_params_with(&second.fingerprint(), &device_a, now_ms(), &fresh_nonce(), &my_anchor());
    let carried = req(&mut nb, "person_unenroll", "", &token_b, signed_for_a.clone());
    assert_eq!(code(&carried), "denied", "a removal signed for one node removes nothing on another: {carried}");
    assert_eq!(held(&mut nb).len(), 3);
    ok(req(&mut na, "person_unenroll", "", &token_a, signed_for_a));
    assert_eq!(held(&mut na).len(), 2);

    let nonce = fresh_nonce();
    let once = first.unenroll_params_with(&other.fingerprint(), &device_b, now_ms(), &nonce, &my_anchor());
    ok(req(&mut nb, "person_unenroll", "", &token_b, once));
    let again = first.unenroll_params_with(&second.fingerprint(), &device_b, now_ms(), &nonce, &my_anchor());
    assert_eq!(code(&req(&mut nb, "person_unenroll", "", &token_b, again)), "denied", "a nonce signs one removal");
    assert_eq!(held(&mut nb).len(), 2);
}

#[test]
fn a_used_nonce_outlasts_a_restart_of_the_node() {
    let dir = tempdir().unwrap();
    let nonce = fresh_nonce();
    let (first, second, other) = (person(), agent(), third());
    let device = {
        let (mut n, _, token) = keyed(dir.path(), &[&second, &other]);
        let device = n.cert.fingerprint();
        ok(req(&mut n, "person_unenroll", "", &token, first.unenroll_params_with(&second.fingerprint(), &device, now_ms(), &nonce, &my_anchor())));
        device
    };
    let mut n = node(dir.path());
    let (_, token) = session(&mut n, "demo", "b");
    let replay = first.unenroll_params_with(&other.fingerprint(), &device, now_ms(), &nonce, &my_anchor());
    assert_eq!(code(&req(&mut n, "person_unenroll", "", &token, replay)), "denied", "the node does not forget a nonce it took ten minutes ago");
    assert_eq!(held(&mut n).len(), 2);
}

#[test]
fn an_approval_and_a_removal_are_for_the_terminal_the_person_signed_in() {
    let dir = tempdir().unwrap();
    let (mut n, _, token) = keyed(dir.path(), &[&agent()]);
    let device = n.cert.fingerprint();
    let (first, second, other) = (person(), agent(), third());
    let elsewhere = process(true);
    let there = anchor_of(elsewhere.id());

    let wrong = other.enrolment_for("a tablet", Some((&first, &device)), &there);
    let refused = req(&mut n, "person_enroll", "", &token, wrong);
    assert_eq!(code(&refused), "denied", "an approval signed in another terminal enrols nothing here: {refused}");
    let mut unsigned = other.enrolment("a tablet", Some((&first, &device)));
    unsigned.as_object_mut().unwrap().remove("anchor");
    assert_eq!(req(&mut n, "person_enroll", "", &token, unsigned)["ok"], false, "an approval names its terminal");
    let swapped = {
        let mut p = other.enrolment_for("a tablet", Some((&first, &device)), &there);
        p["anchor"] = my_anchor();
        p
    };
    assert_eq!(code(&req(&mut n, "person_enroll", "", &token, swapped)), "denied", "naming another terminal does not change what was signed");
    assert_eq!(held(&mut n).len(), 2);
    ok(req(&mut n, "person_enroll", "", &token, other.enrolment("a tablet", Some((&first, &device)))));
    assert_eq!(held(&mut n).len(), 3, "an approval signed for this terminal enrols the key");

    let removal = first.unenroll_params_with(&second.fingerprint(), &device, now_ms(), &fresh_nonce(), &there);
    assert_eq!(code(&req(&mut n, "person_unenroll", "", &token, removal)), "denied", "a removal signed in another terminal removes nothing here");
    assert_eq!(held(&mut n).len(), 3);
    ok(req(&mut n, "person_unenroll", "", &token, first.unenroll_params(&second.fingerprint(), &device)));
    assert_eq!(held(&mut n).len(), 2);
    done(vec![elsewhere]);
}

#[test]
fn a_removed_key_is_not_enrolled_again_and_nothing_it_signed_ahead_of_time_comes_back() {
    let dir = tempdir().unwrap();
    let (mut n, name, token) = keyed(dir.path(), &[&agent()]);
    let device = n.cert.fingerprint();
    let (first, second) = (person(), agent());
    let me = Holder { board: "demo".into(), name: name.clone() };
    ok(req(&mut n, "person_unenroll", "", &token, first.unenroll_params(&second.fingerprint(), &device)));
    std::thread::sleep(std::time::Duration::from_millis(5));

    let mut forged = second.issue_params("demo", "g-00000000000000c5", &Spec::session(&name, "note_verify")).as_object().unwrap().clone();
    forged.insert("op".into(), json!("issue"));
    forged.insert("board".into(), json!("demo"));
    n.host("demo").unwrap().board.append(Kind::Grant, "claude-bbbbbb", Value::Object(forged), "").unwrap();
    assert!(!check(&mut n, &me, "note_verify", &Scope::any()).unwrap(), "a key that is not enrolled signs no grant");

    let again = req(&mut n, "person_enroll", "", &token, second.enrolment("a laptop", Some((&first, &device))));
    assert_eq!(code(&again), "denied", "a removed key is made again as a new key: {again}");
    assert!(!check(&mut n, &me, "note_verify", &Scope::any()).unwrap(), "so what it signed with a time after its removal stays void");
    let shown = ok(req(&mut n, "grants", "demo", &token, json!({})));
    assert_eq!(shown["grants"][0]["status"], "ignored", "{shown}");
    drop(n);
    let mut n = node(dir.path());
    assert_eq!(code(&req(&mut n, "person_enroll", "", &token, second.enrolment("a laptop", Some((&first, &device))))), "denied", "and the removal outlasts a restart");
    assert_eq!(held(&mut n), [first.fingerprint()]);
}
