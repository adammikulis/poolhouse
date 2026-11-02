mod kit;

use kit::*;
use poolhouse_node::{lease::holders::{Holders, HOLDERS_FILE}, node::Node};
use serde_json::json;
use tempfile::tempdir;

#[test]
fn queued_claims_reserve_history_before_granting() {
    let dir = tempdir().unwrap();
    std::fs::write(dir.path().join("lease-config.json"), json!({"max_worktree_records":16}).to_string()).unwrap();
    let mut n = node(dir.path());
    let (_, blocker) = session(&mut n, "demo", "blocker");
    let (_, actor) = session(&mut n, "demo", "actor");
    ok(req(&mut n, "claim", "demo", &blocker, json!({"kind":"worktree","key":"/blocked"})));
    for i in 0..15 {
        let result = ok(req(&mut n, "lease_acquire", "demo", &actor, json!({"resources":[{"type":"claim","kind":"worktree","name":format!("/blocked/{i}")}],"wait":true})));
        assert_eq!(result["state"], "queued", "{result}");
    }
    let refused = req(&mut n, "lease_acquire", "demo", &actor, json!({"resources":[{"type":"claim","kind":"worktree","name":"/blocked/overflow"}],"wait":true}));
    assert_eq!(code(&refused), "quota", "{refused}");
}

#[test]
fn batched_reservations_cannot_exceed_history_capacity() {
    let dir = tempdir().unwrap();
    std::fs::write(dir.path().join("lease-config.json"), json!({"max_worktree_records":16}).to_string()).unwrap();
    let mut n = node(dir.path());
    let (_, actor) = session(&mut n, "demo", "actor");
    let resources: Vec<_> = (0..17).map(|i| json!({"kind":"worktree","key":format!("/batch/{i}")})).collect();
    let refused = req(&mut n, "reserve", "demo", &actor, json!({"resources":resources}));
    assert_eq!(code(&refused), "quota", "{refused}");
    assert!(n.leases.table.leases.is_empty());
    assert_eq!(n.leases.holders.of("demo", "/batch/0").len(), 1);
}

#[test]
fn corrupt_or_unreadable_existing_history_refuses_open() {
    let dir = tempdir().unwrap();
    std::fs::write(dir.path().join(HOLDERS_FILE), b"{").unwrap();
    assert!(Holders::open(dir.path()).is_err());
    std::fs::remove_file(dir.path().join(HOLDERS_FILE)).unwrap();
    std::fs::create_dir(dir.path().join(HOLDERS_FILE)).unwrap();
    assert!(Holders::open(dir.path()).is_err());
}

#[test]
fn failed_history_write_is_retried_without_acknowledging_a_claim() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, actor) = session(&mut n, "demo", "actor");
    std::fs::create_dir(dir.path().join(HOLDERS_FILE)).unwrap();
    for _ in 0..2 {
        let result = req(&mut n, "claim", "demo", &actor, json!({"kind":"worktree","key":"/retry"}));
        assert_eq!(code(&result), "io", "{result}");
        assert!(n.leases.table.leases.is_empty());
        assert!(n.leases.holders.of("demo", "/retry").is_empty());
    }
    std::fs::remove_dir(dir.path().join(HOLDERS_FILE)).unwrap();
    ok(req(&mut n, "claim", "demo", &actor, json!({"kind":"worktree","key":"/retry"})));
    assert_eq!(n.leases.holders.of("demo", "/retry").len(), 1);
}

#[test]
fn held_claim_evidence_is_reconstructed_before_reopen_returns() {
    let dir = tempdir().unwrap();
    let name = {
        let mut n = node(dir.path());
        let (name, actor) = session(&mut n, "demo", "actor");
        ok(req(&mut n, "claim", "demo", &actor, json!({"kind":"worktree","key":"/reopen"})));
        name
    };
    std::fs::remove_file(dir.path().join(HOLDERS_FILE)).unwrap();
    let n = Node::open(dir.path()).unwrap();
    assert_eq!(n.leases.holders.of("demo", "/reopen"), vec![name]);
}

#[test]
fn old_creator_evidence_never_ages_out() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (name, actor) = session(&mut n, "demo", "actor");
    ok(req(&mut n, "claim", "demo", &actor, json!({"kind":"worktree","key":"/old"})));
    n.leases.clock = Box::new(|| u64::MAX / 2);
    ok(req(&mut n, "claim", "demo", &actor, json!({"kind":"worktree","key":"/new"})));
    assert_eq!(n.leases.holders.of("demo", "/old"), vec![name]);
}
