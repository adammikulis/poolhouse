//! What one lineage may make the node do: only what succeeded is counted, a lineage has limits of its own, a flood of fresh
//! sessions fills no table another lineage needs, and a lineage registers and creates boards a bounded number of times.
#![cfg(unix)]

mod kit;

use kit::procs::*;
use kit::*;
use poolhouse_node::lineage::Peer;
use poolhouse_node::node::Node;
use serde_json::{json, Value};
use std::path::Path;
use tempfile::tempdir;

fn register(n: &mut Node, from: &Peer, board: &str, session: &str) -> Value {
    req_from(n, from, "register", board, "", json!({"model": "claude-sonnet-5-5", "harness": "claude-code", "session": session}))
}

fn add(n: &mut Node, from: &Peer, token: &str, id: &str, place: &Path) -> Value {
    req_from(n, from, "project_add", "", token, json!({"id": id, "kind": "folder", "path": place.to_str().unwrap()}))
}

fn folder(root: &Path, name: &str) -> std::path::PathBuf {
    let dir = root.join(name);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

/// A session of the board ``id`` registered from ``from`` that may add a place that is not a git repository.
fn placer(n: &mut Node, from: &Peer, id: &str) -> String {
    let made = ok(register(n, from, id, "placer"));
    let (name, token) = (made["name"].as_str().unwrap().to_string(), made["token"].as_str().unwrap().to_string());
    allow(n, id, &token, &name, &["source_add"]);
    token
}

#[test]
fn only_a_project_that_was_added_counts_toward_the_rate_and_each_lineage_has_its_own() {
    let (state, work) = (tempdir().unwrap(), tempdir().unwrap());
    let mut n = node(state.path());
    let outside = process(true);
    let token = placer(&mut n, &me(), "first");
    for i in 0..15 {
        let reply = add(&mut n, &me(), &token, "first", &work.path().join(format!("missing-{i}")));
        assert_eq!(code(&reply), "invalid", "{reply}");
    }
    for i in 0..10 {
        let id = if i == 0 { "first".to_string() } else { format!("p{i}") };
        let token = if i == 0 { token.clone() } else { placer(&mut n, &me(), &id) };
        let reply = add(&mut n, &me(), &token, &id, &folder(work.path(), &id));
        assert_eq!(reply["ok"], true, "attempts that failed are not counted, so project {i} is made: {reply}");
    }
    let more = placer(&mut n, &me(), "eleventh");
    assert_eq!(code(&add(&mut n, &me(), &more, "eleventh", &folder(work.path(), "eleventh"))), "quota", "the lineage made ten in a minute");

    let theirs = placer(&mut n, &peer(&outside), "other");
    let reply = add(&mut n, &peer(&outside), &theirs, "other", &folder(work.path(), "other"));
    assert_eq!(reply["ok"], true, "another lineage is not held back by the first one's: {reply}");
    done(vec![outside]);
}

#[test]
fn throwaway_sessions_of_one_lineage_share_its_limit_and_make_no_more_projects_than_it_may() {
    let (state, work) = (tempdir().unwrap(), tempdir().unwrap());
    let mut n = node(state.path());
    let mut made = 0;
    for i in 0..12 {
        let id = format!("t{i}");
        let token = placer(&mut n, &me(), &id);
        if add(&mut n, &me(), &token, &id, &folder(work.path(), &id))["ok"] == true {
            made += 1;
        }
    }
    assert_eq!(made, 10, "a fresh session of the same lineage has no fresh limit");
}

#[test]
fn a_lineage_makes_six_links_a_minute_and_a_refused_one_is_not_counted() {
    let state = tempdir().unwrap();
    let mut n = node(state.path());
    let outside = process(true);
    let made = ok(register(&mut n, &me(), "from", "lead"));
    let (name, token) = (made["name"].as_str().unwrap().to_string(), made["token"].as_str().unwrap().to_string());
    for to in ["b1", "b2", "b3", "b4", "b5", "b6", "b7"] {
        ok(register(&mut n, &me(), to, "x"));
    }
    let link = |n: &mut Node, from: &Peer, token: &str, to: &str| req_from(n, from, "link", "from", token, json!({"to": to, "channels": [], "mode": "ro"}));
    for _ in 0..8 {
        assert_eq!(code(&link(&mut n, &me(), &token, "b1")), "denied", "no grant, no link");
    }
    allow(&mut n, "from", &token, &name, &["link_read"]);
    for to in ["b1", "b2", "b3", "b4", "b5", "b6"] {
        let reply = link(&mut n, &me(), &token, to);
        assert_eq!(reply["ok"], true, "eight refused attempts counted for nothing: {reply}");
    }
    assert_eq!(code(&link(&mut n, &me(), &token, "b7")), "quota", "six links in a minute");
    let second = ok(register(&mut n, &me(), "from", "second"));
    let (second_name, second_token) = (second["name"].as_str().unwrap().to_string(), second["token"].as_str().unwrap().to_string());
    allow(&mut n, "from", &second_token, &second_name, &["link_read"]);
    assert_eq!(code(&link(&mut n, &me(), &second_token, "b7")), "quota", "a second session of the lineage has no limit of its own");

    let theirs = ok(register(&mut n, &peer(&outside), "from", "third"));
    let (their_name, their_token) = (theirs["name"].as_str().unwrap().to_string(), theirs["token"].as_str().unwrap().to_string());
    allow(&mut n, "from", &their_token, &their_name, &["link_read"]);
    assert_eq!(link(&mut n, &peer(&outside), &their_token, "b7")["ok"], true, "another lineage links as it may");
    done(vec![outside]);
}

#[test]
fn a_lineage_registers_a_bounded_number_of_sessions_and_creates_a_bounded_number_of_boards() {
    let state = tempdir().unwrap();
    let mut n = node(state.path());
    let outside = process(true);
    let mut boards = 0;
    for i in 0..300 {
        if register(&mut n, &me(), &format!("b{i}"), "s")["ok"] == true {
            boards += 1;
        }
    }
    assert!(boards <= 16, "one lineage made {boards} boards in a few seconds");
    let mut sessions = 0;
    for i in 0..200 {
        if register(&mut n, &me(), "b0", &format!("s{i}"))["ok"] == true {
            sessions += 1;
        }
    }
    assert!(sessions <= 60, "one lineage registered {sessions} sessions in a few seconds");
    let theirs = register(&mut n, &peer(&outside), "elsewhere", "s");
    assert_eq!(theirs["ok"], true, "another lineage is not held back by the first one's: {theirs}");
    done(vec![outside]);
}
