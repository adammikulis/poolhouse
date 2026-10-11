//! The terminal as the one presence step: the node writes the question on the terminal device the asker names and reads the typed
//! line itself, so the asker cannot answer for the person, and only a typed `yes` makes the one-time secret.
#![cfg(unix)]

mod kit;

use std::time::{Duration, Instant};

use kit::screen::screen;
use kit::*;
use poolhouse_node::node::Node;
use poolhouse_node::presence::{Firsts, Mode};
use serde_json::{json, Value};
use tempfile::tempdir;

fn terminal_node(dir: &std::path::Path) -> Node {
    let mut n = Node::open(dir).unwrap();
    n.firsts = Firsts::new(Mode::Terminal.presence(dir));
    n
}

fn ask(n: &mut Node, token: &str, terminal: &str) -> Value {
    req(n, "presence_ask", "", token, json!({"purpose": "key", "terminal": terminal}))
}

fn answered(n: &mut Node, token: &str) -> Value {
    let end = Instant::now() + Duration::from_secs(20);
    loop {
        let said = ok(req(n, "presence_status", "", token, json!({})));
        if said["state"] != "asking" || Instant::now() > end {
            return said;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

#[test]
fn only_a_line_typed_yes_on_the_terminal_makes_the_secret_and_the_node_asked_there_itself() {
    let dir = tempdir().unwrap();
    let mut n = terminal_node(dir.path());
    let (_, token) = session(&mut n, "demo", "a");
    let mut me = screen();
    assert_eq!(ok(ask(&mut n, &token, &me.name))["state"], "asking");
    let shown = me.sees("Type yes");
    assert!(shown.contains("allow the session demo/") && shown.contains("first person key"), "the node wrote the question itself: {shown}");
    me.types("yes\n");
    let said = answered(&mut n, &token);
    assert_eq!(said["state"], "confirmed", "{said}");
    assert!(said["secret"].is_string() && said["presence"] == "terminal", "{said}");
}

#[test]
fn enter_y_and_a_late_yes_are_refusals_and_a_no_keeps_the_node_from_asking_again_soon() {
    for line in ["\n", "y\n", "no\n", "yes please\n", "yess\n"] {
        let dir = tempdir().unwrap();
        let mut n = terminal_node(dir.path());
        let (_, token) = session(&mut n, "demo", "a");
        let mut me = screen();
        ok(ask(&mut n, &token, &me.name));
        me.sees("Type yes");
        me.types(line);
        assert_eq!(answered(&mut n, &token)["state"], "refused", "{line:?}");
        assert_eq!(code(&ask(&mut n, &token, &me.name)), "quota", "a refusal is not asked again at once");
    }
}

#[test]
fn text_typed_before_the_question_is_dropped() {
    let dir = tempdir().unwrap();
    let mut n = terminal_node(dir.path());
    let (_, token) = session(&mut n, "demo", "a");
    let mut me = screen();
    me.types("yes\n");
    ok(ask(&mut n, &token, &me.name));
    me.sees("Type yes");
    me.types("no\n");
    assert_eq!(answered(&mut n, &token)["state"], "refused", "a yes typed ahead is not an answer to a question not yet shown");
}

#[test]
fn nobody_typing_makes_nothing_and_another_session_reads_nothing() {
    let dir = tempdir().unwrap();
    let mut n = terminal_node(dir.path());
    let (_, mine) = session(&mut n, "demo", "a");
    let (_, theirs) = session(&mut n, "demo", "b");
    let me = screen();
    ok(ask(&mut n, &mine, &me.name));
    std::thread::sleep(Duration::from_millis(500));
    let waiting = ok(req(&mut n, "presence_status", "", &mine, json!({})));
    assert!(waiting["state"] == "asking" && waiting["secret"].is_null(), "{waiting}");
    assert_eq!(ok(req(&mut n, "presence_status", "", &theirs, json!({})))["state"], "none");
    let mut params = kit::grants::person().enrolment("a test person", None);
    params["secret"] = json!("guess");
    let guess = req(&mut n, "person_enroll", "", &mine, params);
    assert_eq!(code(&guess), "denied", "{guess}");
}

#[test]
fn a_terminal_that_is_not_one_of_the_askers_is_refused() {
    let dir = tempdir().unwrap();
    let mut n = terminal_node(dir.path());
    let (_, token) = session(&mut n, "demo", "a");
    let file = dir.path().join("not-a-terminal");
    std::fs::write(&file, "x").unwrap();
    for named in ["", "/dev/null", "/dev/../etc/passwd", "/nonexistent/tty", file.to_str().unwrap(), "/dev/random"] {
        let refused = ask(&mut n, &token, named);
        assert_eq!(code(&refused), "denied", "{named}: {refused}");
    }
}
