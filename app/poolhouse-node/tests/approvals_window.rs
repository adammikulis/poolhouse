//! An irreversible data act waits a day before its grant starts, whoever approved it and however it was signed, and a person key can revoke
//! the grant meanwhile. In a pool, a key below the sensitive floor approves nothing sensitive or irreversible, and signs no sensitive grant
//! directly either.

mod kit;

use kit::gov::{claim_at, computer_admin, phone_admin, pool_of, charter};
use kit::grants::{gid_of, Person, Spec};
use kit::phone::{ask, Rig};
use kit::*;
use poolhouse_node::approvals::challenge;
use poolhouse_node::fsutil::hex;
use poolhouse_node::gov::charter::Unit;
use poolhouse_node::gov::keys::Level;
use poolhouse_node::grants::{check, Scope};
use poolhouse_node::identity::Holder;
use poolhouse_node::membership::{Policy, Pool};
use serde_json::{json, Value};

const DAY_MS: u64 = 24 * 3600 * 1000;

fn asked(asks: Value) -> (Rig, kit::phone::FakePhone, String, String, Value) {
    let rig = Rig::new();
    let mut phone = kit::phone::FakePhone::new("Pixel");
    rig.add(&mut phone, None);
    let (name, token) = rig.session("demo", "agent-1");
    let made = ok(rig.ask("demo", &token, asks, json!({"note": "clearing out the notes", "task": ""})));
    (rig, phone, name, token, made)
}

fn live(rig: &Rig, name: &str, action: &str, resource: &str) -> bool {
    check(&mut rig.node.lock().unwrap(), &Holder { board: "demo".into(), name: name.into() }, action, &Scope::on(resource)).unwrap()
}

/// The action, status and start of every grant the node carries for the session of ``token``.
fn statuses(rig: &Rig, token: &str) -> Vec<(String, String, u64)> {
    let listing = ok(rig.call("grants", "demo", token, json!({})));
    listing["grants"].as_array().unwrap().iter().filter(|g| g["carried_by"] == "node").map(|g| (g["action"].as_str().unwrap().to_string(), g["status"].as_str().unwrap().to_string(), g["at_ms"].as_u64().unwrap())).collect()
}

fn decide_with(rig: &Rig, key: &Person, seen: &Value, carrier: &str) -> Value {
    let bytes = challenge::bytes(&seen["challenge"], "approve", &key.fingerprint());
    let sig = hex(&ed25519_dalek::Signer::sign(&key.key, &bytes).to_bytes());
    rig.call("approval_decide", "", carrier, json!({"id": seen["id"], "decision": "approve", "key": key.fingerprint(), "sig": sig, "pick": rig.shown(seen["id"].as_str().unwrap())}))
}

fn decided_by(rig: &Rig, key: &Person, seen: &Value, carrier: &str) -> Value {
    ok(decide_with(rig, key, seen, carrier))
}

/// Put the node of ``rig`` in a pool of two computer-level people; the pool lives as long as the directory returned.
fn in_pool(rig: &Rig) -> tempfile::TempDir {
    let (dir, gov) = pool_of(&charter(&[phone_admin(1, "ann"), computer_admin(2, "bob")], Policy::Secure, Unit::Person));
    rig.node.lock().unwrap().members = Pool::governed(gov);
    dir
}

/// A person key the node enrolled with the claim of ``level``.
fn key_at(rig: &Rig, level: Level) -> Person {
    let key = Person { key: ed25519_dalek::SigningKey::from_bytes(&[0x44; 32]) };
    rig.node.lock().unwrap().persons.add_claimed_key("ed25519", &key.public(), "k", poolhouse_node::poolops::wall_ms(), claim_at(level)).unwrap();
    key
}

#[test]
fn an_irreversible_act_a_computer_key_approved_waits_a_day_and_a_person_key_can_revoke_it() {
    let (rig, phone, name, token, made) = asked(json!([ask("file_delete", "notes"), ask("sync_now", "demo")]));
    let studio = key_at(&rig, Level::Computer);
    let seen = rig.get(&phone, made["id"].as_str().unwrap());
    let (_, carrier) = rig.session("demo", "agent-2");
    let decided = poolhouse_node::poolops::wall_ms();
    let done = decided_by(&rig, &studio, &seen, &carrier);
    assert_eq!(done["state"], "approved", "{done}");
    assert!(live(&rig, &name, "sync_now", "demo"), "a reversible act starts at once");
    assert!(!live(&rig, &name, "file_delete", "notes"), "an irreversible act waits");
    let held = statuses(&rig, &token);
    let (_, status, starts) = held.iter().find(|(action, _, _)| action == "file_delete").cloned().unwrap();
    assert_eq!(status, "not-yet");
    assert!(starts >= decided + DAY_MS - 60_000 && starts <= decided + DAY_MS + 60_000, "it starts a day after the approval: {starts} from {decided}");
    assert!(done["reason"].as_str().unwrap_or("").contains("24 hours"), "the approval says it waits: {done}");

    let gid = done["grants"][0].as_str().unwrap().to_string();
    ok(rig.call("grant_revoke", "demo", &carrier, studio.revoke_params("demo", &gid, "changed my mind")));
    let after = statuses(&rig, &token);
    assert!(after.iter().all(|(action, status, _)| action != "file_delete" || status == "revoked"), "a key revoked it before it started: {after:?}");
}

#[test]
fn an_irreversible_act_a_phone_approved_waits_a_day_too() {
    let (rig, phone, name, _token, made) = asked(json!([ask("file_delete", "notes")]));
    let seen = rig.get(&phone, made["id"].as_str().unwrap());
    let (status, reply) = rig.phone("POST", "decide", "", phone.decision(&seen, "approve"));
    assert_eq!((status, &reply["result"]["state"]), (200, &json!("approved")), "{reply}");
    assert!(!live(&rig, &name, "file_delete", "notes"), "a phone counts as a computer: no member can check more, so its approval waits too");
}

#[test]
fn a_key_below_the_pools_floor_approves_nothing_sensitive_or_irreversible() {
    for (action, resource) in [("file_delete", "notes"), ("shard_consent", "notes"), ("add_device", "*")] {
        let (rig, phone, name, _token, made) = asked(json!([ask(action, resource)]));
        let seen = rig.get(&phone, made["id"].as_str().unwrap());
        let _pool = in_pool(&rig);
        let key = key_at(&rig, Level::Weak);
        let (_, carrier) = rig.session("demo", "agent-2");
        let refused = decide_with(&rig, &key, &seen, &carrier);
        let case = format!("{action} by a typed yes under a computer floor: {refused}");
        assert_eq!(code(&refused), "denied", "{case}");
        assert!(refused["error"]["message"].as_str().unwrap().contains("not an admin key of this pool"), "{case}");
        assert!(!live(&rig, &name, action, resource), "{case}");
        assert_eq!(rig.node.lock().unwrap().approvals.get(made["id"].as_str().unwrap()).unwrap().state, "pending", "a refused key leaves the request for a key at the floor: {case}");
    }
}

#[test]
fn a_key_at_the_floor_approves_what_a_typed_yes_was_refused() {
    let (rig, phone, _name, _token, made) = asked(json!([ask("add_device", "*")]));
    let seen = rig.get(&phone, made["id"].as_str().unwrap());
    let _pool = in_pool(&rig);
    let typed = key_at(&rig, Level::Weak);
    let (_, carrier) = rig.session("demo", "agent-2");
    assert_eq!(code(&decide_with(&rig, &typed, &seen, &carrier)), "denied");
    let listed = Person { key: ed25519_dalek::SigningKey::from_bytes(&kit::key(2).to_bytes()) };
    rig.node.lock().unwrap().persons.add_claimed_key("ed25519", &listed.public(), "Studio", poolhouse_node::poolops::wall_ms(), claim_at(Level::Computer)).unwrap();
    assert_eq!(decided_by(&rig, &listed, &seen, &carrier)["state"], "approved", "a listed key at the floor approves it");
}

#[test]
fn an_irreversible_act_a_typed_yes_approved_outside_a_pool_waits_a_day() {
    let (rig, phone, name, _token, made) = asked(json!([ask("file_delete", "notes")]));
    let key = key_at(&rig, Level::Weak);
    let seen = rig.get(&phone, made["id"].as_str().unwrap());
    let (_, carrier) = rig.session("demo", "agent-2");
    let done = decided_by(&rig, &key, &seen, &carrier);
    assert_eq!(done["state"], "approved", "{done}");
    assert!(!live(&rig, &name, "file_delete", "notes"), "a typed yes is no stronger than a computer's key: it waits too");
    assert!(done["reason"].as_str().unwrap_or("").contains("24 hours"), "{done}");
}

#[test]
fn in_a_pool_a_key_that_signs_grant_issue_directly_skips_neither_the_floor_nor_the_card() {
    let rig = Rig::new();
    let (name, token) = rig.session("demo", "agent-1");
    let _pool = in_pool(&rig);
    let key = key_at(&rig, Level::Weak);
    let mut call = |m: &str, b: &str, t: &str, v: Value| rig.call(m, b, t, v);
    let irreversible = key.issue(&mut call, "demo", &token, gid_of(&name, "file_delete").as_str(), &Spec::session(&name, "file_delete"));
    assert_eq!(code(&irreversible), "denied", "an act that reaches beyond the device is approved on a card, which starts an irreversible one a day later: {irreversible}");
    assert!(irreversible["error"]["message"].as_str().unwrap().contains("your phone gives standing grants"), "{irreversible}");
    let refused = key.issue(&mut call, "demo", &token, gid_of(&name, "link_read").as_str(), &Spec::session(&name, "link_read"));
    assert_eq!(code(&refused), "denied", "{refused}");
    assert!(refused["error"]["message"].as_str().unwrap().contains("This pool needs a key approved from"), "{refused}");
    let routine = key.issue(&mut call, "demo", &token, gid_of(&name, "link_remove").as_str(), &Spec::session(&name, "link_remove"));
    assert_eq!(routine["ok"], true, "a routine grant of a device-local act is still the typed yes's to give: {routine}");
}
