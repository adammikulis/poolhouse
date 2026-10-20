//! Durable logical identities and clocks survive transport segment replacement.
mod kit;

use kit::*;
use poolhouse_node::board::Board;
use poolhouse_node::fold::{fold, Context};
use poolhouse_node::row::Kind;
use poolhouse_node::sync::exchange;
use serde_json::json;
use std::sync::{Arc, atomic::{AtomicU64, Ordering}};
use tempfile::tempdir;

fn clocked(path: &std::path::Path, seed: u8, clock: &Arc<AtomicU64>) -> Board {
    let time = clock.clone();
    Board::open_with_clock(path, "demo", "", key(seed), Box::new(move || time.load(Ordering::SeqCst))).unwrap()
}

#[test]
fn original_task_id_and_task_number_accept_transitions_after_repeated_rotation() {
    let directory = tempdir().unwrap();
    let mut node = node(directory.path());
    let (name, token) = session(&mut node, "demo", "agent");
    let made = ok(req(&mut node, "task_create", "demo", &token,
        json!({"title": "Inspect records", "description": "Keep a stable logical reference", "acceptance": ["Records retained"]})));
    let id = made["id"].as_str().unwrap().to_string();
    let before = ok(req(&mut node, "task_get", "demo", &token, json!({"task": id})));
    node.host("demo").unwrap().board.rotate().unwrap();
    assert_eq!(ok(req(&mut node, "task_get", "demo", &token, json!({"task": id}))), before);
    ok(req(&mut node, "task_watch", "demo", &token, json!({"task": id, "on": true})));
    node.host("demo").unwrap().board.rotate().unwrap();
    let watched = ok(req(&mut node, "task_watchers", "demo", &token, json!({"task": id})));
    assert_eq!(watched["watchers"], json!([name]));
    assert_eq!(ok(req(&mut node, "task_get", "demo", &token, json!({"task": id})))["id"], json!(id));
}

#[test]
fn rotating_room_creator_preserves_foreign_join_order_and_original_creation_time() {
    let directory = tempdir().unwrap();
    let time = Arc::new(AtomicU64::new(1_000_000_000));
    let (a_path, b_path) = (directory.path().join("a"), directory.path().join("b"));
    let (mut a, mut b) = (clocked(&a_path, 1, &time), clocked(&b_path, 2, &time));
    a.append(Kind::Room, "host", json!({"op": "create", "room": "#records", "title": "Records", "open": true}), "").unwrap();
    a.seal().unwrap();
    exchange(&mut a, &mut b).unwrap();
    time.store(1_000_001_000, Ordering::SeqCst);
    b.append(Kind::Room, "guest", json!({"op": "join", "room": "#records"}), "").unwrap();
    b.seal().unwrap();
    exchange(&mut a, &mut b).unwrap();
    let before: Vec<_> = a.merged().into_iter().filter(|row| row.kind == Kind::Room).collect();
    a.rotate().unwrap();
    a.rotate().unwrap();
    assert_eq!(a.merged().into_iter().filter(|row| row.kind == Kind::Room).collect::<Vec<_>>(), before);
    let mut label = |_: &str| "d1".to_string();
    let (entries, rejected) = fold(&a.merged(), Context { own_origin: a.origin(), label: &mut label, is_local: &|_| false });
    assert!(rejected.is_empty());
    let rooms = poolhouse_node::rooms::fold(&entries);
    assert!(rooms["#records"].members.contains("guest"));
    assert_eq!(rooms["#records"].created_ms, 1_000_000_000);
    drop(a);
    let reopened = clocked(&a_path, 1, &time);
    assert_eq!(reopened.merged().into_iter().filter(|row| row.kind == Kind::Room).collect::<Vec<_>>(), before);
}

#[test]
fn rotation_does_not_refresh_note_ttl_and_cross_origin_supersession_keeps_original_id() {
    let directory = tempdir().unwrap();
    let time = Arc::new(AtomicU64::new(1_000_000_000));
    let (mut a, mut b) = (clocked(&directory.path().join("a"), 1, &time), clocked(&directory.path().join("b"), 2, &time));
    let original = a.append(Kind::Note, "writer", json!({"nkind": "fact", "title": "Original", "body": "x", "ttl_days": 1}), "").unwrap();
    time.fetch_add(12 * 3600 * 1000, Ordering::SeqCst);
    a.rotate().unwrap();
    assert_eq!(a.merged().iter().find(|row| row.kind == Kind::Note).unwrap(), &original);
    a.seal().unwrap();
    exchange(&mut a, &mut b).unwrap();
    b.append(Kind::Note, "writer", json!({"nkind": "fact", "title": "Replacement", "body": "y", "supersedes": [original.id()]}), "").unwrap();
    b.seal().unwrap();
    exchange(&mut a, &mut b).unwrap();
    a.rotate().unwrap();
    assert!(!a.rows(a.origin()).iter().any(|row| row.id() == original.id()));
    let expiring = a.append(Kind::Note, "writer", json!({"nkind": "fact", "title": "Expiring", "body": "z", "ttl_days": 1}), "").unwrap();
    a.rotate().unwrap();
    time.fetch_add(2 * 24 * 3600 * 1000, Ordering::SeqCst);
    a.rotate().unwrap();
    assert!(!a.rows(a.origin()).iter().any(|row| row.id() == expiring.id()));
}
