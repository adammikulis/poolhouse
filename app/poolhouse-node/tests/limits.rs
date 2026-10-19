//! What one lineage may make the node do: projects, links and sessions as fast as it likes, and boards up to the number a node hosts.
#![cfg(unix)]

mod kit;

use kit::*;
use poolhouse_node::lineage::Peer;
use poolhouse_node::node::{Node, MAX_BOARDS};
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
fn a_lineage_makes_projects_as_fast_as_it_likes() {
    let (state, work) = (tempdir().unwrap(), tempdir().unwrap());
    let mut n = node(state.path());
    for i in 0..25 {
        let id = format!("p{i}");
        let token = placer(&mut n, &me(), &id);
        let reply = add(&mut n, &me(), &token, &id, &folder(work.path(), &id));
        assert_eq!(reply["ok"], true, "project {i}: {reply}");
    }
}

#[test]
fn a_lineage_makes_links_as_fast_as_it_likes() {
    let state = tempdir().unwrap();
    let mut n = node(state.path());
    let made = ok(register(&mut n, &me(), "from", "lead"));
    let (name, token) = (made["name"].as_str().unwrap().to_string(), made["token"].as_str().unwrap().to_string());
    let targets: Vec<String> = (0..12).map(|i| format!("b{i}")).collect();
    for to in &targets {
        ok(register(&mut n, &me(), to, "x"));
    }
    let link = |n: &mut Node, to: &str| req_from(n, &me(), "link", "from", &token, json!({"to": to, "channels": [], "mode": "ro"}));
    assert_eq!(code(&link(&mut n, &targets[0])), "denied", "no grant, no link");
    allow(&mut n, "from", &token, &name, &["link_read"]);
    for to in &targets {
        let reply = link(&mut n, to);
        assert_eq!(reply["ok"], true, "{reply}");
    }
}

#[test]
fn a_lineage_creates_boards_up_to_what_a_node_hosts_and_registers_as_many_sessions_as_it_needs() {
    let state = tempdir().unwrap();
    let mut n = node(state.path());
    for i in 0..MAX_BOARDS {
        let reply = register(&mut n, &me(), &format!("b{i}"), "s");
        assert_eq!(reply["ok"], true, "board {i}: {reply}");
    }
    let refused = register(&mut n, &me(), "one-more", "s");
    assert_eq!(code(&refused), "quota", "{refused}");
    assert!(refused["error"]["message"].as_str().unwrap().contains(&format!("the limit is {MAX_BOARDS}, set by MAX_BOARDS in node.rs")), "{refused}");
    for i in 0..200 {
        let reply = register(&mut n, &me(), "b0", &format!("s{i}"));
        assert_eq!(reply["ok"], true, "session {i}: {reply}");
    }
}
