use poolhouse_node::contract::*;
use serde_json::{json, Value};

#[test]
fn device_identity_vector_uses_domain_separated_raw_public_key() {
    let vectors: Value = serde_json::from_str(include_str!("../../../contracts/node/vectors.json")).unwrap();
    let key = Ed25519PublicKey::try_from(vectors["device_id"]["public_key"].as_str().unwrap().to_owned()).unwrap();
    assert_eq!(device_id_for_public_key(&key).as_str(), vectors["device_id"]["expected"].as_str().unwrap());
    assert_ne!(device_id_for_public_key(&key).as_str(), key.as_str());
    assert_eq!(SCHEMA_SHA256, vectors["schema_sha256"].as_str().unwrap());
}

#[test]
fn unsigned_values_are_exact_decimal_strings() {
    let maximum: U64 = serde_json::from_str("\"18446744073709551615\"").unwrap();
    assert_eq!(serde_json::to_string(&maximum).unwrap(), "\"18446744073709551615\"");
    for input in ["1", "true", "\"01\"", "\"-0\"", "\"18446744073709551616\""] {
        assert!(serde_json::from_str::<U64>(input).is_err(), "{input}");
    }
    assert!(serde_json::from_str::<U32>("\"4294967296\"").is_err());
}

#[test]
fn nominal_identity_types_are_distinct_and_validate_encoding() {
    assert_ne!(std::any::TypeId::of::<DeviceId>(), std::any::TypeId::of::<CertificateDigest>());
    assert_ne!(std::any::TypeId::of::<DeviceId>(), std::any::TypeId::of::<PoolId>());
    assert!(DeviceId::try_from("AB".repeat(32)).is_err());
    assert!(WindowsSid::try_from("010100000000000100000000".to_owned()).is_ok());
    assert!(WindowsSid::try_from("011000000000000100000000".to_owned()).is_err());
}

#[test]
fn public_identity_rejects_unknown_and_missing_fields() {
    let mut claim = json!({"device_id": "ab".repeat(32), "certificate_digest": "cd".repeat(32),
        "installation_id": "00000000-0000-4000-8000-000000000001", "public_key": "ef".repeat(32), "contract_version": "1"});
    let _: NodeIdentity = serde_json::from_value(claim.clone()).unwrap();
    claim["approved"] = json!(true);
    assert!(serde_json::from_value::<NodeIdentity>(claim.clone()).is_err());
    claim.as_object_mut().unwrap().remove("approved");
    claim.as_object_mut().unwrap().remove("installation_id");
    assert!(serde_json::from_value::<NodeIdentity>(claim).is_err());
}

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
fn lifecycle_metadata_keeps_terminal_states_closed_and_consumption_atomic() {
    use poolhouse_node::contract_lifecycle::TRANSITIONS;
    for transition in TRANSITIONS {
        assert!(!["retired", "consumed", "cancelled", "invalidated"].contains(&transition.from));
        if transition.event == "consume" {
            assert_eq!(transition.from, "released");
            assert_eq!(transition.to, "consumed");
            assert!(transition.checks.contains(&"atomic-single-consumer"));
            assert!(transition.checks.contains(&"exact-live-bound-caller"));
        }
        if transition.event == "release" {
            assert!(transition.checks.contains(&"single-use-hold"));
        }
    }
    assert_eq!(TRANSITIONS.iter().filter(|row| row.event == "consume").count(), 1);
}
