//! Shared founding bytes and P-256 signatures for the Android protocol checks.
mod kit;

use kit::gov::{ident, public};
use kit::p256::PhoneKey;
use poolhouse_node::fsutil::{hex, unhex};
use poolhouse_node::gov::charter::{Charter, First};
use poolhouse_node::gov::keys::{AdminKey, Alg, Role, Strength};
use poolhouse_node::membership::Policy;
use poolhouse_node::row::canonical;
use serde_json::{json, Value};

const VECTORS: &str = "../../tests/fixtures/phone-founding-vectors.json";

fn low_twin(high: &[u8]) -> Vec<u8> {
    let r_len = high[3] as usize;
    let r = &high[4..4 + r_len];
    let s = &high[6 + r_len..];
    let mut r32 = [0u8; 32];
    let digits = r.strip_prefix(&[0]).unwrap_or(r);
    r32[32-digits.len()..].copy_from_slice(digits);
    let digits = s.strip_prefix(&[0]).unwrap_or(s);
    let order = unhex("ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551").unwrap();
    let mut s32 = [0u8; 32];
    s32[32-digits.len()..].copy_from_slice(digits);
    let mut borrow = 0i16;
    for i in (0..32).rev() {
        let d = i16::from(order[i]) - i16::from(s32[i]) - borrow;
        s32[i] = (d & 255) as u8;
        borrow = i16::from(d < 0);
    }
    kit::p256::der(&r32, &s32)
}

#[test]
#[ignore = "writes a new reference fixture"]
fn generate_reference() {
    let phone = PhoneKey::new();
    let entry = |key: String, alg, role, strength, person: &str, label: &str| AdminKey {
        key, alg, role, strength, person: person.into(), label: label.into(), since: 1, acceptance: String::new(),
    };
    let mut charter = Charter::new("lab", Policy::Secure, "0123456789abcdef", vec![
        entry(phone.public.clone(), Alg::P256, Role::Admin, Strength::Strong, "person-a", "phone \"A\"\\key"),
        entry(public(2), Alg::Ed25519, Role::Admin, Strength::Weak, "person-b", "spare"),
        entry(public(3), Alg::Ed25519, Role::Recovery, Strength::Strong, "person-c", "recovery"),
    ], First::of(&ident(250).der, "first device"), 1700000000123);
    charter.thresholds.sensitive = 1;
    charter.holds.admin_add_h = 48;
    charter.holds.admin_remove_h = 72;
    charter.holds.handoff_min = 30;
    charter.holds.dispute_d = 9;
    charter.recover_delay_days = 14;
    let anchor = charter.anchor();
    let selected = charter.admin(&phone.public).unwrap();
    let message = selected.acceptance_message(&anchor, "");
    let high = phone.sign_high(&message);
    let low = hex(&low_twin(&unhex(&high).unwrap()));
    let acceptances: Vec<Value> = charter.admins.iter().map(|k| json!({"key": k.key, "bytes": hex(&k.acceptance_message(&anchor, ""))})).collect();
    let vectors = json!({"charter": charter.body(), "canonical": canonical(&charter.body()), "anchor": anchor,
        "pool_id": charter.pool_id(), "selected_key": phone.public, "acceptances": acceptances,
        "signing_bytes": hex(&message), "high_signature": high, "low_signature": low,
        "other_certificate": hex(&ident(251).der),
        "ed25519_keys": (0u8..=16).chain([255]).map(|byte| {
            let key = hex(&[byte;32]);
            json!({"valid": Alg::of_public(&key) == Some(Alg::Ed25519), "key": key})
        }).collect::<Vec<_>>()});
    std::fs::write(VECTORS, format!("{}\n", serde_json::to_string_pretty(&vectors).unwrap())).unwrap();
}

#[test]
fn shared_bytes_are_the_nodes_bytes_and_normalized_signatures_verify() {
    let v: Value = serde_json::from_str(&std::fs::read_to_string(VECTORS).unwrap()).unwrap();
    let c = Charter::read(&v["charter"]).unwrap();
    assert_eq!(canonical(&c.body()), v["canonical"]);
    assert_eq!(c.anchor(), v["anchor"]);
    assert_eq!(c.pool_id(), v["pool_id"]);
    for acceptance in v["acceptances"].as_array().unwrap() {
        let key = c.admin(acceptance["key"].as_str().unwrap()).unwrap();
        assert_eq!(hex(&key.acceptance_message(&c.anchor(), "")), acceptance["bytes"]);
    }
    for candidate in v["ed25519_keys"].as_array().unwrap() {
        assert_eq!(Alg::of_public(candidate["key"].as_str().unwrap()) == Some(Alg::Ed25519), candidate["valid"].as_bool().unwrap());
    }
    assert!(poolhouse_node::gov::charter::First { cert: v["other_certificate"].as_str().unwrap().into(), name: "other".into() }.device().is_some());
    let key = c.admin(v["selected_key"].as_str().unwrap()).unwrap();
    let message = key.acceptance_message(&c.anchor(), "");
    assert_eq!(hex(&message), v["signing_bytes"]);
    assert!(Alg::P256.verifies(&key.key, &message, v["low_signature"].as_str().unwrap()));
    assert!(!Alg::P256.verifies(&key.key, &message, v["high_signature"].as_str().unwrap()));
    assert_eq!(hex(&low_twin(&unhex(v["high_signature"].as_str().unwrap()).unwrap())), v["low_signature"]);
    for bad in ["3006020100020101", "3006020101020100", "308106020101020101", "300702020001020101", "3006020180020101"] {
        assert!(!Alg::P256.verifies(&key.key, &message, bad));
    }
    let mut changed = message.clone();
    changed.push(b' ');
    assert!(!Alg::P256.verifies(&key.key, &changed, v["low_signature"].as_str().unwrap()));
}
