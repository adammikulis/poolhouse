#![allow(dead_code)]

pub mod gov;
pub mod govnode;
pub mod grants;
pub mod netkit;
pub mod phone;
#[cfg(unix)]
pub mod procs;

use ed25519_dalek::SigningKey;
use poolhouse_node::board::Board;
use poolhouse_node::lineage::Peer;
use poolhouse_node::node::Node;
use poolhouse_node::row::{Kind, Row};
use serde_json::{json, Value};
use std::path::Path;
use tempfile::TempDir;

/// A scratch directory whose path is short enough for a Unix socket inside it.
pub fn short_dir() -> TempDir {
    #[cfg(unix)]
    return tempfile::Builder::new().prefix("pn").tempdir_in("/tmp").unwrap();
    #[cfg(windows)]
    return tempfile::Builder::new().prefix("pn").tempdir().unwrap();
}

/// End the process ``pid`` without letting it clean up (kill -9; `taskkill /F` on Windows).
pub fn kill_hard(pid: u32) {
    #[cfg(unix)]
    {
        // SAFETY: kill -9 of a node process the calling test started.
        assert_eq!(unsafe { libc::kill(pid as i32, libc::SIGKILL) }, 0);
    }
    #[cfg(windows)]
    assert!(std::process::Command::new("taskkill").args(["/F", "/PID"]).arg(pid.to_string()).output().unwrap().status.success());
}

pub fn key(seed: u8) -> SigningKey {
    SigningKey::from_bytes(&[seed; 32])
}

pub fn board(dir: &TempDir, seed: u8) -> Board {
    Board::open(&dir.path().join("log"), "demo", "", key(seed)).unwrap()
}

pub fn say(board: &mut Board, actor: &str, text: &str) -> Row {
    let body = json!({"type": "status", "from": actor, "to": "#general", "subject": "", "body": text});
    board.append(Kind::Message, actor, body, "").unwrap()
}

/// Recompute a row's hash so a tampered row is still a well-formed link in its chain.
pub fn rehash(mut row: Row) -> Row {
    row.hash = row.digest();
    row
}

pub fn texts(entries: &Value) -> Vec<String> {
    entries.as_array().unwrap().iter().filter(|e| e["kind"] == "message")
        .map(|e| e["fields"]["body"].as_str().unwrap().to_string()).collect()
}

/// A person who says yes to the question of the first key or phone at once: the test's stand-in for the operating system's prompt,
/// put on the node of a test through the library field `Node::firsts`. No binary builds one.
pub struct SaysYes;

struct Yes;

impl poolhouse_node::presence::Asking for Yes {
    fn poll(&mut self) -> Option<bool> {
        Some(true)
    }
}

impl poolhouse_node::presence::Presence for SaysYes {
    fn kind(&self) -> &'static str {
        "test"
    }

    fn begin(&mut self, _what: &str, _terminal: &str) -> poolhouse_node::error::Result<Box<dyn poolhouse_node::presence::Asking>> {
        Ok(Box::new(Yes))
    }
}

/// A node on ``dir`` whose person says yes to the prompt of the first key or phone.
pub fn node(dir: &Path) -> Node {
    let mut node = Node::open(dir).unwrap();
    node.firsts = poolhouse_node::presence::Firsts::new(Box::new(SaysYes));
    node
}

/// The process of this test: the peer of every request `req` makes, so the node reads the lineage of the test binary.
pub fn me() -> Peer {
    Peer { pid: Some(std::process::id()) }
}

pub fn req(node: &mut Node, method: &str, board: &str, token: &str, params: Value) -> Value {
    req_from(node, &me(), method, board, token, params)
}

/// A request made by the process ``peer``.
pub fn req_from(node: &mut Node, peer: &Peer, method: &str, board: &str, token: &str, params: Value) -> Value {
    let mut r = json!({"v": 1, "id": 1, "method": method, "params": params});
    if !board.is_empty() {
        r["board"] = json!(board);
    }
    if !token.is_empty() {
        r["token"] = json!(token);
    }
    poolhouse_node::api::handle(node, &r, peer)
}

/// How a test reaches the node it holds: every call from this process.
pub fn caller(n: &mut Node) -> impl FnMut(&str, &str, &str, Value) -> Value + '_ {
    move |m, b, t, p| req(n, m, b, t, p)
}

/// The fingerprints of the person keys the node holds.
pub fn held(n: &mut Node) -> Vec<String> {
    ok(req(n, "person_keys", "", "", json!({})))["keys"].as_array().unwrap().iter().map(|k| k["fingerprint"].as_str().unwrap().to_string()).collect()
}

pub fn ok(reply: Value) -> Value {
    assert_eq!(reply["ok"], true, "{reply}");
    reply["result"].clone()
}

pub fn code(reply: &Value) -> String {
    assert_eq!(reply["ok"], false, "{reply}");
    reply["error"]["code"].as_str().unwrap().to_string()
}

/// Register a session; returns (name, token).
pub fn session(node: &mut Node, board: &str, id: &str) -> (String, String) {
    let r = ok(req(node, "register", board, "", json!({"model": "claude-sonnet-5-5", "harness": "claude-code", "session": id})));
    (r["name"].as_str().unwrap().into(), r["token"].as_str().unwrap().into())
}

pub fn post(node: &mut Node, board: &str, token: &str, text: &str) -> Value {
    req(node, "post", board, token, json!({"kind": "message", "fields": {"type": "status", "body": text}}))
}

/// Enrol the test person on an in-process node and grant the session ``name`` of ``board`` (``token`` is its own) the actions ``only`` names, or all.
pub fn allow(node: &mut Node, board: &str, token: &str, name: &str, only: &[&str]) {
    let dir = node.dir.clone();
    let mut call = |m: &str, b: &str, t: &str, p: Value| req(node, m, b, t, p);
    grants::enrolled(&dir, &mut call, token);
    grants::person().allow(&mut call, board, token, name, only);
}

/// The test person revokes the grant of ``action`` that `allow` gave the session ``name`` (``token`` is any session of ``board``).
pub fn revoke(node: &mut Node, board: &str, token: &str, name: &str, action: &str) {
    let mut call = |m: &str, b: &str, t: &str, p: Value| req(node, m, b, t, p);
    let reply = grants::person().revoke(&mut call, board, token, &grants::gid_of(name, action));
    assert_eq!(reply["ok"], true, "revoke {action} of {name}: {reply}");
}
