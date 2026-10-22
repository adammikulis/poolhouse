//! Shared bytes of a phone's acceptance of being listed as an admin key and of the `admin_add` act that lists it, for the phone app.
mod kit;

use kit::phone::FakePhone;
use poolhouse_node::fsutil::hex;
use poolhouse_node::gov::act::Act;
use poolhouse_node::gov::body;
use poolhouse_node::gov::keys::{AdminKey, Alg, Role, Strength};
use poolhouse_node::row::canonical;
use serde_json::{json, Value};

const PATH: &str = "../../tests/fixtures/phone-admit-vectors.json";

fn entry(vectors: &Value) -> AdminKey {
    let terms = &vectors["terms"];
    AdminKey {
        key: vectors["key"].as_str().unwrap().into(), alg: Alg::P256, role: Role::Admin, strength: Strength::Strong, person: terms["person"].as_str().unwrap().into(),
        since: terms["since"].as_u64().unwrap(), acceptance: vectors["acceptance"].as_str().unwrap().into(), label: vectors["label"].as_str().unwrap().into(),
    }
}

fn vectors() -> Value {
    let phone = FakePhone::new("replacement");
    let terms = json!({"pool": "ab".repeat(16), "prev": "cd".repeat(32), "since": 2, "person": "ann"});
    let mut shared = json!({"now_ms": 1700000002000i64, "terms": terms, "key": phone.public, "label": "replacement", "acceptance": phone.accept(&terms)});
    let listed = entry(&shared);
    let (pool, prev) = (terms["pool"].as_str().unwrap(), terms["prev"].as_str().unwrap());
    let mut act = Act::new(pool, 1, "admin_add", json!({"entry": listed}), 1700000001000, prev).unwrap();
    act.nonce = "1234567890abcdef1234567890abcdef".into();
    shared["acceptance_bytes"] = json!(hex(&listed.acceptance_message(pool, prev)));
    shared["act"] = json!(act);
    shared["act_bytes"] = json!(hex(&act.sign_bytes()));
    shared["act_id"] = json!(act.id());
    shared["canonical"] = json!(canonical(&act.unsigned()));
    shared
}

#[test]
#[ignore = "writes a reference fixture"]
fn generate_reference() {
    std::fs::write(PATH, format!("{}\n", serde_json::to_string_pretty(&vectors()).unwrap())).unwrap();
}

#[test]
fn shared_bytes_match_the_nodes_acceptance_and_admin_add_act() {
    let shared: Value = serde_json::from_str(&std::fs::read_to_string(PATH).unwrap()).unwrap();
    let (listed, terms) = (entry(&shared), &shared["terms"]);
    let (pool, prev) = (terms["pool"].as_str().unwrap(), terms["prev"].as_str().unwrap());
    assert!(listed.check().is_ok() && listed.accepted(pool, prev), "the phone's acceptance verifies under the terms");
    assert_eq!(hex(&listed.acceptance_message(pool, prev)), shared["acceptance_bytes"]);
    let act: Act = serde_json::from_value(shared["act"].clone()).unwrap();
    act.check_shape().unwrap();
    body::parse(&act).unwrap();
    assert_eq!((hex(&act.sign_bytes()), act.id(), canonical(&act.unsigned())), (shared["act_bytes"].as_str().unwrap().into(), shared["act_id"].as_str().unwrap().into(), shared["canonical"].as_str().unwrap().into()));
    for (field, value) in [("person", json!("bob")), ("since", json!(3)), ("pool", json!("ef".repeat(16))), ("prev", json!("ef".repeat(32)))] {
        let mut other = shared.clone();
        other["terms"][field] = value;
        let moved = entry(&other);
        assert!(!moved.accepted(other["terms"]["pool"].as_str().unwrap(), other["terms"]["prev"].as_str().unwrap()), "{field}: an acceptance is for one set of terms");
    }
    let mut act = act;
    act.body["summary"] = json!("untrusted");
    assert!(body::parse(&act).is_err());
}
