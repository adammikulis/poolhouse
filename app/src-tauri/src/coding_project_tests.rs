use super::{flow, identity, public, token, PURPOSE};
use serde_json::json;
use std::path::PathBuf;

const NONCE: &str = "nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn";
const CLAIM: &str = "cccccccccccccccccccccccccccccccc";

#[test]
fn fixed_opaque_arguments_refuse_paths_and_weak_tokens() {
    assert!(identity(&"a".repeat(32)) && token(NONCE));
    for value in ["../folder", "", &"A".repeat(32), &"a".repeat(33)] { assert!(!identity(value)); }
    for value in ["", "weak", &"/".repeat(32), &"x".repeat(129)] { assert!(!token(value)); }
}

#[test]
fn folder_selection_is_private_and_returns_only_opaque_project() {
    for status in ["complete", "needs-project"] {
        let mut calls = Vec::new();
        let answer = flow(|operation, payload| {
            calls.push((operation.to_string(),payload));
            Ok(if operation == "claim" { json!({"status":"choosing","mode":"folder","purpose":PURPOSE,"claim":CLAIM}) }
                else { json!({"status":status,"purpose":PURPOSE,"project":{"id":"project:opaque","title":"Example project"}}) })
        }, || Ok(Some(PathBuf::from("/private/selected/project"))), NONCE).unwrap();
        assert_eq!(calls[0], ("claim".into(),json!({"nonce":NONCE})));
        assert_eq!(calls[1], ("complete".into(),json!({"nonce":NONCE,"claim":CLAIM,"path":"/private/selected/project"})));
        assert_eq!(answer["status"], status);
        assert!(!answer.to_string().contains("private") && !answer.to_string().contains(CLAIM));
    }
}

#[test]
fn cancellation_and_dialog_failure_invalidate_exact_claim_without_path() {
    for failure in [false,true] {
        let mut operations = Vec::new();
        let answer = flow(|operation,payload| {
            operations.push(operation.to_string());
            Ok(if operation == "claim" { json!({"status":"choosing","mode":"folder","purpose":PURPOSE,"claim":CLAIM}) }
                else { assert_eq!(payload,json!({"nonce":NONCE,"claim":CLAIM})); json!({"status":"cancelled","purpose":PURPOSE}) })
        }, || if failure {Err("dialog failed")} else {Ok(None)}, NONCE).unwrap();
        assert_eq!(operations,["claim","cancel"]);
        assert_eq!(answer,json!({"status":"cancelled","purpose":PURPOSE}));
    }
}

#[test]
fn duplicate_and_terminal_receipts_never_reopen_dialog() {
    for receipt in [json!({"status":"choosing","purpose":PURPOSE}),json!({"status":"cancelled","purpose":PURPOSE}),
        json!({"status":"complete","purpose":PURPOSE,"project":{"id":"opaque","title":"Project"}})] {
        assert_eq!(flow(|operation,_| {assert_eq!(operation,"claim"); Ok(receipt.clone())},
            || panic!("no replayed selector"),NONCE).unwrap(),receipt);
    }
}

#[test]
fn wrong_purpose_mode_and_injected_claims_cannot_open_dialog() {
    for claim in [json!({"status":"choosing","mode":"file","purpose":PURPOSE,"claim":CLAIM}),
        json!({"status":"choosing","mode":"folder","purpose":"studio-watch","claim":CLAIM}),
        json!({"status":"choosing","mode":"folder","purpose":PURPOSE,"claim":"weak"}),
        json!({"status":"choosing","mode":"folder","purpose":PURPOSE,"claim":CLAIM,"path":"/private"})] {
        assert!(flow(|_,_| Ok(claim.clone()),|| panic!("invalid challenge"),NONCE).is_err());
    }
    assert!(flow(|_,_| Err("expired current session"),|| panic!("no dialog after revoked claim"),NONCE).is_err());
}

#[test]
fn late_completion_rejection_is_never_retried_or_rebound() {
    let mut operations = Vec::new();
    assert!(flow(|operation,_| {
        operations.push(operation.to_string());
        if operation == "claim" {Ok(json!({"status":"choosing","mode":"folder","purpose":PURPOSE,"claim":CLAIM}))}
        else {Err("session changed while dialog open")}
    },|| Ok(Some(PathBuf::from("/private/selected/project"))),NONCE).is_err());
    assert_eq!(operations,["claim","complete"]);
}

#[test]
fn public_receipts_reject_private_fields_paths_and_foreign_purpose() {
    for receipt in [json!({"status":"complete","purpose":PURPOSE,"project":{"id":"opaque","title":"/private/folder"}}),
        json!({"status":"complete","purpose":PURPOSE,"project":{"id":"opaque","title":"Project","path":"/private"}}),
        json!({"status":"complete","purpose":PURPOSE,"project":{"id":"../folder","title":"Project"}}),
        json!({"status":"complete","purpose":PURPOSE,"project":{"id":"opaque","title":"Project"},"claim":CLAIM}),
        json!({"status":"choosing","purpose":"other"}),json!({"status":"cancelled","purpose":PURPOSE,"path":"/private"})] {
        assert!(public(&receipt).is_err());
    }
}

#[test]
fn native_pickers_share_one_dialog_guard_until_drop() {
    let first = crate::native_picker::acquire().unwrap();
    assert!(crate::native_picker::acquire().is_none());
    drop(first);
    assert!(crate::native_picker::acquire().is_some());
}
