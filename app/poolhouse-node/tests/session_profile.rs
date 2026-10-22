mod kit;

use kit::*;
use serde_json::{json, Value};
use tempfile::tempdir;

fn listed(n: &mut poolhouse_node::node::Node, token: &str, name: &str) -> Value {
    ok(req(n, "agents", "demo", token, json!({})))["agents"].as_array().unwrap().iter().find(|a| a["name"] == name).unwrap().clone()
}

fn child(n: &mut poolhouse_node::node::Node, parent_token: &str, id: &str) -> (String, String) {
    let made = ok(req(n, "register", "demo", parent_token, json!({"model": "claude-sonnet-5-5", "harness": "claude-code", "session": id})));
    (made["name"].as_str().unwrap().into(), made["token"].as_str().unwrap().into())
}

#[test]
fn a_session_states_its_purpose_branch_and_worktree_and_the_roster_shows_them_cleaned() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, lead) = session(&mut n, "demo", "lead");
    let (worker, token) = child(&mut n, &lead, "worker-1");
    let blank = listed(&mut n, &lead, &worker);
    assert_eq!((blank["purpose"].clone(), blank["branch"].clone(), blank["worktree"].clone()), (json!(""), json!(""), json!("")));
    let long = "x".repeat(500);
    let said = ok(req(&mut n, "whoami", "demo", &token, json!({"purpose": format!("rbmk\noperator\u{1b}[31m  example {long}"), "branch": "feat/a", "worktree": "/w/one"})));
    let shown = said["identity"].clone();
    assert_eq!(shown["branch"], "feat/a");
    assert_eq!(shown["worktree"], "/w/one");
    let purpose = shown["purpose"].as_str().unwrap();
    assert!(purpose.starts_with("rbmk operator [31m example x"), "one line, control characters spaces: {purpose:?}");
    assert_eq!(purpose.chars().count(), 120, "cut to 120 characters");
    assert!(!purpose.chars().any(char::is_control));
    assert_eq!(listed(&mut n, &lead, &worker)["purpose"], shown["purpose"], "the lead reads what the worker said");
}

#[test]
fn an_empty_field_keeps_what_was_said_and_a_restart_keeps_it() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, lead) = session(&mut n, "demo", "lead");
    let (worker, token) = child(&mut n, &lead, "worker-1");
    ok(req(&mut n, "whoami", "demo", &token, json!({"purpose": "board roster", "worktree": "/w/one"})));
    ok(req(&mut n, "whoami", "demo", &token, json!({"branch": "feat/board-roster"})));
    drop(n);
    let mut again = node(dir.path());
    let me = listed(&mut again, &lead, &worker);
    assert_eq!((me["purpose"].clone(), me["branch"].clone(), me["worktree"].clone()), (json!("board roster"), json!("feat/board-roster"), json!("/w/one")));
}

#[test]
fn the_profile_is_the_callers_own_and_a_wrong_type_is_refused() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (lead_name, lead) = session(&mut n, "demo", "lead");
    let (_, token) = child(&mut n, &lead, "worker-1");
    ok(req(&mut n, "whoami", "demo", &token, json!({"purpose": "worker one"})));
    assert_eq!(listed(&mut n, &lead, &lead_name)["purpose"], "", "a worker's statement is not the lead's");
    assert_eq!(code(&req(&mut n, "whoami", "demo", &token, json!({"purpose": 7}))), "invalid");
    assert_eq!(code(&req(&mut n, "whoami", "demo", &token, json!({"sender": "someone"}))), "denied", "no field names another session");
}
