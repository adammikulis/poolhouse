//! Shared unsigned admission and removal reference bytes for the phone preview.
use poolhouse_node::fsutil::{hex, sha256_hex};
use poolhouse_node::gov::act::Act;
use poolhouse_node::gov::body;
use poolhouse_node::gov::charter::Charter;
use poolhouse_node::row::canonical;
use serde_json::{json, Value};

const PATH: &str = "../../tests/fixtures/phone-act-vectors.json";

fn vectors() -> Value {
    let founding: Value = serde_json::from_str(&std::fs::read_to_string("../../tests/fixtures/phone-founding-vectors.json").unwrap()).unwrap();
    let charter = Charter::read(&founding["charter"]).unwrap();
    let bodies = [
        ("admit", json!({"cert": founding["other_certificate"], "name": "second device", "how": "join-code", "via": ""})),
        ("remove", json!({"fingerprint": sha256_hex(&poolhouse_node::fsutil::unhex(founding["other_certificate"].as_str().unwrap()).unwrap()), "cut": [
            {"board":"demo","origin":"1234567890abcdef1234567890abcdef","seq":3,"hash":"ab".repeat(32)},
            {"board":"other","origin":"abcdef1234567890abcdef1234567890","seq":0,"hash":""}
        ]})),
    ];
    let rows: Vec<Value> = bodies.into_iter().map(|(kind, body)| {
        let mut act = Act::new(&charter.pool_id(), 1, kind, body, 1700000001000, &charter.hash()).unwrap();
        act.nonce = "1234567890abcdef1234567890abcdef".into();
        act.check_shape().unwrap();
        body::parse(&act).unwrap();
        json!({"act":act, "bytes":hex(&act.sign_bytes()), "id":act.id(), "canonical":canonical(&act.unsigned())})
    }).collect();
    json!({"now_ms":1700000002000i64,"rows":rows})
}

#[test]
#[ignore = "writes a reference fixture"]
fn generate_reference() {
    std::fs::write(PATH, format!("{}\n", serde_json::to_string_pretty(&vectors()).unwrap())).unwrap();
}

#[test]
fn shared_bytes_match_node_act_and_strict_bodies() {
    let shared: Value = serde_json::from_str(&std::fs::read_to_string(PATH).unwrap()).unwrap();
    assert_eq!(shared, vectors());
    for row in shared["rows"].as_array().unwrap() {
        let mut act: Act = serde_json::from_value(row["act"].clone()).unwrap();
        act.body["summary"] = json!("untrusted");
        assert!(body::parse(&act).is_err());
        assert_ne!(hex(&act.sign_bytes()), row["bytes"]);
    }
}
