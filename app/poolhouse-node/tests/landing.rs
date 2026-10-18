mod kit;

use kit::*;
use poolhouse_node::node::Node;
use poolhouse_node::row::Kind;
use serde_json::{json, Value};
use tempfile::tempdir;

fn sha(n: u8) -> String {
    format!("{n:02x}{}", "abcdef0123456789abcdef0123456789abcdef"[..38].to_string())
}

fn review(n: &mut Node, token: &str, branch: &str, at: &str, verdict: &str) -> Value {
    req(n, "land_review", "demo", token, json!({"branch": branch, "sha": at, "verdict": verdict}))
}

fn accepts(n: &mut Node, token: &str, branch: &str, at: &str, worktree: &str) -> Value {
    ok(req(n, "land_accepts", "demo", token, json!({"branch": branch, "sha": at, "worktree": worktree})))
}

fn reviewers(found: &Value) -> Vec<String> {
    found["reviews"].as_array().unwrap().iter().map(|r| r["by"].as_str().unwrap().to_string()).collect()
}

fn claim(n: &mut Node, token: &str, kind: &str, key: &str) {
    ok(req(n, "claim", "demo", token, json!({"kind": kind, "key": key})));
}

#[test]
fn a_review_names_a_branch_a_full_commit_and_a_verdict_and_is_stamped_with_its_sender() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((alice, at), (bob, bt), (_, lt)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"), session(&mut n, "demo", "lander"));
    claim(&mut n, &at, "branch", "feat");
    let tip = sha(1);
    for (branch, at_sha, verdict) in [("feat", "abc".to_string(), "accept"), ("feat", tip.clone(), "maybe"), ("main", tip.clone(), "accept"), ("../x", tip.clone(), "accept")] {
        assert_eq!(code(&review(&mut n, &bt, branch, &at_sha, verdict)), "invalid", "{branch} {at_sha} {verdict}");
    }
    ok(review(&mut n, &bt, "feat", &tip, "accept"));
    let found = accepts(&mut n, &lt, "feat", &tip, "");
    assert_eq!((reviewers(&found), found["reviews"][0]["verdict"].clone(), found["reviews"][0]["standing"]["name"].clone()), (vec![bob.clone()], json!("accept"), json!(bob)));
    assert_eq!(found["reviews"][0]["standing"]["model"], "claude-sonnet-5-5");
    assert_eq!(found["creators"], json!([alice]));
    assert!(reviewers(&accepts(&mut n, &lt, "feat", &sha(2), "")).is_empty(), "a review stands for its own commit only");
    assert!(reviewers(&accepts(&mut n, &lt, "other", &tip, "")).is_empty(), "and for its own branch only");
    ok(review(&mut n, &bt, "feat", &tip, "reject"));
    assert_eq!(accepts(&mut n, &lt, "feat", &tip, "")["reviews"][0]["verdict"], "reject", "a later review of one reviewer replaces its earlier one");
}

#[test]
fn a_session_that_ever_held_the_claim_on_the_branch_or_its_worktree_is_never_counted() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((maker, mt), (past, pt), (_, ot), (_, lt)) = (session(&mut n, "demo", "maker"), session(&mut n, "demo", "past"), session(&mut n, "demo", "other"), session(&mut n, "demo", "lander"));
    let tip = sha(3);
    assert_eq!(accepts(&mut n, &lt, "feat", &tip, "/w/feat")["creators"], json!([]), "no claim, no creator of record");
    claim(&mut n, &mt, "branch", "feat");
    claim(&mut n, &pt, "worktree", "/w/feat");
    ok(req(&mut n, "release", "demo", &pt, json!({"kind": "worktree", "key": "/w/feat"})));
    for (token, branch) in [(&mt, "feat"), (&pt, "feat"), (&ot, "feat")] {
        let done = review(&mut n, token, branch, &tip, "accept");
        assert_eq!(done["ok"] == false, token == &mt, "the holder is refused at once; the rest are filtered when read: {done}");
    }
    ok(review(&mut n, &mt, "elsewhere", &tip, "accept"));
    ok(review(&mut n, &pt, "feat", &tip, "accept"));
    let found = accepts(&mut n, &lt, "feat", &tip, "/w/feat");
    assert_eq!(reviewers(&found).len(), 1, "only the session that held nothing counts: {found}");
    assert_ne!(found["reviews"][0]["by"], json!(maker));
    assert_ne!(found["reviews"][0]["by"], json!(past));
    let mut who = found["creators"].as_array().unwrap().clone();
    who.sort_by_key(|v| v.as_str().unwrap().to_string());
    assert_eq!(who.len(), 2, "a released claim is still on record: {found}");
    assert_eq!(reviewers(&accepts(&mut n, &lt, "feat", &tip, "")).len(), 2, "a worktree only excludes when the caller names it");
}

#[test]
fn a_row_appended_under_the_creator_name_is_dropped_when_read_and_the_caller_never_counts_itself() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((maker, mt), (_, bt)) = (session(&mut n, "demo", "maker"), session(&mut n, "demo", "b"));
    claim(&mut n, &mt, "branch", "feat");
    let tip = sha(4);
    let body = json!({"ev": "review", "branch": "feat", "sha": tip, "verdict": "accept", "standing": {"name": maker, "model": "claude-sonnet-5-5", "model_state": ""}});
    n.host("demo").unwrap().board.append(Kind::Landing, &maker, body, "").unwrap();
    assert!(reviewers(&accepts(&mut n, &bt, "feat", &tip, "")).is_empty());
    ok(review(&mut n, &bt, "feat", &tip, "accept"));
    assert!(reviewers(&accepts(&mut n, &bt, "feat", &tip, "")).is_empty(), "the caller is the lander and does not count");
}

#[test]
fn a_session_of_another_board_or_a_retired_one_asks_nothing() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, lead) = session(&mut n, "demo", "lead");
    let (_, elsewhere) = session(&mut n, "other", "x");
    let kid = ok(req(&mut n, "register", "demo", &lead, json!({"model": "claude-sonnet-5-5", "harness": "claude-code", "session": "kid"})));
    let (name, token) = (kid["name"].as_str().unwrap().to_string(), kid["token"].as_str().unwrap().to_string());
    assert_eq!(code(&req(&mut n, "land_accepts", "demo", &elsewhere, json!({"branch": "z", "sha": sha(5), "worktree": ""}))), "denied");
    ok(req(&mut n, "retire", "demo", &lead, json!({"target": name})));
    assert_eq!(code(&review(&mut n, &token, "z", &sha(5), "accept")), "denied");
}

#[test]
fn entries_of_the_retired_landing_queue_fold_and_count_for_nothing() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((worker, wt), (judge, _), (_, lt)) = (session(&mut n, "demo", "worker"), session(&mut n, "demo", "judge"), session(&mut n, "demo", "lander"));
    let tip = sha(6);
    claim(&mut n, &wt, "branch", "feat");
    let standing = json!({"name": judge, "parent": "", "ancestors": [], "model": "claude-sonnet-5-5", "model_state": ""});
    let old = [
        (&worker, json!({"ev": "request", "branch": "feat", "sha": tip, "target": "dev", "selectors": ["tests/test_x.py"], "replaces": "x"})),
        (&judge, json!({"ev": "review", "req": "demo:d1:1", "sha": tip, "verdict": "accept", "standing": standing})),
        (&worker, json!({"ev": "state", "req": "demo:d1:1", "status": "landed", "detail": "d"})),
        (&worker, json!({"ev": "beat", "what": "x"})),
        (&worker, json!({"ev": "pause", "reason": "r"})),
    ];
    for (actor, body) in old {
        n.host("demo").unwrap().board.append(Kind::Landing, actor, body, "").unwrap();
    }
    assert!(reviewers(&accepts(&mut n, &lt, "feat", &tip, "")).is_empty(), "a review of the queue names a request, not a branch, and counts for nothing");
    assert!(n.host("demo").unwrap().board.rejected().is_empty(), "the old entries fold: {:?}", n.host("demo").unwrap().board.rejected());
}
