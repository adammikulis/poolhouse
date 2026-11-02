mod kit;

use kit::*;
use poolhouse_node::node::Node;
use serde_json::{json, Value};
use tempfile::tempdir;

fn listed(n: &mut Node, token: &str, name: &str) -> Value {
    ok(req(n, "agents", "demo", token, json!({})))["agents"].as_array().unwrap().iter().find(|a| a["name"] == name).unwrap().clone()
}

fn person(n: &mut Node, token: &str, id: &str) -> Value {
    req(n, "register_person", "demo", token, json!({"session": id}))
}

#[test]
fn a_session_cannot_register_or_claim_the_harness_of_the_persons_own_session() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (name, token) = session(&mut n, "demo", "agent");
    for harness in ["poolhouse-ui", "Poolhouse-UI", "  poolhouse-ui "] {
        let made = req(&mut n, "register", "demo", "", json!({"model": "person", "harness": harness, "session": "person-1"}));
        assert_eq!(code(&made), "denied", "registering as {harness:?}");
        assert_eq!(code(&req(&mut n, "whoami", "demo", &token, json!({"model": "person", "harness": harness}))), "denied", "claiming {harness:?}");
    }
    let me = listed(&mut n, &token, &name);
    assert_eq!((me["harness"].clone(), me["person"].clone()), (json!("claude-code"), json!(false)), "the refusals changed nothing");
    assert_eq!(ok(req(&mut n, "agents", "demo", &token, json!({})))["agents"].as_array().unwrap().len(), 1, "no session was made");
}

#[test]
fn the_model_person_is_a_claim_like_any_other_and_makes_nobody_the_person() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let said = ok(req(&mut n, "register", "demo", "", json!({"model": "person", "harness": "claude-code", "session": "claimant"})));
    let (name, token) = (said["name"].as_str().unwrap().to_string(), said["token"].as_str().unwrap().to_string());
    assert_eq!(listed(&mut n, &token, &name)["person"], false);
    let again = ok(req(&mut n, "whoami", "demo", &token, json!({"model": "person"})));
    assert_eq!((again["identity"]["model"].clone(), again["identity"]["person"].clone()), (json!("person"), json!(false)));
}

#[test]
fn only_the_persons_own_call_makes_a_session_the_person_and_the_mark_is_the_node_s_alone() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, lead) = session(&mut n, "demo", "lead");
    let made = ok(person(&mut n, "", "person-1"));
    let (name, token) = (made["name"].as_str().unwrap().to_string(), made["token"].as_str().unwrap().to_string());
    let me = listed(&mut n, &lead, &name);
    assert_eq!((me["parent"].clone(), me["harness"].clone(), me["model"].clone(), me["person"].clone()), (json!(""), json!("poolhouse-ui"), json!("person"), json!(true)));
    assert_eq!(ok(req(&mut n, "whoami", "demo", &token, json!({})))["identity"]["person"], true);
    let again = ok(person(&mut n, &token, "person-1"));
    assert_eq!((again["name"].clone(), again["created"].clone()), (json!(name), json!(false)), "the same session again is the same session");
    let others: Vec<Value> = ok(req(&mut n, "agents", "demo", &lead, json!({})))["agents"].as_array().unwrap().iter().filter(|a| a["person"] == true).cloned().collect();
    assert_eq!(others.len(), 1, "one session carries the mark");
    let entries = ok(req(&mut n, "read", "demo", &lead, json!({"kind": "identity"})));
    assert!(entries["entries"].as_array().unwrap().iter().all(|e| e["fields"].get("person").is_none()), "the mark is not a field of an identity entry another device reads");
    drop(n);
    let mut reopened = node(dir.path());
    assert_eq!(listed(&mut reopened, &token, &name)["person"], true, "the mark outlasts a restart");
}

#[test]
fn the_persons_session_has_no_parent_and_is_not_taken_or_renamed_by_a_session_that_asks() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, lead) = session(&mut n, "demo", "lead");
    let before = ok(req(&mut n, "agents", "demo", &lead, json!({})))["agents"].as_array().unwrap().len();
    assert_eq!(code(&person(&mut n, &lead, "person-1")), "denied", "a person's session is no subagent of a session");
    assert_eq!(ok(req(&mut n, "agents", "demo", &lead, json!({})))["agents"].as_array().unwrap().len(), before, "no session was made");
    let made = ok(person(&mut n, "", "person-1"));
    let (name, token) = (made["name"].as_str().unwrap().to_string(), made["token"].as_str().unwrap().to_string());
    let taken = req(&mut n, "register", "demo", "", json!({"model": "person", "harness": "poolhouse-ui", "session": "person-1"}));
    assert_eq!(code(&taken), "denied", "the native identity of the person's session is not registered by the ordinary call");
    assert_eq!(code(&person(&mut n, &lead, "person-1")), "denied", "and not with the token of a session that is not it");
    for claim in [json!({"harness": "claude-code"}), json!({"model": "claude-opus-5-5"})] {
        assert_eq!(code(&req(&mut n, "whoami", "demo", &token, claim)), "denied", "the person's session is named by the node");
    }
    let me = listed(&mut n, &lead, &name);
    assert_eq!((me["harness"].clone(), me["model"].clone(), me["person"].clone()), (json!("poolhouse-ui"), json!("person"), json!(true)));
}
