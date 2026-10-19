//! A terminal registers as many main sessions as it needs, idle sessions are marked and retired by the node, and a session that ends
//! retires itself and its subagents and nobody else.
#![cfg(unix)]

mod kit;

use kit::procs::*;
use kit::*;
use poolhouse_node::lineage::Peer;
use poolhouse_node::node::Node;
use serde_json::{json, Value};
use tempfile::tempdir;

const DAY_MS: u64 = 86_400_000;

fn open(dir: &std::path::Path) -> Node {
    node(dir)
}

fn start(n: &mut Node, from: &Peer, harness: &str, session: &str) -> Value {
    req_from(n, from, "register", "demo", "", json!({"model": "claude-sonnet-5-5", "harness": harness, "session": session}))
}

fn names(n: &mut Node, token: &str, retired: bool) -> Vec<Value> {
    ok(req(n, "agents", "demo", token, json!({"retired": retired})))["agents"].as_array().unwrap().clone()
}

fn age(dir: &std::path::Path, name: &str, seen_ms_ago: u64) {
    let file = dir.join("boards").join("demo").join("names.json");
    let mut all: Value = serde_json::from_slice(&std::fs::read(&file).unwrap()).unwrap();
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis() as u64;
    all[name]["seen_ms"] = json!(now - seen_ms_ago);
    std::fs::write(&file, serde_json::to_vec(&all).unwrap()).unwrap();
}

#[test]
fn a_session_that_ends_loses_its_token_and_its_subagents_and_retires_nobody_else() {
    let dir = tempdir().unwrap();
    let mut n = open(dir.path());
    let ((lead, lt), (other, ot)) = (session(&mut n, "demo", "lead"), session(&mut n, "demo", "other"));
    let kid = ok(req(&mut n, "register", "demo", &lt, json!({"model": "claude-haiku-5-5", "harness": "claude-code", "session": "kid"})));
    let (kid_name, kt) = (kid["name"].as_str().unwrap().to_string(), kid["token"].as_str().unwrap().to_string());
    assert_eq!(code(&req(&mut n, "retire", "demo", &lt, json!({"target": other}))), "denied", "a main session retires no other main session");
    assert_eq!(code(&req(&mut n, "retire", "demo", &ot, json!({"target": lead}))), "denied");
    assert_eq!(code(&req(&mut n, "retire", "demo", &kt, json!({"target": lead}))), "denied", "a subagent does not retire its parent");
    assert_eq!(code(&req(&mut n, "retire", "demo", &lt, json!({"reason": "forged"}))), "denied", "a session does not flag itself");
    assert_eq!(ok(req(&mut n, "whoami", "demo", &lt, json!({})))["name"], lead.as_str(), "the refusals ended nothing");

    let done = ok(req(&mut n, "retire", "demo", &lt, json!({})));
    assert_eq!((done["name"].as_str().unwrap(), done["subagents_retired"].clone()), (lead.as_str(), json!(1)));
    for (who, token) in [("the session", &lt), ("its subagent", &kt)] {
        assert_eq!(code(&req(&mut n, "whoami", "demo", token, json!({}))), "denied", "{who} holds no token afterwards");
    }
    assert_eq!(ok(req(&mut n, "whoami", "demo", &ot, json!({})))["name"], other.as_str(), "another main session is as it was");
    let all = names(&mut n, &ot, true);
    let state = |who: &str| all.iter().find(|a| a["name"] == who).map(|a| (a["retired"].clone(), a["retired_reason"].clone())).unwrap();
    assert_eq!((state(&lead), state(&kid_name)), ((json!(true), json!("done")), (json!(true), json!("done"))));
    assert_eq!(code(&req(&mut n, "retire", "demo", &lt, json!({}))), "denied", "a retired token retires nothing");
    let audit = ok(req(&mut n, "read", "demo", &ot, json!({"kind": "audit"})));
    assert!(audit["entries"].as_array().unwrap().iter().any(|e| e["fields"]["event"] == "retire" && e["fields"]["subject"] == lead.as_str()));
}

#[test]
fn a_session_that_ended_registers_again_from_its_own_lineage_and_a_subagent_that_ended_never_does() {
    let dir = tempdir().unwrap();
    let mut n = open(dir.path());
    let (outside, me) = (process(true), me());
    let first = ok(start(&mut n, &me, "claude-code", "lead"));
    let (name, old) = (first["name"].as_str().unwrap().to_string(), first["token"].as_str().unwrap().to_string());
    let kid = ok(req(&mut n, "register", "demo", &old, json!({"model": "claude-haiku-5-5", "harness": "claude-code", "session": "kid"})));
    ok(req(&mut n, "retire", "demo", &old, json!({})));
    assert_eq!(code(&start(&mut n, &peer(&outside), "claude-code", "lead")), "denied", "from another terminal it is not the session that ended");
    assert_eq!(code(&req(&mut n, "whoami", "demo", &old, json!({}))), "denied");
    let again = ok(start(&mut n, &me, "claude-code", "lead"));
    assert_eq!((again["name"].as_str().unwrap(), again["created"].clone()), (name.as_str(), json!(false)), "it is the session it was");
    let fresh = again["token"].as_str().unwrap();
    assert_eq!(ok(req(&mut n, "whoami", "demo", fresh, json!({})))["name"], name.as_str());
    assert_eq!(code(&req(&mut n, "whoami", "demo", &old, json!({}))), "denied", "the token it ended with stays refused");
    let all = names(&mut n, fresh, true);
    assert_eq!(all.iter().find(|a| a["name"] == name.as_str()).unwrap()["retired"], false);
    let sub = req(&mut n, "register", "demo", fresh, json!({"model": "claude-haiku-5-5", "harness": "claude-code", "session": "kid"}));
    assert_eq!(code(&sub), "denied", "its subagent ended with it and a retired subagent registers no more");
    assert_eq!(all.iter().find(|a| a["name"] == kid["name"]).unwrap()["retired"], true);
    done(vec![outside]);
}

#[test]
fn a_terminal_starts_as_many_live_sessions_as_it_needs_and_so_does_a_harness_across_terminals() {
    let dir = tempdir().unwrap();
    let mut n = open(dir.path());
    let terminals = (process(true), process(true));
    let tokens: Vec<String> = (0..80)
        .map(|i| ok(start(&mut n, &peer(&terminals.0), "claude-code", &format!("runaway-{i}")))["token"].as_str().unwrap().to_string())
        .collect();
    for i in 0..30 {
        assert_eq!(ok(start(&mut n, &peer(&terminals.1), "claude-code", &format!("other-{i}")))["created"], true);
    }
    assert_eq!(names(&mut n, &tokens[0], false).len(), 110, "every one of them is live");
    done(vec![terminals.0, terminals.1]);
}

#[test]
fn idle_sessions_are_marked_and_are_retired_after_a_day() {
    let dir = tempdir().unwrap();
    let (stale, quiet, busy, busy_token);
    {
        let mut n = open(dir.path());
        let a = session(&mut n, "demo", "stale");
        let b = session(&mut n, "demo", "quiet");
        let c = session(&mut n, "demo", "busy");
        (stale, quiet, busy, busy_token) = (a.0, b.0, c.0, c.1);
    }
    age(dir.path(), &stale, DAY_MS + 60_000);
    age(dir.path(), &quiet, 2 * 3600 * 1000);
    let mut n = open(dir.path());
    let listed = names(&mut n, &busy_token, true);
    let find = |who: &str| listed.iter().find(|a| a["name"] == who).unwrap().clone();
    assert_eq!(find(&busy)["idle"], false);
    assert_eq!((find(&quiet)["idle"].clone(), find(&quiet)["retired"].clone()), (json!(true), json!(false)), "quiet for two hours: marked idle, kept");
    assert_eq!((find(&stale)["retired"].clone(), find(&stale)["retired_reason"].clone()), (json!(true), json!("idle")), "idle for a day: retired by the node");
    let me = me();
    ok(start(&mut n, &me, "claude-code", "newcomer"));
    let audit = ok(req(&mut n, "read", "demo", &busy_token, json!({"kind": "audit"})));
    assert!(audit["entries"].as_array().unwrap().iter().any(|e| e["fields"]["event"] == "retire" && e["fields"]["subject"] == stale.as_str()), "the node says it retired it");
}

#[test]
fn a_token_of_a_session_the_node_retired_for_idleness_is_refused() {
    let dir = tempdir().unwrap();
    let (name, token, watcher);
    {
        let mut n = open(dir.path());
        let a = session(&mut n, "demo", "forgotten");
        let b = session(&mut n, "demo", "watcher");
        (name, token, watcher) = (a.0, a.1, b.1);
    }
    age(dir.path(), &name, 2 * DAY_MS);
    let mut n = open(dir.path());
    assert_eq!(names(&mut n, &watcher, false).len(), 1, "listing retires it");
    assert_eq!(code(&req(&mut n, "whoami", "demo", &token, json!({}))), "denied");
    assert_eq!(code(&req(&mut n, "post", "demo", &token, json!({"kind": "message", "fields": {"type": "status", "body": "still here"}}))), "denied");
}
