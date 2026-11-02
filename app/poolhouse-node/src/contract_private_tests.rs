//! Private data codecs remain tested inside the node crate.
use crate::contract::*;
use serde_json::{json, Value};

#[test]
fn private_union_rejects_unknown_platform_and_extra_fields() {
    let parent: NativeParent = serde_json::from_value(json!({"kind": "none", "value": {}})).unwrap();
    assert!(matches!(parent, NativeParent::None(_)));
    assert!(serde_json::from_value::<NativeParent>(json!({"kind": "none", "value": {"thread_id": "forged"}})).is_err());
    assert!(serde_json::from_value::<ProcessObservation>(json!({"platform": "forged", "value": {}})).is_err());
}

#[test]
fn private_lease_diagnostics_do_not_expose_values() {
    let secret = "ab".repeat(32);
    let lease = SessionLease::try_from(secret.clone()).unwrap();
    let display = format!("{lease:?}");
    assert!(display.contains("redacted"));
    assert!(!display.contains(&secret));
}

#[test]
fn audit_token_words_roundtrip_little_endian_without_sign_loss() {
    let vectors: Value = serde_json::from_str(include_str!("../../../contracts/node/vectors.json")).unwrap();
    let vector = &vectors["audit_token"];
    let words: [u32; 8] = std::array::from_fn(|i| u32::try_from(vector["words"][i].as_u64().unwrap()).unwrap());
    let token = AuditToken::from_words(words);
    assert_eq!(token.as_str(), vector["encoded"].as_str().unwrap());
    assert_eq!(token.words(), words);
    assert_eq!(AuditToken::try_from(token.as_str().to_owned()).unwrap().words(), words);
    assert!(words[7] > i32::MAX as u32);
}

#[test]
fn private_sid_encoding_rejects_invalid_subauthority_count() {
    assert!(WindowsSid::try_from("010100000000000100000000".to_owned()).is_ok());
    assert!(WindowsSid::try_from("011000000000000100000000".to_owned()).is_err());
}
