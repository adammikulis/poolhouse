//! Which process a registration came from: a main session registers from any process, always. One whose recorded lineage no longer runs
//! moves to the process that registers it (new incarnation, the tokens of the ended lineage end); a live sibling gets a token of its own
//! lineage and the session stays where it runs; a request whose process cannot be read registers and moves nothing. A session holds a few
//! tokens and keeps its first, and `rebind` takes a session over at once, from the terminal it is run in, with nothing signed or tapped.
#![cfg(unix)]

mod kit;

use kit::procs::*;
use kit::*;
use poolhouse_node::lineage::Peer;
use poolhouse_node::node::Node;
use serde_json::{json, Value};
use tempfile::tempdir;

fn register(n: &mut Node, from: &Peer, session: &str, parent: &str) -> Value {
    req_from(n, from, "register", "demo", parent, json!({"model": "claude-sonnet-5-5", "harness": "claude-code", "session": session}))
}

fn incarnation(n: &Node, name: &str) -> String {
    n.boards["demo"].names.get(name).unwrap().incarnation.clone()
}

/// The pids of the processes of the lineage the session is registered from, the registrant first.
fn registered_from(n: &Node, name: &str) -> Vec<u32> {
    n.boards["demo"].names.get(name).unwrap().lineage.iter().map(|a| a.pid).collect()
}

fn who(n: &mut Node, token: &str) -> Value {
    req(n, "whoami", "demo", token, json!({}))
}

fn text(reply: &Value) -> String {
    reply["error"]["message"].as_str().unwrap().trim_start_matches("denied: ").to_string()
}

fn rebind(n: &mut Node, from: &Peer, session: &str) -> Value {
    req_from(n, from, "rebind", "demo", "", json!({"harness": "claude-code", "session": session}))
}

#[test]
fn a_main_session_registers_from_any_process_and_a_live_sibling_leaves_it_where_it_runs() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (inside, outside, harness) = (process(false), process(true), detached());
    let me = me();
    let first = ok(register(&mut n, &me, "lead", ""));
    let name = first["name"].as_str().unwrap().to_string();
    assert_eq!(first["created"], true);
    let from_a_command = ok(register(&mut n, &peer(&harness), "lead", ""));
    assert_eq!(from_a_command["name"], first["name"], "a command the harness runs in a session of its own, with no terminal, is still the terminal's");
    let again = ok(register(&mut n, &peer(&inside), "lead", ""));
    assert_eq!((again["name"].clone(), again["created"].clone()), (first["name"].clone(), json!(false)), "a process started by the registrant's own terminal is the same lineage");
    let at = incarnation(&n, &name);
    let home = registered_from(&n, &name);

    let daemon = orphan();
    let from_a_daemon = ok(register(&mut n, &Peer { pid: Some(daemon) }, "lead", ""));
    let from_another_terminal = ok(register(&mut n, &peer(&outside), "lead", ""));
    let unreadable = ok(register(&mut n, &Peer::none(), "lead", ""));
    // SAFETY: ends the sleeping process this test started.
    unsafe { libc::kill(daemon as i32, libc::SIGKILL) };

    assert_eq!(incarnation(&n, &name), at, "a live sibling moves nothing");
    assert_eq!(registered_from(&n, &name), home, "the session stays registered where it runs");
    for registration in [&first, &from_a_command, &again, &from_a_daemon, &from_another_terminal, &unreadable] {
        assert_eq!(registration["name"], first["name"], "a daemon, another terminal and a process that cannot be read all register the one session");
        assert_eq!(ok(who(&mut n, registration["token"].as_str().unwrap()))["name"], first["name"], "and each got a token that names it");
    }
    let other = ok(register(&mut n, &peer(&outside), "someone-else", ""));
    assert_eq!(other["created"], true, "a new session registers from anywhere, and holds no grant");
    done(vec![inside, outside, harness]);
}

#[test]
fn a_main_session_whose_lineage_has_ended_moves_to_the_process_that_registers_it_and_the_old_tokens_end() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (first_terminal, second_terminal, third_terminal) = (process(true), process(true), process(true));
    let first = ok(register(&mut n, &peer(&first_terminal), "walker", ""));
    let (name, old) = (first["name"].as_str().unwrap().to_string(), first["token"].as_str().unwrap().to_string());
    let before = incarnation(&n, &name);
    assert_eq!(ok(who(&mut n, &old))["name"], name.as_str());
    done(vec![first_terminal]);

    let moved = ok(register(&mut n, &peer(&second_terminal), "walker", ""));
    assert_eq!((moved["name"].as_str().unwrap(), moved["created"].clone()), (name.as_str(), json!(false)), "the session resumed in another terminal is the session it was");
    assert_ne!(incarnation(&n, &name), before, "it moved: a new incarnation, which ends what was approved for the old one");
    assert_eq!(registered_from(&n, &name), vec![second_terminal.id()], "it is registered from the terminal that runs it now");
    let stale = who(&mut n, &old);
    assert_eq!(code(&stale), "denied", "the token of the lineage that ended ended with it");
    assert!(text(&stale).starts_with("the token is not known"), "and its client is told in the words that make it register again: {}", text(&stale));
    let now = moved["token"].as_str().unwrap();
    assert_eq!(ok(who(&mut n, now))["name"], name.as_str());

    let kept = incarnation(&n, &name);
    let sibling = ok(register(&mut n, &peer(&third_terminal), "walker", ""));
    assert_eq!(incarnation(&n, &name), kept, "a third terminal while the second runs is a live sibling: nothing moved");
    assert_eq!(ok(who(&mut n, now))["name"], name.as_str(), "and nothing ended");
    assert_eq!(ok(who(&mut n, sibling["token"].as_str().unwrap()))["name"], name.as_str());
    done(vec![second_terminal, third_terminal]);
}

#[test]
fn a_request_from_a_process_that_cannot_be_read_registers_and_moves_nothing() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let gone = process(true);
    let first = ok(register(&mut n, &peer(&gone), "lead", ""));
    let name = first["name"].as_str().unwrap().to_string();
    done(vec![gone]);
    let at = (incarnation(&n, &name), registered_from(&n, &name));
    let unread = ok(register(&mut n, &Peer::none(), "lead", ""));
    assert_eq!((incarnation(&n, &name), registered_from(&n, &name)), at, "a process nobody can name is no lineage to move the session to");
    assert_eq!(ok(who(&mut n, unread["token"].as_str().unwrap()))["name"], name.as_str(), "it reads and writes, and spends no grant");
    let readable = ok(register(&mut n, &me(), "lead", ""));
    assert_eq!(readable["name"], name.as_str());
    assert_eq!(registered_from(&n, &name).first(), Some(&std::process::id()), "the next process that can be read takes the session");
}

#[test]
fn a_registration_ends_no_token_of_a_lineage_that_runs_and_past_eight_the_oldest_but_the_first_goes() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let outside = process(true);
    let me = me();
    let first = ok(register(&mut n, &me, "lead", ""));
    let original = first["token"].as_str().unwrap().to_string();
    let sibling = ok(register(&mut n, &peer(&outside), "lead", ""));
    assert_eq!(ok(who(&mut n, &original))["name"], first["name"], "a live sibling ended nothing the lead held");
    assert_eq!(ok(who(&mut n, sibling["token"].as_str().unwrap()))["name"], first["name"]);
    let mut tokens = vec![original.clone()];
    for _ in 0..6 {
        tokens.push(ok(register(&mut n, &me, "lead", ""))["token"].as_str().unwrap().to_string());
    }
    for token in tokens.iter().map(String::as_str).chain([sibling["token"].as_str().unwrap()]) {
        assert_eq!(ok(who(&mut n, token))["name"], first["name"], "a session holds eight tokens");
    }
    let ninth = ok(register(&mut n, &me, "lead", ""))["token"].as_str().unwrap().to_string();
    assert_eq!(ok(who(&mut n, &original))["name"], first["name"], "the first token is never the one that goes");
    let (kept_sibling, oldest) = (sibling["token"].as_str().unwrap(), who(&mut n, sibling["token"].as_str().unwrap()));
    assert_eq!(code(&oldest), "denied", "the oldest of the others went to make room, whoever it was registered for: {kept_sibling}");
    assert!(text(&oldest).starts_with("the token is not known"), "and its client registers again: {oldest}");
    for token in tokens.iter().skip(1).chain([&ninth]) {
        assert_eq!(ok(who(&mut n, token))["name"], first["name"]);
    }
    done(vec![outside]);
}

#[test]
fn rebind_takes_a_session_over_from_the_terminal_it_is_run_in_with_nothing_signed_or_tapped() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (here, there) = (me(), process(true));
    let first = ok(register(&mut n, &here, "lead", ""));
    let (name, token) = (first["name"].as_str().unwrap().to_string(), first["token"].as_str().unwrap().to_string());
    ok(register(&mut n, &here, "someone-else", ""));

    assert_eq!(code(&rebind(&mut n, &peer(&there), "nobody")), "denied", "a session nobody registered is moved nowhere");
    assert_eq!(code(&rebind(&mut n, &Peer::none(), "lead")), "denied", "no process behind the request, nowhere to move the session");
    assert_eq!(ok(who(&mut n, &token))["name"], name.as_str(), "none of that ended the lead's token");

    let moved = ok(rebind(&mut n, &peer(&there), "lead"));
    assert_eq!((moved["name"].clone(), moved["moved"].clone()), (json!(name), json!(true)));
    assert!(moved["tokens_ended"].as_u64().unwrap() >= 1);
    assert_eq!(code(&who(&mut n, &token)), "denied", "the tokens it held ended");
    assert_eq!(registered_from(&n, &name), vec![there.id()], "it is registered from the terminal that ran the command");
    let sibling = ok(register(&mut n, &here, "lead", ""));
    assert_eq!(sibling["name"], name.as_str(), "the old lineage registers it again, as a live sibling");
    assert_eq!(registered_from(&n, &name), vec![there.id()], "and the session stays with the terminal that took it");
    let audit = ok(req(&mut n, "read", "demo", sibling["token"].as_str().unwrap(), json!({"kind": "audit"})));
    assert!(audit["entries"].as_array().unwrap().iter().any(|e| e["fields"]["event"] == "rebind" && e["fields"]["subject"] == name.as_str()), "the move is on the board: {audit}");
    done(vec![there]);
}

#[test]
fn a_move_survives_a_restart_and_a_retired_session_is_not_moved_but_registers() {
    let dir = tempdir().unwrap();
    let there = process(true);
    let (name, token) = {
        let mut n = node(dir.path());
        let first = ok(register(&mut n, &me(), "lead", ""));
        let (name, token) = (first["name"].as_str().unwrap().to_string(), first["token"].as_str().unwrap().to_string());
        ok(rebind(&mut n, &peer(&there), "lead"));
        (name, token)
    };
    let mut n = node(dir.path());
    assert_eq!(registered_from(&n, &name), vec![there.id()], "the move is kept");
    assert_eq!(code(&who(&mut n, &token)), "denied", "so is the end of the tokens it held");
    let (_, other) = session(&mut n, "demo", "other");
    ok(req(&mut n, "retire", "demo", &other, json!({})));
    let retired = rebind(&mut n, &peer(&there), "other");
    assert_eq!(code(&retired), "denied");
    assert!(text(&retired).contains("comes back when its harness registers it"), "{retired}");
    assert_eq!(ok(register(&mut n, &me(), "other", ""))["created"], false, "a retired session registers instead, under its name");
    done(vec![there]);
}

#[test]
fn the_node_tells_a_process_its_own_lineage_and_names_no_other() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let there = process(true);
    let mine = ok(req(&mut n, "lineage", "", "", json!({})));
    assert_eq!(mine["lineage"].as_array().unwrap().last().unwrap(), &kit::grants::my_anchor(), "the top of a lineage is the anchor a person signs for");
    let theirs = ok(req_from(&mut n, &peer(&there), "lineage", "", "", json!({})));
    assert_eq!(theirs["lineage"], json!([kit::grants::anchor_of(there.id())]), "a terminal's own shell is the whole of its lineage");
    assert_eq!(code(&req(&mut n, "lineage", "", "", json!({"pid": 1}))), "invalid", "no request names the process it asks about");
    assert_eq!(ok(req_from(&mut n, &Peer::none(), "lineage", "", "", json!({})))["lineage"], json!([]), "a request whose process cannot be read has none");
    done(vec![there]);
}

#[test]
fn a_subagent_registers_under_its_parent_and_a_name_that_is_a_main_session_is_not_a_subagent() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (here, apart) = (me(), process(true));
    let lead = ok(register(&mut n, &here, "lead", ""));
    let sub = ok(register(&mut n, &peer(&apart), "sub", lead["token"].as_str().unwrap()));
    assert_eq!(sub["parent"], lead["name"], "a subagent is registered by its parent's token, from any process");
    let again = ok(register(&mut n, &here, "sub", lead["token"].as_str().unwrap()));
    assert_eq!(again["name"], sub["name"], "and again from another, since the parent's token is what a subagent registers with");
    assert_eq!(code(&register(&mut n, &peer(&apart), "sub", "")), "denied", "a subagent is not registered as a main session");
    assert_eq!(code(&register(&mut n, &here, "lead", sub["token"].as_str().unwrap())), "denied", "and a main session is not registered as a subagent");
    done(vec![apart]);
}

#[test]
fn the_peer_of_a_connection_is_the_process_that_connected() {
    let dir = short_dir();
    let listener = poolhouse_node::sys::Listener::bind(dir.path()).unwrap();
    let client = poolhouse_node::sys::connect(dir.path()).unwrap();
    let server = loop {
        match listener.accept() {
            Ok(stream) => break stream,
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => std::thread::sleep(std::time::Duration::from_millis(5)),
            Err(e) => panic!("{e}"),
        }
    };
    assert_eq!(Peer::of(&server).pid, Some(std::process::id()));
    assert!(!Peer::of(&server).lineage().is_empty());
    drop(client);
}

#[test]
fn hostile_params_to_a_move_change_nothing() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (here, there) = (me(), process(true));
    let first = ok(register(&mut n, &here, "lead", ""));
    let (name, token) = (first["name"].as_str().unwrap().to_string(), first["token"].as_str().unwrap().to_string());
    let good = json!({"harness": "claude-code", "session": "lead"});
    let big = "x".repeat(200_000);
    let hostile = [("harness", json!(5)), ("harness", json!(big)), ("harness", json!("claude-code\nx")), ("session", json!(["lead"])), ("session", json!("../lead")),
                   ("session", json!(big)), ("admin", json!(true)), ("as", json!("person")), ("name", json!(name)), ("token", json!(token)),
                   ("anchor", json!({"pid": 1, "start": 1})), ("sig", json!("00")), ("by", json!("someone")), ("approval", json!("ap-1"))];
    for (key, value) in hostile {
        let mut p = good.clone();
        p[key] = value;
        let reply = req_from(&mut n, &peer(&there), "rebind", "demo", "", p);
        assert_eq!(reply["ok"], false, "{key}: {reply}");
    }
    for board in ["", "nowhere", "../demo", "DEMO"] {
        let reply = req_from(&mut n, &peer(&there), "rebind", board, "", good.clone());
        assert_eq!(reply["ok"], false, "board {board:?}: {reply}");
    }
    assert_eq!(code(&req_from(&mut n, &peer(&there), "rebind", "demo", "", json!(["not", "an", "object"]))), "invalid");
    assert_eq!(ok(who(&mut n, &token))["name"], name.as_str(), "nothing was ended");
    assert_eq!(registered_from(&n, &name).first(), Some(&std::process::id()), "and nothing was moved");
    done(vec![there]);
}
