//! A token is a bearer secret in a file its user can read, so what it spends is bound to the lineage that registered it: a
//! token presented from another terminal still reads and sends, and spends no grant. A name with no lineage recorded is
//! adopted only by a process that presents a token of that name, and an orphan that registers first adopts nothing.
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
    req_from(n, from, "land_brake", "demo", token, json!({"pause": false}))
}

fn verify(n: &mut Node, from: &Peer, token: &str, note: &str) -> Value {
    req_from(n, from, "note_verify", "demo", token, json!({"note": note, "exit": 0, "out_sha": SHA}))
}

/// A main session registered from this test's process that holds `land_control`.
fn lead(n: &mut Node) -> (String, String) {
    let first = ok(register(n, &me(), "lead", ""));
    let (name, token) = (first["name"].as_str().unwrap().to_string(), first["token"].as_str().unwrap().to_string());
    allow(n, "demo", &token, &name, &["land_control"]);
    (name, token)
}

fn message(reply: &Value) -> String {
    reply["error"]["message"].as_str().unwrap().to_string()
}

#[test]
fn a_token_read_from_its_file_spends_no_grant_from_another_terminal_and_still_reads_and_sends() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, token) = lead(&mut n);
    let (inside, outside, harness) = (process(false), process(true), detached());
    assert_eq!(brake(&mut n, &me(), &token)["ok"], true, "the lineage that registered it spends it");
    assert_eq!(brake(&mut n, &peer(&inside), &token)["ok"], true, "a process of the same terminal spends it");
    assert_eq!(brake(&mut n, &peer(&harness), &token)["ok"], true, "so does a command a harness starts in a session of its own");

    let refused = brake(&mut n, &peer(&outside), &token);
    assert_eq!(code(&refused), "denied", "{refused}");
    let said = message(&refused);
    assert!(said.contains("rebind") && said.contains("terminal"), "the refusal tells the person to rebind: {said}");
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
    assert!(message(&refused).contains("rebind"), "{refused}");
    assert_eq!(verify(&mut n, &me(), &stolen, &note)["ok"], true, "the lineage it carries is its parent's");
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
fn a_token_with_no_lineage_recorded_spends_nothing_until_a_person_rebinds_it() {
    let dir = tempdir().unwrap();
    let token = {
        let mut n = node(dir.path());
        lead(&mut n).1
    };
    strip_lineage(dir.path());
    let mut n = node(dir.path());
    let refused = brake(&mut n, &me(), &token);
    assert_eq!(code(&refused), "denied", "{refused}");
    assert!(message(&refused).contains("rebind"), "{refused}");
    assert_eq!(req(&mut n, "whoami", "demo", &token, json!({}))["ok"], true, "it still reads");
}

#[test]
fn an_orphan_that_registers_first_adopts_no_name_and_the_lead_who_holds_its_token_is_not_locked_out() {
    let dir = tempdir().unwrap();
    let (name, token) = {
        let mut n = node(dir.path());
        lead(&mut n)
    };
    strip_lineage(dir.path());
    let mut n = node(dir.path());
    let (outside, daemon) = (process(true), orphan());

    let adopted = register(&mut n, &peer(&outside), "lead", "");
    assert_eq!(code(&adopted), "denied", "an orphan with no token of that name adopts nothing: {adopted}");
    assert!(message(&adopted).contains("rebind"), "{adopted}");
    assert_eq!(code(&register(&mut n, &Peer { pid: Some(daemon) }, "lead", "")), "denied", "nor does a daemon");
    assert_eq!(code(&register(&mut n, &me(), "lead", "")), "denied", "and a process with no token of that name is not the lead either");

    let again = register(&mut n, &me(), "lead", &token);
    assert_eq!(ok(again.clone())["name"], name.as_str(), "the lead presents the token it holds and is bound where it runs: {again}");
    let fresh = ok(again)["token"].as_str().unwrap().to_string();
    assert_eq!(brake(&mut n, &me(), &fresh)["ok"], true, "the grants it held are its again");
    assert_eq!(code(&register(&mut n, &peer(&outside), "lead", "")), "denied", "the orphan still adopts nothing");
    assert_eq!(code(&register(&mut n, &peer(&outside), "lead", &fresh)), "denied", "and a token lifted from the file registers nothing from another terminal");
    unsafe { libc::kill(daemon as i32, libc::SIGKILL) };
    done(vec![outside]);
}
