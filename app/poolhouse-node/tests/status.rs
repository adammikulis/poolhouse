mod kit;

use kit::*;
use serde_json::json;
use tempfile::tempdir;

#[test]
fn the_status_of_a_board_counts_its_entries_by_kind_and_says_whether_its_logs_are_whole() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((_, at), (bob, _)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"));
    ok(post(&mut n, "demo", &at, "one"));
    ok(req(&mut n, "post", "demo", &at, json!({"kind": "message", "fields": {"type": "note", "to": bob, "body": "two"}})));
    ok(req(&mut n, "post", "demo", &at, json!({"kind": "note", "fields": {"nkind": "fact", "title": "t", "body": "b"}})));
    let board = ok(req(&mut n, "status", "demo", &at, json!({})))["board"].clone();
    assert_eq!((board["entries"]["message"].clone(), board["entries"]["note"].clone(), board["entries"]["identity"].clone()), (json!(2), json!(1), json!(2)));
    assert_eq!((board["damaged"].clone(), board["rejected"].clone(), board["sessions"].clone()), (json!({}), json!(0), json!(2)));
    assert_eq!(code(&req(&mut n, "status", "demo", "nothing", json!({}))), "denied", "the detail is for a session of the board");
}
