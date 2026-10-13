//! A key's strength is the node's to derive: strong only for a key a phone enrolled on this device, whatever a charter says.

mod kit;

use kit::gov::*;
use kit::govnode::*;
use kit::grants::{enrolled, person as grantor};
use kit::netkit::*;
use poolhouse_node::gov::charter::Unit;
use poolhouse_node::membership::Policy;
use serde_json::{json, Value};

const NO_STRONG_KEY: &str = "A pool needs a strong key, one that asks for your presence each time it signs, and that charter lists none.";

fn act_of(keys: &[K]) -> Value {
    serde_json::to_value(found_act(&charter(keys, Policy::Secure, Unit::Person))).unwrap()
}

fn draft(a: &Dev, keys: Value) -> Value {
    a.call("pool_draft_found", "", &a.token, json!({"title": "home", "policy": "secure", "project": "", "keys": keys}))
}

#[test]
fn a_charter_that_calls_an_unenrolled_key_strong_founds_nothing() {
    let a = device();
    let before = a.pool();
    let reply = a.call("pool_found", "", &a.token, json!({"act": act_of(&[strong(ANN, "ann")])}));
    assert_eq!(reply["ok"], false, "{reply}");
    assert_eq!(reply["error"]["say"], NO_STRONG_KEY, "{reply}");
    assert_eq!(a.pool(), before, "it is still in no pool");
    assert_eq!(a.status()["governance"]["mode"], "unfounded");
}

#[test]
fn the_same_charter_handed_over_whole_founds_nothing() {
    let a = device();
    let body = charter(&[strong(ANN, "ann")], Policy::Secure, Unit::Person).body();
    let reply = a.call("pool_found", "", &a.token, json!({"charter": body}));
    assert_eq!(reply["ok"], false, "{reply}");
    assert_eq!(reply["error"]["say"], NO_STRONG_KEY, "{reply}");
    assert_eq!(a.status()["governance"]["mode"], "unfounded");
}

#[test]
fn a_key_a_person_enrolled_with_a_typed_yes_is_not_strong() {
    let a = device();
    let typed = grantor().public();
    let mut call = |m: &str, b: &str, t: &str, p: Value| a.call(m, b, t, p);
    enrolled(a.dir.path(), &mut call, &a.token);
    let held = a.ok("person_keys", "", "", json!({}));
    assert!(held["keys"].as_array().unwrap().iter().any(|k| k["fingerprint"] == grantor().fingerprint().as_str()), "the typed key is enrolled on this node: {held}");
    let reply = a.call("pool_found", "", &a.token, json!({"act": act_of(&[strong(0x70, "ann")])}));
    assert_eq!(reply["ok"], false, "a key the person typed yes for is a key a process of the account can use: {reply}");
    let drafted = a.ok("pool_draft_found", "", &a.token, json!({"title": "home", "policy": "secure", "project": "", "keys": [{"key": typed, "person": "ann", "label": "x"}]}));
    assert_eq!(drafted["charter"]["admins"][0]["strength"], "weak", "{drafted}");
}

#[test]
fn a_draft_that_names_a_strength_is_refused() {
    let a = device();
    for said in ["strong", "weak"] {
        let reply = draft(&a, json!([{"key": public(ANN), "person": "ann", "strength": said, "label": "x"}]));
        assert_eq!(reply["ok"], false, "{said}: {reply}");
    }
    let unknown = draft(&a, json!([{"key": public(ANN), "person": "ann", "label": "x", "role": "recovery"}]));
    assert_eq!(unknown["ok"], false, "a key is named by its key, person and label: {unknown}");
}

#[test]
fn a_draft_gives_every_key_the_strength_the_node_derives_and_the_pool_it_founds_keeps_it() {
    let a = device();
    let now = poolhouse_node::poolops::wall_ms();
    a.node.lock().unwrap().persons.add_phone_key("ed25519", &public(ANN), "ann's phone", now).unwrap();
    let drafted = a.ok("pool_draft_found", "", &a.token, json!({"title": "home", "policy": "secure", "project": "", "keys": [
        {"key": public(ANN), "person": "ann", "label": "x"}, {"key": public(BEN), "person": "ben", "label": "y"}]}));
    let strength = |key: &str| drafted["charter"]["admins"].as_array().unwrap().iter().find(|k| k["key"] == key).unwrap()["strength"].clone();
    assert_eq!((strength(&public(ANN)), strength(&public(BEN))), (json!("strong"), json!("weak")), "{drafted}");
}

#[test]
fn one_enrolled_key_does_not_vouch_for_another_the_charter_calls_strong() {
    let a = device();
    let now = poolhouse_node::poolops::wall_ms();
    a.node.lock().unwrap().persons.add_phone_key("ed25519", &public(ANN), "ann's phone", now).unwrap();
    let both = a.call("pool_found", "", &a.token, json!({"act": act_of(&[strong(ANN, "ann"), strong(BEN, "ben")])}));
    assert_eq!(both["ok"], false, "{both}");
    assert!(both["error"]["say"].as_str().unwrap().contains("not a phone"), "{both}");
    assert_eq!(a.status()["governance"]["mode"], "unfounded");
    let recovery = a.call("pool_found", "", &a.token, json!({"act": act_of(&[strong(ANN, "ann"), recovery(BEN, "ann")])}));
    assert_eq!(recovery["ok"], false, "a recovery key the charter calls strong is held to the same rule: {recovery}");
    let honest = a.call("pool_found", "", &a.token, json!({"act": act_of(&[strong(ANN, "ann"), weak(BEN, "ben")])}));
    assert_eq!(honest["ok"], true, "a key the charter calls weak needs no enrolment: {honest}");
}

#[test]
fn a_phone_key_that_was_removed_is_strong_no_more() {
    let a = device();
    let now = poolhouse_node::poolops::wall_ms();
    let fp = a.node.lock().unwrap().persons.add_phone_key("ed25519", &public(ANN), "ann's phone", now).unwrap();
    a.node.lock().unwrap().persons.remove(&fp, now + 1).unwrap();
    let reply = a.call("pool_found", "", &a.token, json!({"act": act_of(&[strong(ANN, "ann")])}));
    assert_eq!(reply["ok"], false, "{reply}");
}

#[test]
fn a_session_without_the_grant_learns_nothing_about_which_keys_a_phone_enrolled() {
    let (knows, ignorant) = (device(), device());
    enrol_phone_key(&knows, ANN);
    let act = act_of(&[strong(ANN, "ann")]);
    let ask = |d: &Dev| {
        let (_name, token) = d.session("demo", "ungranted");
        d.call("pool_found", "", &token, json!({"act": act.clone()}))
    };
    let (held, not_held) = (ask(&knows), ask(&ignorant));
    for reply in [&held, &not_held] {
        assert_eq!(reply["ok"], false, "{reply}");
        assert!(reply["error"]["message"].as_str().is_some_and(|m| m.contains("pool_create")), "the refusal is the missing grant: {reply}");
    }
    let shown = |reply: &Value| (reply["error"]["code"].clone(), reply["error"]["message"].clone(), reply["error"]["grant"]["action"].clone(), reply["error"]["grant"]["resource"].clone());
    assert_eq!(shown(&held), shown(&not_held), "the same answer whether or not the key is a phone's here");
    assert_eq!((knows.status()["governance"]["mode"].as_str(), ignorant.status()["governance"]["mode"].as_str()), (Some("unfounded"), Some("unfounded")));
}

#[test]
fn a_founding_act_handed_to_the_other_pool_calls_founds_nothing() {
    let a = device();
    let act = act_of(&[strong(ANN, "ann")]);
    let submitted = a.call("pool_submit", "", &a.token, json!({"act": act}));
    assert_eq!(submitted["ok"], false, "{submitted}");
    let cosigned = a.call("pool_cosign", "", &a.token, json!({"act": "x"}));
    assert_eq!(cosigned["ok"], false, "{cosigned}");
    assert_eq!(a.status()["governance"]["mode"], "unfounded");
}

fn sources(dir: &std::path::Path, found: &mut Vec<std::path::PathBuf>) {
    for entry in std::fs::read_dir(dir).unwrap().flatten() {
        match entry.path() {
            p if p.is_dir() => sources(&p, found),
            p if p.extension().is_some_and(|e| e == "rs") => found.push(p),
            _ => {}
        }
    }
}

type Sources = Vec<(std::path::PathBuf, String)>;

/// The files, as paths under `src`, that name `add_phone_key` in any form (a method call, a path, a function value, a `use`) and are neither
/// its definition nor the phone's enrolment.
fn naming_it_outside_the_phone(files: &Sources) -> Vec<std::path::PathBuf> {
    let own = std::path::Path::new("grants").join("person.rs");
    files.iter().filter(|(path, text)| text.contains("add_phone_key") && *path != own && !path.starts_with("phone")).map(|(path, _)| path.clone()).collect()
}

#[test]
fn only_a_phones_own_enrolment_marks_a_key_as_a_phones() {
    let src = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut paths = Vec::new();
    sources(&src, &mut paths);
    let files: Sources = paths.iter().map(|p| (p.strip_prefix(&src).unwrap().to_path_buf(), std::fs::read_to_string(p).unwrap())).collect();
    assert_eq!(naming_it_outside_the_phone(&files), Vec::<std::path::PathBuf>::new(), "a key is enrolled as a phone's only by the phone's enrolment");
    assert!(files.iter().any(|(path, text)| path.starts_with("phone") && text.contains("add_phone_key")), "the phone's enrolment is what the scan expects to find");
}

#[test]
fn the_scan_catches_every_way_of_naming_the_phone_enrolment() {
    let planted = |text: &str| -> Sources { vec![(std::path::Path::new("gov").join("ops.rs"), text.to_string())] };
    for text in [
        "node.persons.add_phone_key(ED25519, key, label, now)?;",
        "Persons::add_phone_key(&mut node.persons, ED25519, key, label, now)?;",
        "Persons::add_phone_key ::<>(&mut p, a, b, c, d)?;",
        "let enrol = Persons::add_phone_key;",
        "use crate::grants::person::Persons as P; P::add_phone_key(&mut p, a, b, c, d)",
    ] {
        assert_eq!(naming_it_outside_the_phone(&planted(text)).len(), 1, "{text}");
    }
    let allowed: Sources = [("grants/person.rs", "pub fn add_phone_key"), ("phone/mod.rs", "node.persons.add_phone_key("), ("phone/enrol.rs", "add_phone_key")]
        .iter().map(|(p, t)| (std::path::PathBuf::from(p), t.to_string())).collect();
    assert!(naming_it_outside_the_phone(&allowed).is_empty());
    let lookalike: Sources = vec![(std::path::Path::new("phonebook").join("mod.rs"), "x.add_phone_key(".into())];
    assert_eq!(naming_it_outside_the_phone(&lookalike).len(), 1, "a path that only starts with the word phone is not the phone");
}

#[test]
fn a_signer_whose_key_no_phone_enrolled_founds_nothing_and_leaves_nothing_behind() {
    let a = device();
    let before = a.pool();
    a.node.lock().unwrap().set_signer(person(ANN, "ann"));
    let denied = a.call("pool_create", "", &a.token, json!({"title": "home"}));
    assert_eq!(denied["ok"], false, "{denied}");
    assert!(denied["error"]["say"].as_str().unwrap().contains("asks for your presence"), "{denied}");
    assert_eq!(a.pool(), before);
    assert!(!a.dir.path().join("pools").exists() || std::fs::read_dir(a.dir.path().join("pools")).unwrap().count() == 0);
}
