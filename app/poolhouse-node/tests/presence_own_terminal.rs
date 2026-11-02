//! A computer whose own node has no prompt of the system's to ask with (a standard macOS account, Linux without polkit or a desktop session) asks the
//! person on a terminal and counts what that makes as a typed yes. A program that can open a pseudo-terminal of its own can also type into it, so
//! the question is the person's own session's alone, and is put on the controlling terminal of the process that asks, which the node finds itself:
//! the asker names none.
#![cfg(unix)]

mod kit;

use std::os::unix::process::CommandExt;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use kit::screen::{screen, Screen};
use kit::*;
use poolhouse_node::lineage::Peer;
use poolhouse_node::node::Node;
use poolhouse_node::presence::{Firsts, Mode};
use serde_json::{json, Value};
use tempfile::tempdir;

/// A node on a computer with no prompt of its own.
fn fallback_node(dir: &std::path::Path) -> Node {
    let mut n = Node::open(dir).unwrap();
    n.firsts = Firsts::new(Mode::Os.presence_where(false, dir));
    assert_eq!(n.firsts.kind(), "terminal");
    n
}

/// A process with ``screen`` as its controlling terminal (a terminal's shell), or with none (as a harness starts a command).
fn sleeper(on: Option<&Screen>) -> Child {
    let slave = on.map_or(0, Screen::device_fd);
    let mut command = Command::new("sleep");
    command.arg("30").stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    // SAFETY: setsid and ioctl are async-signal-safe and run in the child before exec.
    unsafe {
        command.pre_exec(move || {
            libc::setsid();
            if slave != 0 {
                libc::ioctl(slave, libc::TIOCSCTTY as _, 0);
            }
            Ok(())
        });
    }
    command.spawn().unwrap()
}

fn end(mut child: Child) {
    child.kill().unwrap();
    child.wait().unwrap();
}

fn ask_as(n: &mut Node, pid: Option<u32>, token: &str, named: &str) -> Value {
    req_from(n, &Peer { pid }, "presence_ask", "", token, json!({"purpose": "key", "public": kit::grants::person().public(), "terminal": named}))
}

#[test]
fn a_session_an_agent_started_cannot_open_a_terminal_of_its_own_and_answer_for_the_person() {
    let dir = tempdir().unwrap();
    let mut n = fallback_node(dir.path());
    let (_, agent) = session(&mut n, "demo", "an-agent");
    let mut mine = screen();
    for purpose in ["key", "phone"] {
        let asked = req(&mut n, "presence_ask", "", &agent, json!({"purpose": purpose, "public": kit::grants::person().public(), "terminal": mine.name}));
        assert_eq!(code(&asked), "denied", "{purpose}: {asked}");
    }
    mine.types("yes\n");
    let said = ok(req(&mut n, "presence_status", "", &agent, json!({})));
    assert_eq!((said["state"].as_str(), said["secret"].is_null()), (Some("none"), true), "no question was put, so the yes typed at the agent's own terminal is no answer: {said}");
    let mut params = kit::grants::person().enrolment("an agent's key", None);
    params["secret"] = json!("guess");
    assert_eq!(code(&req(&mut n, "person_enroll", "", &agent, params)), "denied");
    assert!(held(&mut n).is_empty(), "no key was enrolled");
}

#[test]
fn the_question_is_put_on_the_controlling_terminal_of_the_process_that_asks_and_on_no_other() {
    let dir = tempdir().unwrap();
    let mut n = fallback_node(dir.path());
    let me = person_token(&mut n, "demo", "the-person");
    let (mut here, mut elsewhere) = (screen(), screen());
    let shell = sleeper(Some(&here));
    let asked = ok(ask_as(&mut n, Some(shell.id()), &me, &elsewhere.name));
    assert_eq!(asked["state"], "asking", "{asked}");
    let shown = here.sees("Type yes");
    assert!(shown.contains("first person key"), "the node wrote the question on the asking process's own terminal, whatever terminal the request named: {shown}");
    elsewhere.types("yes\n");
    here.types("yes\n");
    let end_at = Instant::now() + Duration::from_secs(20);
    let mut said = ok(req(&mut n, "presence_status", "", &me, json!({})));
    while said["state"] == "asking" && Instant::now() < end_at {
        std::thread::sleep(Duration::from_millis(50));
        said = ok(req(&mut n, "presence_status", "", &me, json!({})));
    }
    assert_eq!(said["state"], "confirmed", "{said}");
    here.drain();
    end(shell);
}

#[test]
fn a_request_from_a_process_with_no_terminal_of_its_own_is_asked_nothing_whatever_terminal_it_names() {
    let dir = tempdir().unwrap();
    let mut n = fallback_node(dir.path());
    let me = person_token(&mut n, "demo", "the-person");
    let mut named = screen();
    let detached = sleeper(None);
    for pid in [Some(detached.id()), None] {
        let refused = ask_as(&mut n, pid, &me, &named.name);
        assert_eq!(code(&refused), "denied", "{pid:?}: {refused}");
        assert!(refused["error"]["message"].as_str().unwrap().contains("process that has one"), "{refused}");
    }
    named.types("yes\n");
    assert_eq!(ok(req(&mut n, "presence_status", "", &me, json!({})))["state"], "none");
    end(detached);
}

#[test]
fn an_agents_four_sessions_take_nothing_from_the_persons_one_prompt() {
    let dir = tempdir().unwrap();
    let mut n = fallback_node(dir.path());
    let impostors: Vec<String> = (0..6).map(|i| session(&mut n, "demo", &format!("agent-{i}")).1).collect();
    for token in &impostors {
        assert_eq!(code(&ask_as(&mut n, None, token, "")), "denied");
    }
    let me = person_token(&mut n, "demo", "the-person");
    let mut here = screen();
    let shell = sleeper(Some(&here));
    let asked = ask_as(&mut n, Some(shell.id()), &me, "");
    assert_eq!(asked["ok"], true, "the person is asked after any number of agent sessions asked: {asked}");
    here.sees("Type yes");
    // a terminal's last process waits for what was written on it to be read, so the question is dropped before the process ends
    drop(n);
    end(shell);
}
