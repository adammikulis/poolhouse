//! Which boards the node removes, and how: a board is renamed out of `boards/` before its files are deleted.
#![cfg(unix)]

mod kit;

use kit::*;
use poolhouse_node::node::Node;
use serde_json::json;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use tempfile::tempdir;

/// A node whose board ``id`` was made by a session that has ended.
fn ended(dir: &Path, id: &str) -> Node {
    let mut n = node(dir);
    n.board_grace_ms = 0;
    let (_, token) = session(&mut n, id, "short");
    ok(req(&mut n, "retire", id, &token, json!({"target": "", "reason": "done"})));
    n
}

fn trash(dir: &Path) -> Vec<String> {
    std::fs::read_dir(dir.join("boards")).unwrap().map(|e| e.unwrap().file_name().to_string_lossy().to_string()).filter(|n| n.starts_with('.')).collect()
}

#[test]
fn a_board_that_holds_only_the_ends_of_its_sessions_is_removed() {
    let dir = tempdir().unwrap();
    let mut n = ended(dir.path(), "plain");
    assert_eq!(n.reap_idle_boards().unwrap(), 1);
    assert!(!n.boards.contains_key("plain") && !dir.path().join("boards/plain").exists() && trash(dir.path()).is_empty());
}

#[test]
fn a_board_with_an_uploaded_file_is_not_removed() {
    let dir = tempdir().unwrap();
    let mut n = ended(dir.path(), "filed");
    let files = dir.path().join("boards/filed/files");
    std::fs::create_dir_all(&files).unwrap();
    std::fs::write(files.join("f1"), b"bytes a message may name").unwrap();
    assert_eq!(n.reap_idle_boards().unwrap(), 0, "a board holding a file stays");
    assert!(files.join("f1").exists());
}

#[test]
fn a_board_holding_a_flagged_entry_is_not_removed() {
    let dir = tempdir().unwrap();
    drop(ended(dir.path(), "flagged"));
    let item = json!({"entry": {"qid": "q1", "entry": "e1", "flags": ["injection"], "held_ms": 1, "released_ms": 0, "released_by": ""}});
    std::fs::write(dir.path().join("boards/flagged/quarantine.json"), serde_json::to_vec(&item).unwrap()).unwrap();
    let mut n = node(dir.path());
    n.board_grace_ms = 0;
    assert!(n.boards.contains_key("flagged"));
    assert_eq!(n.reap_idle_boards().unwrap(), 0, "a board with an item in quarantine stays");
}

#[test]
fn a_removal_that_cannot_finish_leaves_no_half_deleted_board_and_the_leftover_goes_when_the_node_starts() {
    let dir = tempdir().unwrap();
    let mut n = ended(dir.path(), "locked");
    let stuck = dir.path().join("boards/locked/stuck");
    std::fs::create_dir_all(&stuck).unwrap();
    std::fs::write(stuck.join("x"), b"x").unwrap();
    std::fs::set_permissions(&stuck, std::fs::Permissions::from_mode(0o500)).unwrap();
    let removed = n.reap_idle_boards().unwrap();
    assert_eq!(removed, 1);
    assert!(!n.boards.contains_key("locked"));
    assert!(!dir.path().join("boards/locked").exists(), "a board that could not be deleted is not left as a live directory");
    let left = trash(dir.path());
    assert_eq!(left.len(), 1, "what could not be deleted waits under a trash name: {left:?}");
    std::fs::set_permissions(dir.path().join("boards").join(&left[0]).join("stuck"), std::fs::Permissions::from_mode(0o700)).unwrap();
    drop(n);
    let again = node(dir.path());
    assert!(!again.boards.contains_key("locked"));
    assert!(trash(dir.path()).is_empty(), "the leftover is removed when the node starts");
}
