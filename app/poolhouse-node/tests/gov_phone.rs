//! A person's phone signs the acts of a pool: the card the node writes from the structured act, the signature over the act's own bytes,
//! and what a wrong, replayed, swapped or declined answer does. The phone is made of keys (`kit::phone`); nothing in the node knows it.

mod kit;

use kit::phone::{FakePhone, Rig};
use poolhouse_node::fsutil::{hex, sha256_hex, unhex};
use serde_json::{json, Value};

fn linked() -> (Rig, FakePhone, String) {
    let rig = Rig::new();
    let mut phone = FakePhone::new("Pixel");
    rig.add(&mut phone, None);
    let (_, token) = rig.session("pool", "agent-one");
    (rig, phone, token)
}

fn ask_found(rig: &Rig, token: &str, name: &str, policy: &str) -> Value {
    rig.call("pool_ask_found", "", token, json!({"title": name, "policy": policy, "project": ""}))
}

fn id_of(reply: &Value) -> String {
    assert_eq!(reply["ok"], true, "{reply}");
    reply["result"]["id"].as_str().unwrap().to_string()
}

/// The `decide` body of ``phone`` approving the request ``view`` by signing ``bytes``.
fn approve_with(phone: &FakePhone, view: &Value, bytes: &[u8]) -> Value {
    json!({"id": view["id"], "decision": "approve", "key": phone.fingerprint(), "sig": phone.sign(bytes), "text_sha256": sha256_hex(view["text"].as_str().unwrap().as_bytes())})
}

/// The bytes the request asks its key to sign, as the phone reads them from the signed action.
fn message(view: &Value) -> Vec<u8> {
    unhex(view["challenge"]["action"]["message"].as_str().unwrap()).unwrap()
}

fn tap(rig: &Rig, phone: &FakePhone, id: &str) -> Value {
    let view = rig.get(phone, id);
    rig.phone("POST", "decide", "", approve_with(phone, &view, &message(&view))).1
}

fn governance(rig: &Rig) -> Value {
    rig.call("pool_status", "", "", json!({}))["result"]["governance"].clone()
}

fn own_cert(rig: &Rig) -> String {
    hex(&rig.node.lock().unwrap().cert.der.clone())
}

fn found(rig: &Rig, phone: &FakePhone, token: &str, policy: &str) {
    let id = id_of(&ask_found(rig, token, "home", policy));
    let done = tap(rig, phone, &id);
    assert_eq!(done["ok"], true, "{done}");
}

#[test]
fn a_phone_founds_a_pool_with_its_own_key_as_the_strong_admin_and_admits_this_device() {
    let (rig, phone, token) = linked();
    let reply = ask_found(&rig, &token, "home", "secure");
    let id = id_of(&reply);
    let text = reply["result"]["text"].as_str().unwrap();
    assert!(reply["result"]["title"].as_str().unwrap().starts_with("Found the pool home, secure: you will be its first admin"), "{reply}");
    for label in ["POOL: SECURE.", "RISK: SENSITIVE.", "WHO:", "ASK: Found the pool 'home', secure.", "KEY: Your phone's key ", "SCOPE:", "CANNOT:", "WHY:", "DENY:", "EXPIRES:"] {
        assert!(text.contains(label), "the card says {label}:\n{text}");
    }
    assert_eq!(governance(&rig)["mode"], "unfounded", "nothing is founded until the phone signs");
    let done = tap(&rig, &phone, &id);
    assert_eq!((done["ok"].clone(), done["result"]["state"].clone()), (json!(true), json!("approved")), "{done}");
    let gov = governance(&rig);
    assert_eq!((gov["mode"].as_str(), gov["name"].as_str()), (Some("governed"), Some("home")), "{gov}");
    assert_eq!(gov["admins"][0]["strength"], "strong");
    assert_eq!(gov["admins"][0]["fingerprint"], phone.fingerprint(), "the phone's key is the admin");
    assert_eq!(gov["weak_keys_only"], false);
    let admit = rig.call("pool_ask_sign", "", &token, json!({"kind": "admit", "body": {"cert": own_cert(&rig), "name": "", "how": "found", "via": ""}}));
    let (admit_id, card) = (id_of(&admit), admit["result"]["text"].as_str().unwrap().to_string());
    assert!(card.contains("ASK: Let the device") && card.contains("join the pool."), "{card}");
    assert_eq!(rig.call("pool_status", "", "", json!({}))["result"]["members"].as_array().map_or(0, Vec::len), 0, "this device is no member until the phone signs");
    assert_eq!(tap(&rig, &phone, &admit_id)["ok"], true);
    let members = rig.call("pool_status", "", "", json!({}))["result"]["members"].clone();
    assert_eq!(members.as_array().unwrap().len(), 1, "{members}");
}

#[test]
fn the_card_names_the_pool_its_policy_and_the_key_in_plain_ascii() {
    let (rig, _phone, token) = linked();
    let open = ask_found(&rig, &token, "caf\u{e9} home", "open");
    let text = open["result"]["text"].as_str().unwrap();
    assert!(text.contains("POOL: OPEN. Devices on the same network join without asking."), "{text}");
    assert!(text.contains("'caf- home'") && text.is_ascii(), "a name that is not ASCII is shown with hyphens, never sent raw:\n{text}");
    assert!(text.lines().all(|l| l.len() < 400));
}

#[test]
fn a_decline_signs_the_challenge_and_founds_nothing_and_asking_again_waits() {
    let (rig, phone, token) = linked();
    let id = id_of(&ask_found(&rig, &token, "home", "secure"));
    let view = rig.get(&phone, &id);
    let denied = rig.phone("POST", "decide", "", phone.decision(&view, "deny")).1;
    assert_eq!(denied["result"]["state"], "denied", "{denied}");
    assert_eq!(governance(&rig)["mode"], "unfounded");
    let again = ask_found(&rig, &token, "home", "secure");
    assert_eq!(again["ok"], false, "{again}");
    assert!(again["error"]["say"].as_str().unwrap_or_default().contains("said no"), "{again}");
}

#[test]
fn a_signature_over_the_challenge_is_not_a_signature_of_the_act() {
    let (rig, phone, token) = linked();
    let id = id_of(&ask_found(&rig, &token, "home", "secure"));
    let view = rig.get(&phone, &id);
    let swapped = rig.phone("POST", "decide", "", phone.decision(&view, "approve")).1;
    assert_eq!(swapped["ok"], false, "the approval of a grant is not a signature of a charter: {swapped}");
    assert_eq!(governance(&rig)["mode"], "unfounded");
    assert_eq!(rig.get(&phone, &id)["state"], "pending", "a refused answer leaves the request waiting");
}

#[test]
fn a_signature_over_other_bytes_or_by_another_key_founds_nothing() {
    let (rig, phone, token) = linked();
    let id = id_of(&ask_found(&rig, &token, "home", "secure"));
    let view = rig.get(&phone, &id);
    let wrong_bytes = rig.phone("POST", "decide", "", approve_with(&phone, &view, b"poolhouse/pool-accept/v1\n{}")).1;
    assert_eq!(wrong_bytes["ok"], false, "{wrong_bytes}");
    let stranger = FakePhone::new("Other");
    let by_stranger = rig.phone("POST", "decide", "", approve_with(&stranger, &view, &message(&view))).1;
    assert_eq!(by_stranger["ok"], false, "a key this node does not hold signs nothing: {by_stranger}");
    assert_eq!(governance(&rig)["mode"], "unfounded");
}

#[test]
fn a_signature_made_for_one_request_does_not_answer_another() {
    let (rig, phone, token) = linked();
    let (first, second) = (id_of(&ask_found(&rig, &token, "home", "secure")), id_of(&ask_found(&rig, &token, "work", "secure")));
    let (view_a, view_b) = (rig.get(&phone, &first), rig.get(&phone, &second));
    let replayed = rig.phone("POST", "decide", "", approve_with(&phone, &view_b, &message(&view_a))).1;
    assert_eq!(replayed["ok"], false, "{replayed}");
    assert_eq!(governance(&rig)["mode"], "unfounded");
    assert_eq!(tap(&rig, &phone, &first)["ok"], true);
    assert_eq!(governance(&rig)["name"], "home");
    let again = tap(&rig, &phone, &first);
    assert_eq!(again["ok"], false, "a request is decided once: {again}");
}

#[test]
fn a_node_with_no_phone_says_so_in_one_sentence_and_a_pool_is_not_founded_twice() {
    let rig = Rig::new();
    let (_, token) = rig.session("pool", "agent-one");
    let none = ask_found(&rig, &token, "home", "secure");
    assert_eq!(none["ok"], false, "{none}");
    let say = none["error"]["say"].as_str().unwrap();
    assert!(say.contains("your phone") && say.len() <= 160, "{say}");
    let mut phone = FakePhone::new("Pixel");
    rig.add(&mut phone, None);
    found(&rig, &phone, &token, "secure");
    let twice = ask_found(&rig, &token, "other", "secure");
    assert_eq!(twice["ok"], false);
    assert!(twice["error"]["say"].as_str().unwrap().contains("already runs a pool"), "{twice}");
}

#[test]
fn a_later_act_the_phone_signs_is_taken_and_one_a_removed_key_would_sign_is_not_asked() {
    let (rig, phone, token) = linked();
    found(&rig, &phone, &token, "secure");
    let admit = id_of(&rig.call("pool_ask_sign", "", &token, json!({"kind": "admit", "body": {"cert": own_cert(&rig), "name": "", "how": "found", "via": ""}})));
    assert_eq!(tap(&rig, &phone, &admit)["ok"], true);
    let fingerprint = rig.device();
    let removal = rig.call("pool_ask_sign", "", &token, json!({"kind": "remove", "body": {"fingerprint": fingerprint}}));
    let card = removal["result"]["text"].as_str().unwrap();
    assert!(card.contains("RISK: IRREVERSIBLE.") && card.contains("ASK: Remove ") && card.contains("stops working with every device at once"), "{card}");
    let nothing = rig.call("pool_ask_sign", "", &token, json!({"kind": "set_mode", "body": {"mode": "nonsense"}}));
    assert_eq!(nothing["ok"], false, "an act that is not well formed is not put to the person: {nothing}");
    let not_a_kind = rig.call("pool_ask_sign", "", &token, json!({"kind": "found", "body": {}}));
    assert_eq!(not_a_kind["ok"], false, "{not_a_kind}");
}

#[test]
fn a_session_that_asks_again_and_again_is_stopped_and_a_stranger_session_asks_nothing() {
    let (rig, _phone, token) = linked();
    let mut refused = 0;
    for n in 0..8 {
        if ask_found(&rig, &token, &format!("pool{n}"), "secure")["ok"] == false {
            refused += 1;
        }
    }
    assert!(refused >= 1, "ten minutes allow a session only so many requests");
    let none = rig.call("pool_ask_found", "", "not-a-token", json!({"title": "home", "policy": "secure", "project": ""}));
    assert_eq!(none["ok"], false, "{none}");
}

#[test]
fn another_phone_of_the_person_cannot_answer_a_request_asked_of_the_first_phones_key() {
    let (rig, first, token) = linked();
    let mut second = FakePhone::new("Spare");
    rig.add(&mut second, Some(&first));
    let id = id_of(&ask_found(&rig, &token, "home", "secure"));
    let view = rig.get(&first, &id);
    let by_second = rig.phone("POST", "decide", "", approve_with(&second, &view, &message(&view))).1;
    assert_eq!(by_second["ok"], false, "{by_second}");
    assert!(by_second["error"]["message"].as_str().unwrap().contains("not the key this request asked"), "{by_second}");
    assert_eq!(governance(&rig)["mode"], "unfounded");
    assert_eq!(tap(&rig, &first, &id)["ok"], true, "the key asked can still answer");
}

#[test]
fn a_phone_that_is_not_an_admin_of_the_pool_is_not_asked_to_sign_for_it() {
    use kit::gov::*;
    use poolhouse_node::gov::charter::Unit;
    use poolhouse_node::membership::Policy;
    let (rig, _phone, token) = linked();
    let c = charter(&[strong(1, "ann")], Policy::Secure, Unit::Person);
    rig.node.lock().unwrap().pool_found(&serde_json::to_value(found_act(&c)).unwrap()).unwrap();
    let asked = rig.call("pool_ask_sign", "", &token, json!({"kind": "admit", "body": {"cert": own_cert(&rig), "name": "", "how": "join-code", "via": ""}}));
    assert_eq!(asked["ok"], false, "{asked}");
    assert!(asked["error"]["say"].as_str().unwrap().contains("None of the keys"), "{asked}");
}
