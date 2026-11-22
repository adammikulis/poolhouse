//! A token is a bearer secret in a file its user can read, so what it spends is bound to the lineage that registered it: a token
//! presented from another terminal still reads and sends, and spends no grant. While a process of the lineage a session is registered
//! from runs, no other live process spends what the session holds, even with a token of its own; when that lineage ends the session
//! follows the process that registers it, and a stale token is answered in the words that make its client register again. Nothing in
//! a refusal sends anyone to a terminal, a phone or a person.
#![cfg(unix)]

mod kit;

use kit::grants::{person, Spec};
use kit::procs::*;
use kit::*;
use poolhouse_node::lineage::Peer;
use poolhouse_node::node::Node;
use serde_json::{json, Value};
use tempfile::tempdir;

const SHA: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

fn register(n: &mut Node, from: &Peer, session: &str, token: &str) -> Value {
    req_from(n, from, "register", "demo", token, json!({"model": "claude-sonnet-5-5", "harness": "claude-code", "session": session}))
}

fn brake(n: &mut Node, from: &Peer, token: &str) -> Value {
    req_from(n, from, "task_mode", "demo", token, json!({"mode": "open"}))
}

fn verify(n: &mut Node, from: &Peer, token: &str, note: &str) -> Value {
    req_from(n, from, "note_verify", "demo", token, json!({"note": note, "exit": 0, "out_sha": SHA}))
}

/// A main session registered from this test's process that holds `task_authority`.
fn lead(n: &mut Node) -> (String, String) {
    let first = ok(register(n, &me(), "lead", ""));
    let (name, token) = (first["name"].as_str().unwrap().to_string(), first["token"].as_str().unwrap().to_string());
    allow(n, "demo", &token, &name, &["task_authority"]);
    (name, token)
}

fn message(reply: &Value) -> String {
    reply["error"]["message"].as_str().unwrap().trim_start_matches("denied: ").to_string()
}

/// A refusal that asks nothing of a person.
fn calm(reply: &Value) -> String {
    let said = message(reply);
    for word in ["rebind", "phone", "tap ", "typed", "type yes", "new session"] {
        assert!(!said.to_lowercase().contains(word), "a refusal sends nobody anywhere ({word}): {said}");
    }
    said
}

/// A main session registered from ``from`` that holds `task_authority`.
fn walker(n: &mut Node, from: &Peer) -> (String, String) {
    let first = ok(register(n, from, "walker", ""));
    let (name, token) = (first["name"].as_str().unwrap().to_string(), first["token"].as_str().unwrap().to_string());
    allow(n, "demo", &token, &name, &["task_authority"]);
    (name, token)
}

fn registered_from(n: &Node, name: &str) -> Vec<u32> {
    n.boards["demo"].names.get(name).unwrap().lineage.iter().map(|a| a.pid).collect()
}

#[test]
fn a_token_read_from_its_file_spends_no_grant_from_another_live_terminal_and_still_reads_and_sends() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, token) = lead(&mut n);
    let (inside, outside, harness) = (process(false), process(true), detached());
    assert_eq!(brake(&mut n, &me(), &token)["ok"], true, "the lineage that registered it spends it");
    assert_eq!(brake(&mut n, &peer(&inside), &token)["ok"], true, "a process of the same terminal spends it");
    assert_eq!(brake(&mut n, &peer(&harness), &token)["ok"], true, "so does a command a harness starts in a session of its own");

    let refused = brake(&mut n, &peer(&outside), &token);
    assert_eq!(code(&refused), "denied", "{refused}");
    let said = calm(&refused);
    assert!(said.contains("still running") && said.contains("terminal"), "the refusal says why and that it passes: {said}");
    assert!(refused["error"].get("grant").is_none(), "a grant is no help: no key is asked to sign one: {refused}");
    assert!(!said.contains(&token), "{said}");
    let uses = |n: &mut Node| ok(req(n, "grant_log", "demo", &token, json!({"limit": 200})))["log"].as_array().unwrap().iter().filter(|e| e["op"] == "use").count();
    assert_eq!(uses(&mut n), 3, "the refused call spent nothing");

    assert_eq!(req_from(&mut n, &peer(&outside), "whoami", "demo", &token, json!({}))["ok"], true, "a read is not a spend");
    let sent = req_from(&mut n, &peer(&outside), "post", "demo", &token, json!({"kind": "message", "fields": {"type": "status", "body": "hello"}}));
    assert_eq!(sent["ok"], true, "{sent}");
    done(vec![inside, outside, harness]);
}

#[test]
fn a_subagent_token_spends_only_where_its_parent_was_registered() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let first = ok(register(&mut n, &me(), "lead", ""));
    let (name, token) = (first["name"].as_str().unwrap().to_string(), first["token"].as_str().unwrap().to_string());
    enrolled_person(&mut n, &token);
    let to = json!({"kind": "session", "name": name, "subagents": true});
    let reply = person().issue(&mut |m, b, t, p| req(&mut n, m, b, t, p), "demo", &token, "g-00000000000000d1", &Spec::to(to, "note_verify"));
    assert_eq!(reply["ok"], true, "{reply}");
    let note = ok(req(&mut n, "post", "demo", &token, json!({"kind": "note", "fields": {"nkind": "fact", "title": "t", "body": "b", "verify_cmd": "true"}})));
    let note = note["seq"].to_string();

    let outside = process(true);
    let mine = ok(register(&mut n, &me(), "sub-a", &token))["token"].as_str().unwrap().to_string();
    assert_eq!(verify(&mut n, &me(), &mine, &note)["ok"], true, "a subagent its parent registered spends where the parent does");
    assert_eq!(code(&verify(&mut n, &peer(&outside), &mine, &note)), "denied", "and from another terminal it does not");

    let taken = register(&mut n, &peer(&outside), "sub-b", &token);
    let stolen = ok(taken.clone())["token"].as_str().unwrap().to_string();
    assert_eq!(ok(taken)["parent"], name.as_str(), "a subagent registers with its parent's token from any process");
    let refused = verify(&mut n, &peer(&outside), &stolen, &note);
    assert_eq!(code(&refused), "denied", "a token registered with a lifted parent token spends nowhere the parent was not: {refused}");
    calm(&refused);
    assert_eq!(verify(&mut n, &me(), &stolen, &note)["ok"], true, "the lineage it carries is its parent's");
    done(vec![outside]);
}

#[test]
fn a_subagent_that_presents_its_own_token_from_another_terminal_gets_a_token_of_the_lineage_it_was_registered_from() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let first = ok(register(&mut n, &me(), "lead", ""));
    let (name, token) = (first["name"].as_str().unwrap().to_string(), first["token"].as_str().unwrap().to_string());
    enrolled_person(&mut n, &token);
    let to = json!({"kind": "session", "name": name, "subagents": true});
    ok(person().issue(&mut |m, b, t, p| req(&mut n, m, b, t, p), "demo", &token, "g-00000000000000d2", &Spec::to(to, "note_verify")));
    let note = ok(req(&mut n, "post", "demo", &token, json!({"kind": "note", "fields": {"nkind": "fact", "title": "t", "body": "b", "verify_cmd": "true"}})))["seq"].to_string();
    let sub = ok(register(&mut n, &me(), "sub-a", &token))["token"].as_str().unwrap().to_string();

    let outside = process(true);
    let again = register(&mut n, &peer(&outside), "sub-a", &sub);
    assert_eq!(ok(again.clone())["parent"], name.as_str(), "a subagent registers again with its own token from any process: {again}");
    let lifted = ok(again)["token"].as_str().unwrap().to_string();
    assert_eq!(code(&verify(&mut n, &peer(&outside), &lifted, &note)), "denied", "and the token it gets spends nowhere its first one did not");
    assert_eq!(verify(&mut n, &me(), &lifted, &note)["ok"], true);
    done(vec![outside]);
}

fn enrolled_person(n: &mut Node, token: &str) {
    let dir = n.dir.clone();
    kit::grants::enrolled(&dir, &mut |m, b, t, p| req(n, m, b, t, p), token);
}

/// The state directory's `names.json` and `tokens.json` as a build that kept no lineage left them.
fn strip_lineage(dir: &std::path::Path) {
    let names = dir.join("boards/demo/names.json");
    let mut all: Value = serde_json::from_slice(&std::fs::read(&names).unwrap()).unwrap();
    for ident in all.as_object_mut().unwrap().values_mut() {
        ident.as_object_mut().unwrap().remove("lineage");
    }
    std::fs::write(&names, all.to_string()).unwrap();
    let tokens = dir.join("tokens.json");
    let mut all: Value = serde_json::from_slice(&std::fs::read(&tokens).unwrap()).unwrap();
    for held in all.as_object_mut().unwrap().values_mut() {
        held.as_object_mut().unwrap().remove("lineage");
    }
    std::fs::write(&tokens, all.to_string()).unwrap();
}

#[test]
fn a_live_sibling_that_registers_the_same_session_cannot_spend_its_grant_until_the_terminal_that_runs_it_ends() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (a, b) = (process(true), process(true));
    let (name, held) = walker(&mut n, &peer(&a));
    assert_eq!(brake(&mut n, &peer(&a), &held)["ok"], true, "the terminal that runs the session spends its grant");

    let sibling = ok(register(&mut n, &peer(&b), "walker", ""));
    assert_eq!(sibling["name"], name.as_str(), "the second terminal registers: nothing sends a person anywhere");
    let mine = sibling["token"].as_str().unwrap().to_string();
    let refused = brake(&mut n, &peer(&b), &mine);
    assert_eq!(code(&refused), "denied", "{refused}");
    let said = calm(&refused);
    assert!(said.contains(&name) && said.contains("still running"), "the refusal names the session and why: {said}");
    assert!(refused["error"].get("grant").is_none(), "{refused}");
    assert_eq!(registered_from(&n, &name), vec![a.id()], "the session stayed where it runs");
    assert_eq!(brake(&mut n, &peer(&a), &held)["ok"], true, "and the live sibling took nothing from it");
    assert_eq!(brake(&mut n, &peer(&b), &held)["ok"], false, "a token read from the other terminal's file spends nothing either");
    let uses = |n: &mut Node| ok(req(n, "grant_log", "demo", &held, json!({"limit": 200})))["log"].as_array().unwrap().iter().filter(|e| e["op"] == "use").count();
    assert_eq!(uses(&mut n), 2, "only the two spends of the terminal that runs it, and the refused calls spent nothing");

    done(vec![a]);
    assert_eq!(brake(&mut n, &peer(&b), &mine)["ok"], true, "when the terminal that ran it ends, the one that is left spends the session's grant with the token it has");
    done(vec![b]);
}

#[test]
fn a_stale_token_is_answered_as_unknown_and_registering_again_from_where_it_runs_spends() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (a, b) = (process(true), process(true));
    let (name, stale) = walker(&mut n, &peer(&a));
    done(vec![a]);

    let refused = brake(&mut n, &peer(&b), &stale);
    assert_eq!(code(&refused), "denied", "{refused}");
    let said = calm(&refused);
    assert!(said.starts_with("the token is not known"), "its client reads the words that make it register again: {said}");
    assert!(refused["error"].get("grant").is_none(), "{refused}");

    let again = ok(register(&mut n, &peer(&b), "walker", ""));
    assert_eq!(again["name"], name.as_str(), "the same session, from the terminal it runs in now");
    assert_eq!(registered_from(&n, &name), vec![b.id()]);
    assert_eq!(brake(&mut n, &peer(&b), again["token"].as_str().unwrap())["ok"], true, "the grants followed the session");
    assert_eq!(code(&brake(&mut n, &peer(&b), &stale)), "denied", "the stale token ended with the lineage it came from");
    done(vec![b]);
}

#[test]
fn a_token_with_no_lineage_recorded_is_replaced_by_the_next_registration_and_spends_again() {
    let dir = tempdir().unwrap();
    let (name, token) = {
        let mut n = node(dir.path());
        lead(&mut n)
    };
    strip_lineage(dir.path());
    let mut n = node(dir.path());
    let refused = brake(&mut n, &me(), &token);
    assert_eq!(code(&refused), "denied", "{refused}");
    assert!(calm(&refused).starts_with("the token is not known"), "{refused}");
    assert_eq!(req(&mut n, "whoami", "demo", &token, json!({}))["ok"], true, "it still reads");

    let again = ok(register(&mut n, &me(), "lead", ""));
    assert_eq!(again["name"], name.as_str());
    assert_eq!(brake(&mut n, &me(), again["token"].as_str().unwrap())["ok"], true, "a session with no lineage recorded is adopted by the process that registers it");
    assert_eq!(code(&req(&mut n, "whoami", "demo", &token, json!({}))), "denied", "and the token that named no lineage ended");
}

#[test]
fn an_orphan_that_registers_first_adopts_the_session_and_the_lead_that_registers_next_is_a_live_sibling() {
    let dir = tempdir().unwrap();
    let (name, token) = {
        let mut n = node(dir.path());
        lead(&mut n)
    };
    strip_lineage(dir.path());
    let mut n = node(dir.path());
    let outside = process(true);

    let adopted = ok(register(&mut n, &peer(&outside), "lead", ""));
    assert_eq!(adopted["name"], name.as_str(), "an orphan that registers first adopts the session, as anything that registers a session with no lineage does");
    assert_eq!(registered_from(&n, &name), vec![outside.id()]);
    assert_eq!(code(&req(&mut n, "whoami", "demo", &token, json!({}))), "denied", "the token that named no lineage ended");

    let lead_again = ok(register(&mut n, &me(), "lead", ""));
    let fresh = lead_again["token"].as_str().unwrap().to_string();
    assert_eq!(lead_again["name"], name.as_str(), "the lead registers: nobody is sent anywhere");
    let refused = brake(&mut n, &me(), &fresh);
    assert_eq!(code(&refused), "denied", "while the orphan runs, the lead is its live sibling and spends nothing of the session: {refused}");
    calm(&refused);
    assert_eq!(brake(&mut n, &peer(&outside), adopted["token"].as_str().unwrap())["ok"], true, "the grants it held are the orphan's now");
    done(vec![outside]);
    assert_eq!(brake(&mut n, &me(), &fresh)["ok"], true, "and the lead's again when the orphan ends");
}
