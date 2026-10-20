//! An irreversible data act that only a computer-level key approved waits a day before its grant starts, and a person key can revoke the
//! grant meanwhile; a phone's approval starts at once.

mod kit;

use kit::grants::Person;
use kit::phone::{ask, Rig};
use kit::*;
use poolhouse_node::approvals::challenge;
use poolhouse_node::fsutil::hex;
use poolhouse_node::gov::keys::Level;
use poolhouse_node::grants::{check, Scope};
use poolhouse_node::identity::Holder;
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

fn statuses(rig: &Rig, token: &str) -> Vec<(String, String, u64)> {
    let listing = ok(rig.call("grants", "demo", token, json!({})));
    listing["grants"].as_array().unwrap().iter().filter(|g| g["carried_by"] == "node").map(|g| (g["action"].as_str().unwrap().to_string(), g["status"].as_str().unwrap().to_string(), g["at_ms"].as_u64().unwrap())).collect()
}

fn decided_by(rig: &Rig, key: &Person, seen: &Value, carrier: &str) -> Value {
    let bytes = challenge::bytes(&seen["challenge"], "approve", &key.fingerprint());
    let sig = hex(&ed25519_dalek::Signer::sign(&key.key, &bytes).to_bytes());
    ok(rig.call("approval_decide", "", carrier, json!({"id": seen["id"], "decision": "approve", "key": key.fingerprint(), "sig": sig, "pick": rig.shown(seen["id"].as_str().unwrap())})))
}

#[test]
fn an_irreversible_act_a_computer_key_approved_waits_a_day_and_a_person_key_can_revoke_it() {
    let (rig, phone, name, token, made) = asked(json!([ask("file_delete", "notes"), ask("sync_now", "demo")]));
    let studio = Person { key: ed25519_dalek::SigningKey::from_bytes(&[0x44; 32]) };
    rig.node.lock().unwrap().persons.add_leveled_key("ed25519", &studio.public(), "Studio", poolhouse_node::poolops::wall_ms(), Level::Computer).unwrap();
    let seen = rig.get(&phone, made["id"].as_str().unwrap());
    let (_, carrier) = rig.session("demo", "agent-2");
    let decided = poolhouse_node::poolops::wall_ms();
    let done = decided_by(&rig, &studio, &seen, &carrier);
    assert_eq!(done["state"], "approved", "{done}");
    assert!(live(&rig, &name, "sync_now", "demo"), "a reversible act starts at once");
    assert!(!live(&rig, &name, "file_delete", "notes"), "an irreversible act a computer key approved waits");
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
fn an_irreversible_act_a_phone_approved_starts_at_once() {
    let (rig, phone, name, _token, made) = asked(json!([ask("file_delete", "notes")]));
    let seen = rig.get(&phone, made["id"].as_str().unwrap());
    let (status, reply) = rig.phone("POST", "decide", "", phone.decision(&seen, "approve"));
    assert_eq!((status, &reply["result"]["state"]), (200, &json!("approved")), "{reply}");
    assert!(live(&rig, &name, "file_delete", "notes"), "a phone is above the computer level: no wait");
}
