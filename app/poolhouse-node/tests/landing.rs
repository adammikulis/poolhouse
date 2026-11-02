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

fn accepts(n: &mut Node, token: &str, at: &str) -> Value {
    ok(req(n, "land_accepts", "demo", token, json!({"sha": at})))
}

fn accepts_for(n: &mut Node, token: &str, branch: &str, at: &str, worktree: &str) -> Value {
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
    let ((_, _), (bob, bt), (_, lt)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"), session(&mut n, "demo", "pusher"));
    let tip = sha(1);
    for (branch, at_sha, verdict) in [("feat", "abc".to_string(), "accept"), ("feat", tip.clone(), "maybe"), ("main", tip.clone(), "accept"), ("../x", tip.clone(), "accept")] {
        assert_eq!(code(&review(&mut n, &bt, branch, &at_sha, verdict)), "invalid", "{branch} {at_sha} {verdict}");
    }
    ok(review(&mut n, &bt, "feat", &tip, "accept"));
    let found = accepts(&mut n, &lt, &tip);
    assert_eq!((reviewers(&found), found["reviews"][0]["verdict"].clone(), found["reviews"][0]["standing"]["name"].clone()), (vec![bob.clone()], json!("accept"), json!(bob)));
    assert_eq!(found["reviews"][0]["standing"]["model"], "claude-sonnet-5-5");
    assert!(reviewers(&accepts(&mut n, &lt, &sha(2))).is_empty(), "a review stands for its own commit only");
    ok(review(&mut n, &bt, "feat", &tip, "reject"));
    assert_eq!(accepts(&mut n, &lt, &tip)["reviews"][0]["verdict"], "reject", "a later review of one reviewer replaces its earlier one");
}

#[test]
fn a_review_stands_for_its_commit_under_whatever_branch_name_the_reviewer_wrote() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((alice, at), (bob, bt), (_, lt)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"), session(&mut n, "demo", "pusher"));
    let tip = sha(3);
    ok(review(&mut n, &at, "feat", &tip, "accept"));
    ok(review(&mut n, &bt, "elsewhere", &tip, "reject"));
    let found = accepts(&mut n, &lt, &tip);
    let mut by = reviewers(&found);
    by.sort();
    let mut expected = vec![alice, bob];
    expected.sort();
    assert_eq!(by, expected, "{found}");
    let branches: Vec<&str> = found["reviews"].as_array().unwrap().iter().map(|r| r["branch"].as_str().unwrap()).collect();
    assert!(branches.contains(&"feat") && branches.contains(&"elsewhere"), "{branches:?}");
}

#[test]
fn a_row_appended_under_a_reviewers_name_is_read_and_the_caller_never_counts_itself() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((maker, _), (_, bt)) = (session(&mut n, "demo", "maker"), session(&mut n, "demo", "b"));
    let tip = sha(4);
    let body = json!({"ev": "review", "branch": "feat", "sha": tip, "verdict": "accept", "standing": {"name": maker, "model": "claude-sonnet-5-5", "model_state": ""}});
    n.host("demo").unwrap().board.append(Kind::Landing, &maker, body, "").unwrap();
    assert_eq!(reviewers(&accepts(&mut n, &bt, &tip)), vec![maker]);
    ok(review(&mut n, &bt, "feat", &tip, "accept"));
    assert_eq!(reviewers(&accepts(&mut n, &bt, &tip)).len(), 1, "the caller does not count itself");
}

#[test]
fn a_session_of_another_board_or_a_retired_one_asks_nothing() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, lead) = session(&mut n, "demo", "lead");
    let (_, elsewhere) = session(&mut n, "other", "x");
    let kid = ok(req(&mut n, "register", "demo", &lead, json!({"model": "claude-sonnet-5-5", "harness": "claude-code", "session": "kid"})));
    let (name, token) = (kid["name"].as_str().unwrap().to_string(), kid["token"].as_str().unwrap().to_string());
    assert_eq!(code(&req(&mut n, "land_accepts", "demo", &elsewhere, json!({"sha": sha(5)}))), "denied");
    ok(req(&mut n, "retire", "demo", &lead, json!({"target": name})));
    assert_eq!(code(&review(&mut n, &token, "z", &sha(5), "accept")), "denied");
}

#[test]
fn entries_of_the_retired_landing_queue_fold_and_count_for_nothing() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((worker, _), (judge, _), (_, lt)) = (session(&mut n, "demo", "worker"), session(&mut n, "demo", "judge"), session(&mut n, "demo", "pusher"));
    let tip = sha(6);
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
    assert!(reviewers(&accepts(&mut n, &lt, &tip)).is_empty(), "a review of the queue names a request, not a branch, and counts for nothing");
    assert!(n.host("demo").unwrap().board.rejected().is_empty(), "the old entries fold: {:?}", n.host("demo").unwrap().board.rejected());
}

fn child(n: &mut Node, parent_token: &str, id: &str) -> (String, String) {
    let r = ok(req(n, "register", "demo", parent_token, json!({"model": "claude-haiku-5-5", "harness": "claude-code", "session": id})));
    (r["name"].as_str().unwrap().into(), r["token"].as_str().unwrap().into())
}

#[test]
fn a_session_started_by_a_creator_or_by_one_of_its_subagents_is_never_counted_and_cannot_review() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((lead, lt), (judge, jt), (_, lander)) = (session(&mut n, "demo", "lead"), session(&mut n, "demo", "judge"), session(&mut n, "demo", "lander"));
    let ((kid, kt), tip) = (child(&mut n, &lt, "kid"), sha(7));
    let (grandkid, gt) = child(&mut n, &kt, "grandkid");
    claim(&mut n, &lt, "branch", "feat");
    for (who, token) in [(&kid, &kt), (&grandkid, &gt)] {
        assert_eq!(code(&review(&mut n, token, "feat", &tip, "accept")), "denied", "{who} was started by the session that holds the claim");
    }
    ok(review(&mut n, &jt, "feat", &tip, "accept"));
    for who in [&kid, &grandkid] {
        let body = json!({"ev": "review", "branch": "feat", "sha": tip, "verdict": "accept", "standing": {"name": who, "model": "claude-haiku-5-5", "model_state": ""}});
        n.host("demo").unwrap().board.append(Kind::Landing, who, body, "").unwrap();
    }
    let found = accepts_for(&mut n, &lander, "feat", &tip, "");
    assert_eq!(reviewers(&found), vec![judge.clone()], "a row written under the name of a session below the creator is dropped when read: {found}");
    assert_eq!(found["creators"], json!([lead]));
    ok(req(&mut n, "retire", "demo", &lt, json!({"target": kid})));
    assert_eq!(code(&review(&mut n, &gt, "feat", &tip, "accept")), "denied", "the line to the creator runs through a session that has ended");
    assert_eq!(reviewers(&accepts_for(&mut n, &lander, "feat", &tip, "")), vec![judge], "and so does what the node counts");
}

#[test]
fn a_sibling_of_the_session_that_claimed_the_branch_is_independent_of_it() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((_, lt), (_, lander)) = (session(&mut n, "demo", "lead"), session(&mut n, "demo", "lander"));
    let ((worker, wt), (reviewer, rt), tip) = (child(&mut n, &lt, "worker"), child(&mut n, &lt, "reviewer"), sha(8));
    let (helper, ht) = child(&mut n, &wt, "helper");
    claim(&mut n, &wt, "branch", "feat");
    assert_eq!(code(&review(&mut n, &ht, "feat", &tip, "accept")), "denied", "{helper} was started by the session that holds the claim");
    ok(review(&mut n, &rt, "feat", &tip, "accept"));
    let found = accepts_for(&mut n, &lander, "feat", &tip, "");
    assert_eq!((reviewers(&found), found["creators"].clone()), (vec![reviewer], json!([worker])), "the creator's own parent and sibling are not below it");
}
