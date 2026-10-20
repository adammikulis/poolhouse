mod kit;

use kit::*;
use poolhouse_node::node::Node;
use poolhouse_node::row::Kind;
use serde_json::{json, Value};
use std::collections::BTreeMap;
use tempfile::tempdir;

/// The rows this node's own log holds, counted by what they say (the labels of `scripts/board_tally.py`).
fn tally(n: &mut Node) -> BTreeMap<String, usize> {
    let hosted = n.host("demo").unwrap();
    let mut out = BTreeMap::new();
    for r in hosted.board.rows(hosted.board.origin()) {
        let said = |k: &str| r.body[k].as_str().unwrap_or("").to_string();
        let label = match r.kind {
            Kind::Lease => format!("lease {} {}", r.body["resources"][0].as_str().unwrap_or("").split(':').take(2).collect::<Vec<_>>().join(" "), said("action")),
            Kind::Audit => format!("audit {}", said("event")),
            Kind::Identity if r.body["retired"] == Value::Bool(true) => "identity retired".to_string(),
            Kind::Identity => "identity registered or updated".to_string(),
            Kind::Head => continue,
            other => format!("{other:?}"),
        };
        *out.entry(label).or_default() += 1;
    }
    out
}

fn rows(n: &mut Node, label: &str) -> usize {
    tally(n).get(label).copied().unwrap_or(0)
}

fn total(n: &mut Node) -> usize {
    tally(n).values().sum()
}

fn subagent(n: &mut Node, parent: &str, id: &str, model: &str) -> (String, String) {
    let r = ok(req(n, "register", "demo", parent, json!({"model": model, "harness": "claude-code", "session": id})));
    (r["name"].as_str().unwrap().into(), r["token"].as_str().unwrap().into())
}

/// Ten subagents that start, say the model they run on at the end and retire: an identity row when each starts and one when each ends,
/// the ending carrying who ended it and what it released; no audit row and no row for a label that changed.
#[test]
fn a_subagent_writes_one_identity_row_when_it_starts_and_one_when_it_ends() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, lead) = session(&mut n, "demo", "lead");
    let before = total(&mut n);
    for i in 0..10 {
        let (_, token) = subagent(&mut n, &lead, &format!("kid-{i}"), "");
        ok(req(&mut n, "whoami", "demo", &token, json!({"model": "claude-sonnet-5-5", "harness": "claude-code"})));
        ok(req(&mut n, "retire", "demo", &token, json!({})));
    }
    let after = tally(&mut n);
    assert_eq!(after.values().sum::<usize>() - before, 20, "{after:?}");
    assert_eq!(after.get("audit retire"), None, "{after:?}");
    assert_eq!(after.get("identity retired").copied(), Some(10), "{after:?}");
}

/// A subagent that runs on another model than its parent's has that model on the board: one more identity row, the same for all ten.
#[test]
fn a_subagent_on_a_model_of_its_own_writes_that_model_to_the_board() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, lead) = session(&mut n, "demo", "lead");
    let before = total(&mut n);
    for i in 0..10 {
        let (name, token) = subagent(&mut n, &lead, &format!("kid-{i}"), "");
        ok(req(&mut n, "whoami", "demo", &token, json!({"model": "claude-haiku-5-5", "harness": "claude-code"})));
        ok(req(&mut n, "retire", "demo", &token, json!({})));
        let read = ok(req(&mut n, "read", "demo", &lead, json!({"kind": "identity"})));
        let last = read["entries"].as_array().unwrap().iter().filter(|e| e["fields"]["name"] == name.as_str()).last().unwrap().clone();
        assert_eq!((last["fields"]["model"].clone(), last["fields"]["model_state"].clone()), (json!("claude-haiku-5-5"), json!("claimed")), "{last}");
    }
    assert_eq!(total(&mut n) - before, 30);
}

/// The row of an ending says who ended the session and why, which the audit row used to say.
#[test]
fn the_ending_row_names_who_ended_the_session_and_what_was_released() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (lead_name, lead) = session(&mut n, "demo", "lead");
    let (kid, _) = subagent(&mut n, &lead, "kid", "claude-haiku-5-5");
    ok(req(&mut n, "retire", "demo", &lead, json!({"target": kid})));
    let read = ok(req(&mut n, "read", "demo", &lead, json!({"kind": "identity"})));
    let ended = read["entries"].as_array().unwrap().iter().filter(|e| e["fields"]["name"] == kid.as_str() && e["fields"]["retired"] == true).last().unwrap().clone();
    assert_eq!(ended["fields"]["retired_by"], lead_name.as_str(), "{ended}");
    assert!(ended["fields"]["detail"].as_str().unwrap().contains("0 leases released"), "{ended}");
    ok(req(&mut n, "retire", "demo", &lead, json!({})));
    assert_eq!(rows(&mut n, "identity retired"), 2);
}

/// A claim of a thing on this device alone is held in this device's table and leaves nothing on the board.
#[test]
fn claims_of_things_on_this_device_alone_write_no_row() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, token) = session(&mut n, "demo", "a");
    let before = total(&mut n);
    for (kind, key) in [("install", "/venv/a"), ("worktree", "/work/a"), ("area", "/repo/src"), ("port", "9301"), ("server", "broker")] {
        ok(req(&mut n, "claim", "demo", &token, json!({"kind": kind, "key": key, "ttl_s": 600})));
        let held = ok(req(&mut n, "claims", "demo", &token, json!({"kind": kind})));
        assert_eq!(held["claims"].as_array().unwrap().len(), 1, "{kind} is held");
        ok(req(&mut n, "release", "demo", &token, json!({"kind": kind, "key": key})));
        assert_eq!(ok(req(&mut n, "claims", "demo", &token, json!({"kind": kind})))["claims"].as_array().unwrap().len(), 0, "{kind} is released");
    }
    ok(req(&mut n, "reserve", "demo", &token, json!({"resources": [{"kind": "worktree", "key": "/work/b"}, {"kind": "install", "key": "/venv/b"}], "ttl_s": 600})));
    assert_eq!(total(&mut n), before, "{:?}", tally(&mut n));
}

/// A branch is a name the whole pool shares: its claim and its release are rows.
#[test]
fn a_branch_claim_is_a_row_when_taken_and_when_released() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, token) = session(&mut n, "demo", "a");
    ok(req(&mut n, "claim", "demo", &token, json!({"kind": "branch", "key": "feat", "ttl_s": 600})));
    assert_eq!(rows(&mut n, "lease claim branch acquire"), 1);
    ok(req(&mut n, "release", "demo", &token, json!({"kind": "branch", "key": "feat"})));
    assert_eq!(rows(&mut n, "lease claim branch release"), 1);
}

/// A claim that leaves no row is still known after the node restarts, still keeps another session out, and still ends with its process.
#[test]
fn a_claim_without_a_row_survives_a_restart_and_still_conflicts() {
    let dir = tempdir().unwrap();
    let (name, token) = {
        let mut n = node(dir.path());
        let (name, token) = session(&mut n, "demo", "a");
        ok(req(&mut n, "claim", "demo", &token, json!({"kind": "worktree", "key": "/work/keep", "ttl_s": 600})));
        (name, token)
    };
    let mut n = node(dir.path());
    let (_, other) = session(&mut n, "demo", "b");
    let refused = req(&mut n, "claim", "demo", &other, json!({"kind": "worktree", "key": "/work/keep/sub"}));
    assert_eq!(code(&refused), "denied");
    assert!(refused["error"]["message"].as_str().unwrap().contains(&name), "{refused}");
    let _ = token;
}

/// The session that held a worktree claim stays the creator of record for a review although the claim left no row: after the claim is
/// released and the node restarted, the lander is still told who made it.
#[test]
fn the_holder_of_a_worktree_claim_without_a_row_is_still_a_creator_of_record() {
    let dir = tempdir().unwrap();
    let tip = "a".repeat(40);
    let maker = {
        let mut n = node(dir.path());
        let (name, token) = session(&mut n, "demo", "maker");
        ok(req(&mut n, "claim", "demo", &token, json!({"kind": "worktree", "key": "/w/feat", "ttl_s": 600})));
        ok(req(&mut n, "release", "demo", &token, json!({"kind": "worktree", "key": "/w/feat"})));
        name
    };
    let mut n = node(dir.path());
    let (_, lander) = session(&mut n, "demo", "lander");
    let found = ok(req(&mut n, "land_accepts", "demo", &lander, json!({"branch": "feat", "sha": tip, "worktree": "/w/feat"})));
    assert_eq!(found["creators"], json!([maker]), "{found}");
    assert_eq!(rows(&mut n, "lease claim worktree acquire"), 0);
}

/// An area is spelled under the path of one device's repository: another device holding the same spelling is a different thing, so the
/// two do not wait for each other, and a branch is still one pool-wide name.
#[test]
fn an_area_is_held_per_device_and_a_branch_per_pool() {
    let (da, db) = (tempdir().unwrap(), tempdir().unwrap());
    let (mut a, mut b) = (node(da.path()), node(db.path()));
    let ((_, ta), (_, tb)) = (session(&mut a, "demo", "a"), session(&mut b, "demo", "b"));
    ok(req(&mut a, "claim", "demo", &ta, json!({"kind": "area", "key": "/repo/src", "ttl_s": 600})));
    ok(req(&mut a, "claim", "demo", &ta, json!({"kind": "branch", "key": "feat", "ttl_s": 600})));
    poolhouse_node::sync::exchange_nodes(&mut a, &mut b).unwrap();
    ok(req(&mut b, "claim", "demo", &tb, json!({"kind": "area", "key": "/repo/src", "ttl_s": 600})));
    assert_eq!(code(&req(&mut b, "claim", "demo", &tb, json!({"kind": "branch", "key": "feat"}))), "denied");
}
