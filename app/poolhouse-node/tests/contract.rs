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

#[test]
fn job_wire_requires_explicit_null_and_rejects_sensitive_fields() {
    let job = json!({"job_id": "00000000-0000-4000-8000-000000000001",
        "generation": "00000000-0000-4000-8000-000000000002", "revision": "1",
        "project_id": "00000000-0000-4000-8000-000000000003", "name": "A run",
        "operation": "models.inspect", "state": {"kind": "preparing", "value": {}},
        "return_code": null, "submitted_at": "1700000000000000", "started_at": null, "finished_at": null});
    let decoded: JobSnapshot = serde_json::from_value(job.clone()).unwrap();
    assert_eq!(serde_json::to_value(decoded).unwrap(), job);
    for field in ["return_code", "started_at", "finished_at"] {
        let mut missing = job.clone();
        missing.as_object_mut().unwrap().remove(field);
        assert!(serde_json::from_value::<JobSnapshot>(missing).is_err(), "{field}");
    }
    for field in ["env", "cwd", "argv", "pid"] {
        let mut leaked = job.clone(); leaked[field] = json!("private");
        assert!(serde_json::from_value::<JobSnapshot>(leaked).is_err());
    }
}

#[test]
fn job_scalars_preserve_exact_boolean_text_and_signed_code_encodings() {
    assert!(serde_json::from_str::<Boolean>("true").is_ok());
    for value in ["1", "0", "null", "\"true\""] { assert!(serde_json::from_str::<Boolean>(value).is_err()); }
    for value in ["-0", "+1", "01", "2147483648", "-2147483649"] { assert!(ExitCode::try_from(value.to_owned()).is_err()); }
    assert!(ExitCode::try_from("-2147483648".to_owned()).is_ok());
    assert!(JobDisplayName::try_from("é".repeat(128)).is_ok());
    assert!(JobDisplayName::try_from("é".repeat(129)).is_err());
    assert!(JobDisplayName::try_from("bad\0text".to_owned()).is_err());
}

#[test]
fn operation_markers_bind_the_declared_job_types_without_dispatch() {
    use poolhouse_node::contract_operations::{JobsCancel, JobsGet, Operation};
    assert_eq!(JobsGet::NAME, "jobs.get"); assert_eq!(JobsCancel::NAME, "jobs.cancel");
    assert_eq!(std::any::TypeId::of::<<JobsCancel as Operation>::Request>(), std::any::TypeId::of::<JobCancelRequest>());
}

#[test]
fn job_page_and_output_bounds_use_decoded_units() {
    for value in ["0", "101", "01", "4294967296"] { assert!(JobPageLimit::try_from(value.to_owned()).is_err()); }
    assert!(JobPageLimit::try_from("100".to_owned()).is_ok());
    assert!(JobOutputByteLimit::try_from("65536".to_owned()).is_ok());
    assert!(JobOutputByteLimit::try_from("65537".to_owned()).is_err());
    assert!(JobOutputBytes::try_from("00c3a90aff".to_owned()).is_ok());
    assert!(JobOutputBytes::try_from("ff".repeat(65536)).is_ok());
    for value in ["0".to_owned(), "AA".to_owned(), "ff".repeat(65537)] { assert!(JobOutputBytes::try_from(value).is_err()); }
    assert!(serde_json::from_value::<JobOutputPosition>(json!({"kind": "resume", "value": {"cursor": "ab".repeat(32)}})).is_ok());
    assert!(serde_json::from_value::<JobOutputPosition>(json!({"kind": "resume", "value": {}})).is_err());
}

#[test]
fn job_page_list_rejects_excess_items_and_untyped_data() {
    let make_view = || serde_json::from_value::<JobView>(json!({"job": {
        "job_id": "00000000-0000-4000-8000-000000000001",
        "generation": "00000000-0000-4000-8000-000000000002", "revision": "1",
        "project_id": "00000000-0000-4000-8000-000000000003", "name": "A run",
        "operation": "models.inspect", "state": {"kind": "preparing", "value": {}},
        "return_code": null, "submitted_at": "1", "started_at": null, "finished_at": null},
        "cancellation": {"requested": false, "settled": false}})).unwrap();
    assert_eq!(JobViewList::try_from(vec![make_view(); 100]).unwrap().as_slice().len(), 100);
    assert!(JobViewList::try_from(vec![make_view(); 101]).is_err());
    assert!(serde_json::from_value::<JobViewList>(json!([{}])).is_err());
    assert_ne!(std::any::TypeId::of::<JobListCursor>(), std::any::TypeId::of::<JobOutputCursor>());
}

#[test]
fn shared_job_scalar_vectors_match_rust_decoding() {
    let vectors: Value = serde_json::from_str(include_str!("../../../contracts/node/vectors.json")).unwrap();
    for row in vectors["job_scalars"].as_array().unwrap() {
        let accepts = |value: &Value| match row["type"].as_str().unwrap() {
            "JobPageLimit" => serde_json::from_value::<JobPageLimit>(value.clone()).is_ok(),
            "JobOutputByteLimit" => serde_json::from_value::<JobOutputByteLimit>(value.clone()).is_ok(),
            "JobOutputBytes" => serde_json::from_value::<JobOutputBytes>(value.clone()).is_ok(),
            "ExitCode" => serde_json::from_value::<ExitCode>(value.clone()).is_ok(),
            "Boolean" => serde_json::from_value::<Boolean>(value.clone()).is_ok(),
            other => panic!("unhandled vector type {other}"),
        };
        for value in row["accepted"].as_array().unwrap() { assert!(accepts(value), "{}: {value}", row["type"]); }
        for value in row["refused"].as_array().unwrap() { assert!(!accepts(value), "{}: {value}", row["type"]); }
    }
}

#[test]
fn agent_snapshot_preserves_unknowns_and_separate_pause_intent() {
    let snapshot = json!({"agent_id": "00000000-0000-4000-8000-000000000001",
        "generation": "00000000-0000-4000-8000-000000000002", "project_id": "00000000-0000-4000-8000-000000000003",
        "project_generation": "00000000-0000-4000-8000-000000000004", "revision": "1", "control_revision": "3", "name": "local-worker",
        "state": {"kind": "working", "value": {}}, "pause_requested": true, "running": null,
        "steps": null, "tasks": "0", "ignored": null, "memory_bytes": null});
    let decoded: AgentControlSnapshot = serde_json::from_value(snapshot.clone()).unwrap();
    assert_eq!(serde_json::to_value(decoded).unwrap(), snapshot);
    for field in ["running", "steps", "memory_bytes"] {
        let mut missing = snapshot.clone(); missing.as_object_mut().unwrap().remove(field);
        assert!(serde_json::from_value::<AgentControlSnapshot>(missing).is_err());
    }
    for field in ["pid", "lease", "token", "person"] {
        let mut leaked = snapshot.clone(); leaked[field] = json!("forged");
        assert!(serde_json::from_value::<AgentControlSnapshot>(leaked).is_err());
    }
}

#[test]
fn task_control_requests_require_exact_execution_and_generations() {
    let request = json!({"agent_id": "00000000-0000-4000-8000-000000000001",
        "generation": "00000000-0000-4000-8000-000000000002", "project_id": "00000000-0000-4000-8000-000000000003",
        "project_generation": "00000000-0000-4000-8000-000000000004", "request_id": "00000000-0000-4000-8000-000000000005",
        "task": {"board_id": "ab".repeat(32), "task_id": "task-001"}, "task_generation": "00000000-0000-4000-8000-000000000006",
        "execution_id": "00000000-0000-4000-8000-000000000007", "execution_generation": "00000000-0000-4000-8000-000000000008"});
    let decoded: AgentTaskStopRequest = serde_json::from_value(request.clone()).unwrap();
    assert_eq!(decoded.task.task_id.as_str(), "task-001");
    assert!(serde_json::from_value::<AgentControlRequest>(request.clone()).is_err());
    for field in ["task_generation", "execution_id", "execution_generation", "project_generation"] {
        let mut missing = request.clone(); missing.as_object_mut().unwrap().remove(field);
        assert!(serde_json::from_value::<AgentTaskStopRequest>(missing).is_err());
    }
}

#[test]
fn general_agent_control_requires_an_expected_control_revision() {
    let mut request = json!({"agent_id": "00000000-0000-4000-8000-000000000001",
        "generation": "00000000-0000-4000-8000-000000000002", "project_id": "00000000-0000-4000-8000-000000000003",
        "project_generation": "00000000-0000-4000-8000-000000000004", "request_id": "00000000-0000-4000-8000-000000000005"});
    assert!(serde_json::from_value::<AgentControlRequest>(request.clone()).is_err());
    request["expected_control_revision"] = json!("3");
    assert!(serde_json::from_value::<AgentControlRequest>(request).is_ok());
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
fn timestamp_component_vectors_check_carry_and_overflow() {
    let vectors: Value = serde_json::from_str(include_str!("../../../contracts/node/vectors.json")).unwrap();
    let convert = |row: &Value| -> Option<UnixMicros> {
        let seconds = row[0].as_str()?.parse::<u64>().ok()?;
        let micros = row[1].as_str()?.parse::<u64>().ok()?;
        UnixMicros::try_from_parts(seconds, micros).ok()
    };
    for row in vectors["unix_microseconds"]["accepted"].as_array().unwrap() {
        assert_eq!(convert(row).unwrap().as_str(), row[2].as_str().unwrap());
    }
    for row in vectors["unix_microseconds"]["refused"].as_array().unwrap() { assert!(convert(row).is_none()); }
}

#[test]
fn finite_binary64_vectors_preserve_bits_and_refuse_nonfinite_values() {
    let vectors: Value = serde_json::from_str(include_str!("../../../contracts/node/vectors.json")).unwrap();
    for bits in vectors["finite_f64"]["accepted"].as_array().unwrap() {
        let encoded: FiniteF64 = serde_json::from_value(bits.clone()).unwrap();
        let value = encoded.to_f64();
        assert_eq!(format!("{:016x}", value.to_bits()), bits.as_str().unwrap());
        assert_eq!(FiniteF64::try_from_f64(value).unwrap(), encoded);
    }
    for invalid in vectors["finite_f64"]["refused"].as_array().unwrap() {
        assert!(serde_json::from_value::<FiniteF64>(invalid.clone()).is_err());
    }
    for value in [f64::INFINITY, f64::NEG_INFINITY, f64::NAN, -f64::NAN] {
        assert!(FiniteF64::try_from_f64(value).is_err());
    }
    assert_ne!(FiniteF64::try_from_f64(0.0).unwrap(), FiniteF64::try_from_f64(-0.0).unwrap());
}

#[test]
fn domain_numeric_observations_preserve_fractional_sign_and_unknown_state() {
    for margin in [json!({"kind": "surplus", "value": {"numerator": "17", "denominator": "2"}}),
                   json!({"kind": "shortfall", "value": {"numerator": "1", "denominator": "2"}})] {
        let decoded: ModelByteMargin = serde_json::from_value(margin.clone()).unwrap();
        assert_eq!(serde_json::to_value(decoded).unwrap(), margin);
    }
    assert!(serde_json::from_value::<ModelByteMargin>(json!({"kind": "shortfall", "value": {"numerator": "0", "denominator": "2"}})).is_err());
    assert!(serde_json::from_value::<ModelExactRatio>(json!({"numerator": "1", "denominator": "0"})).is_err());
    let unknown = json!({"kind": "unknown", "value": {"reason": "service state unavailable", "observed_at": "1", "last_known": null}});
    let decoded: StartupServiceObservation = serde_json::from_value(unknown.clone()).unwrap();
    assert_eq!(serde_json::to_value(decoded).unwrap(), unknown);
}

#[test]
fn domain_sets_reject_duplicates_and_approval_queue_can_be_empty() {
    let monday = json!({"kind": "mon", "value": {}});
    assert!(serde_json::from_value::<SettingsWeekdays>(json!([monday.clone()])).is_ok());
    assert!(serde_json::from_value::<SettingsWeekdays>(json!([])).is_err());
    assert!(serde_json::from_value::<SettingsWeekdays>(json!([monday.clone(), monday])).is_err());
    let mut snapshot = json!({"snapshot_id": "ab".repeat(32), "revision": "1", "items": [], "complete": true,
                              "next_cursor": null, "cursor": "cd".repeat(32)});
    assert!(serde_json::from_value::<ApprovalWatchSnapshot>(snapshot.clone()).is_ok());
    snapshot["approved"] = json!(true);
    assert!(serde_json::from_value::<ApprovalWatchSnapshot>(snapshot).is_err());
}
