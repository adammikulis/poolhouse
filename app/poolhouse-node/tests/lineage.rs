//! Which process a registration came from: a main session registers again only from the lineage it was registered from, a
//! refusal gives out nothing and ends nothing, a session holds a few tokens and keeps its first, and a person's key moves a
//! session to the terminal the person is at.
#![cfg(unix)]

mod kit;

use std::os::unix::process::CommandExt;
use std::process::{Child, Command, Stdio};

use kit::grants::{enrolled, person};
use kit::*;
use poolhouse_node::lineage::Peer;
use poolhouse_node::node::Node;
use serde_json::{json, Value};
use tempfile::tempdir;

/// A process that does nothing for a while: in the session of this test, or (``own_terminal``) leading a session of its own
/// with a controlling terminal, as a shell in another terminal does.
fn process(own_terminal: bool) -> Child {
    let mut slave = 0;
    if own_terminal {
        let mut master = 0;
        // SAFETY: openpty fills two descriptors; the master stays open for the life of the test so the terminal does not hang up.
        assert_eq!(unsafe { libc::openpty(&mut master, &mut slave, std::ptr::null_mut(), std::ptr::null_mut(), std::ptr::null_mut()) }, 0);
    }
    let mut command = Command::new("sleep");
    command.arg("60").stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    if own_terminal {
        // SAFETY: setsid and ioctl are async-signal-safe and run in the child before exec.
        unsafe {
            command.pre_exec(move || {
                libc::setsid();
                libc::ioctl(slave, libc::TIOCSCTTY as _, 0);
                Ok(())
            });
        }
    }
    let child = command.spawn().unwrap();
    if own_terminal {
        // SAFETY: the descriptor came from openpty above and is closed once.
        unsafe { libc::close(slave) };
    }
    child
}

/// A process in a session of its own with no terminal, as a harness starts each command it runs.
fn detached() -> Child {
    let mut command = Command::new("sleep");
    command.arg("60").stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    // SAFETY: setsid is async-signal-safe and runs in the child before exec.
    unsafe { command.pre_exec(|| { libc::setsid(); Ok(()) }) };
    command.spawn().unwrap()
}

/// A process whose parent has gone, as a daemon's is, and its id.
fn orphan() -> u32 {
    let out = Command::new("sh").args(["-c", "sleep 60 >/dev/null 2>&1 & echo $!"]).output().unwrap();
    String::from_utf8_lossy(&out.stdout).trim().parse().unwrap()
}

fn peer(child: &Child) -> Peer {
    Peer { pid: Some(child.id()) }
}

fn done(mut children: Vec<Child>) {
    for child in &mut children {
        child.kill().unwrap();
        child.wait().unwrap();
    }
}

fn register(n: &mut Node, from: &Peer, session: &str, parent: &str) -> Value {
    req_from(n, from, "register", "demo", parent, json!({"model": "claude-sonnet-5-5", "harness": "claude-code", "session": session}))
}

fn who(n: &mut Node, token: &str) -> Value {
    req(n, "whoami", "demo", token, json!({}))
}

#[test]
fn a_main_session_registers_again_only_from_the_lineage_it_came_from() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (inside, outside, harness) = (process(false), process(true), detached());
    let me = Peer { pid: Some(std::process::id()) };
    let first = ok(register(&mut n, &me, "lead", ""));
    assert_eq!(first["created"], true);
    let from_a_command = ok(register(&mut n, &peer(&harness), "lead", ""));
    assert_eq!(from_a_command["name"], first["name"], "a command the harness runs in a session of its own, with no terminal, is still the terminal's");
    let daemon = orphan();
    assert_eq!(code(&register(&mut n, &Peer { pid: Some(daemon) }, "lead", "")), "denied", "a process whose parent is gone has no lineage to share");
    // SAFETY: ends the sleeping process this test started.
    unsafe { libc::kill(daemon as i32, libc::SIGKILL) };

    let again = ok(register(&mut n, &peer(&inside), "lead", ""));
    assert_eq!((again["name"].clone(), again["created"].clone()), (first["name"].clone(), json!(false)), "a process started by the registrant's own terminal is the same lineage");

    let refused = register(&mut n, &peer(&outside), "lead", "");
    assert_eq!(code(&refused), "denied");
    let message = refused["error"]["message"].as_str().unwrap();
    assert!(message.contains("another process lineage") && message.contains("rebind"), "{message}");
    assert!(!message.contains(first["token"].as_str().unwrap()) && !message.contains(again["token"].as_str().unwrap()), "a refusal names no token");
    assert_eq!(code(&register(&mut n, &Peer::none(), "lead", "")), "denied", "a request whose process cannot be read is not the registrant's");

    assert_eq!(ok(who(&mut n, first["token"].as_str().unwrap()))["name"], first["name"], "and the refusals ended nothing the lead held");
    let other = ok(register(&mut n, &peer(&outside), "someone-else", ""));
    assert_eq!(other["created"], true, "a new session registers from anywhere, and holds no grant");
    done(vec![inside, outside, harness]);
}

#[test]
fn a_refused_registration_ends_no_token_and_past_eight_the_oldest_but_the_first_goes() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let outside = process(true);
    let me = Peer { pid: Some(std::process::id()) };
    let first = ok(register(&mut n, &me, "lead", ""));
    let original = first["token"].as_str().unwrap().to_string();
    for _ in 0..20 {
        assert_eq!(code(&register(&mut n, &peer(&outside), "lead", "")), "denied");
    }
    assert_eq!(ok(who(&mut n, &original))["name"], first["name"], "twenty refusals ended nothing the lead held");
    let mut tokens = vec![original.clone()];
    for _ in 0..7 {
        tokens.push(ok(register(&mut n, &me, "lead", ""))["token"].as_str().unwrap().to_string());
    }
    for token in &tokens {
        assert_eq!(ok(who(&mut n, token))["name"], first["name"], "a session holds eight tokens");
    }
    let ninth = ok(register(&mut n, &me, "lead", ""))["token"].as_str().unwrap().to_string();
    assert_eq!(ok(who(&mut n, &original))["name"], first["name"], "the first token is never the one that goes");
    assert_eq!(code(&who(&mut n, &tokens[1])), "denied", "the oldest of the others went to make room");
    for token in tokens.iter().skip(2).chain([&ninth]) {
        assert_eq!(ok(who(&mut n, token))["name"], first["name"]);
    }
    done(vec![outside]);
}

#[test]
fn a_person_moves_a_session_to_the_terminal_they_are_at_and_the_move_is_used_once() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (here, there) = (Peer { pid: Some(std::process::id()) }, process(true));
    let first = ok(register(&mut n, &here, "lead", ""));
    let (name, token) = (first["name"].as_str().unwrap().to_string(), first["token"].as_str().unwrap().to_string());
    enrolled(dir.path(), &mut |m, b, t, p| req(&mut n, m, b, t, p), &token);
    let device = n.cert.fingerprint();
    let params = person().rebind_params("claude-code", "lead", "demo", &name, &device);

    let stranger = kit::grants::Person { key: ed25519_dalek::SigningKey::from_bytes(&[0x44; 32]) };
    let forged = stranger.rebind_params("claude-code", "lead", "demo", &name, &device);
    assert_eq!(code(&req_from(&mut n, &peer(&there), "rebind", "demo", "", forged)), "denied", "a key nobody enrolled moves nothing");
    let elsewhere = person().rebind_params("claude-code", "lead", "demo", &name, &"ab".repeat(32));
    assert_eq!(code(&req_from(&mut n, &peer(&there), "rebind", "demo", "", elsewhere)), "denied", "an approval for another node moves nothing here");
    ok(register(&mut n, &here, "someone-else", ""));
    let other_session = person().rebind_params("claude-code", "someone-else", "demo", &name, &device);
    assert_eq!(code(&req_from(&mut n, &peer(&there), "rebind", "demo", "", other_session)), "denied", "an approval for one session moves no other");
    assert_eq!(code(&req_from(&mut n, &Peer::none(), "rebind", "demo", "", params.clone())), "denied", "no process behind the request, nowhere to move the session");
    assert_eq!(ok(who(&mut n, &token))["name"], name.as_str(), "none of that ended the lead's token");

    let moved = ok(req_from(&mut n, &peer(&there), "rebind", "demo", "", params.clone()));
    assert_eq!((moved["name"].clone(), moved["moved"].clone()), (json!(name), json!(true)));
    assert!(moved["tokens_ended"].as_u64().unwrap() >= 1);
    assert_eq!(code(&who(&mut n, &token)), "denied", "the tokens it held ended");
    assert_eq!(code(&req_from(&mut n, &peer(&there), "rebind", "demo", "", params)), "denied", "an approval is used once");

    assert_eq!(code(&register(&mut n, &here, "lead", "")), "denied", "the old lineage no longer registers it");
    let fresh = ok(register(&mut n, &peer(&there), "lead", ""));
    assert_eq!(fresh["name"], name.as_str());
    assert_eq!(ok(who(&mut n, fresh["token"].as_str().unwrap()))["name"], name.as_str());
    let audit = ok(req(&mut n, "read", "demo", fresh["token"].as_str().unwrap(), json!({"kind": "audit"})));
    assert!(audit["entries"].as_array().unwrap().iter().any(|e| e["fields"]["event"] == "rebind" && e["fields"]["subject"] == name.as_str()), "{audit}");
    done(vec![there]);
}

#[test]
fn a_subagent_registers_under_its_parent_and_a_retired_or_moved_name_is_not_a_subagent() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (here, apart) = (Peer { pid: Some(std::process::id()) }, process(true));
    let lead = ok(register(&mut n, &here, "lead", ""));
    let sub = ok(register(&mut n, &peer(&apart), "sub", lead["token"].as_str().unwrap()));
    assert_eq!(sub["parent"], lead["name"], "a subagent is registered by its parent's token, from any process");
    let again = ok(register(&mut n, &here, "sub", lead["token"].as_str().unwrap()));
    assert_eq!(again["name"], sub["name"], "and again from another, since the parent's token is what a subagent registers with");
    assert_eq!(code(&register(&mut n, &peer(&apart), "sub", "")), "denied", "a subagent is not registered as a main session");
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
    let (here, there) = (Peer { pid: Some(std::process::id()) }, process(true));
    let first = ok(register(&mut n, &here, "lead", ""));
    let (name, token) = (first["name"].as_str().unwrap().to_string(), first["token"].as_str().unwrap().to_string());
    enrolled(dir.path(), &mut |m, b, t, p| req(&mut n, m, b, t, p), &token);
    let device = n.cert.fingerprint();
    let good = person().rebind_params("claude-code", "lead", "demo", &name, &device);
    let big = "x".repeat(200_000);
    let hostile = [("harness", json!(5)), ("harness", json!(big)), ("harness", json!("claude-code\nx")), ("session", json!(["lead"])), ("session", json!("../lead")),
                   ("at_ms", json!(-1)), ("at_ms", json!("now")), ("at_ms", json!(1.5)), ("at_ms", Value::Null), ("nonce", json!("short")),
                   ("nonce", json!("../../etc/passwd/aaaaaaaa")), ("nonce", json!(big)), ("nonce", json!(7)), ("by", json!("../persons")),
                   ("by", json!(big)), ("by", json!(5)), ("sig", json!("zz")), ("sig", json!("00".repeat(64))), ("sig", json!(["00"])),
                   ("sig", json!(big)), ("admin", json!(true)), ("as", json!("person")), ("name", json!(name)), ("token", json!(token))];
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
    assert_eq!(code(&register(&mut n, &peer(&there), "lead", "")), "denied", "and nothing was moved");
    ok(register(&mut n, &here, "lead", ""));
    done(vec![there]);
}

#[test]
fn a_session_registered_with_no_lineage_takes_the_first_one_that_registers_it_again() {
    let dir = tempdir().unwrap();
    let (inside, outside) = (process(false), process(true));
    {
        let mut n = node(dir.path());
        ok(register(&mut n, &Peer::none(), "old", ""));
    }
    let mut n = node(dir.path());
    let adopted = ok(register(&mut n, &peer(&outside), "old", ""));
    assert_eq!(adopted["created"], false, "a name from before lineages were kept registers for whoever asks first");
    assert_eq!(code(&register(&mut n, &peer(&inside), "old", "")), "denied", "and from then on only from there");
    assert_eq!(ok(register(&mut n, &peer(&outside), "old", ""))["name"], adopted["name"]);
    drop(n);
    let mut n = node(dir.path());
    assert_eq!(code(&register(&mut n, &peer(&inside), "old", "")), "denied", "the lineage outlasts a restart of the node");
    done(vec![inside, outside]);
}
