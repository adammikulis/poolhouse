//! What one origin's log may hold: a share for one author, bytes as well as rows, a reserve for the node's own rows, and the same
//! bounds on a copy of another device's log.

mod kit;

use kit::*;
use poolhouse_node::board::Board;
use poolhouse_node::error::{Error, Result};
use poolhouse_node::logbounds::{Bounds, AUTHOR_SHARE_PERCENT, MAX_LOG_BYTES, MAX_LOG_ROWS, RESERVED_BYTES, RESERVED_ROWS};
use poolhouse_node::row::{Kind, Row};
use poolhouse_node::sync::exchange;
use serde_json::json;
use tempfile::{tempdir, TempDir};

fn bounded(dir: &TempDir, seed: u8, bounds: Bounds) -> Board {
    Board::open_bounded(&dir.path().join("log"), "demo", "", key(seed), bounds).unwrap()
}

fn rows(rows: usize) -> Bounds {
    Bounds { rows, bytes: 1 << 24, reserved_rows: 4, reserved_bytes: 1 << 10, author_percent: 25 }
}

fn write(board: &mut Board, actor: &str, size: usize) -> Result<Row> {
    let body = json!({"type": "status", "from": actor, "to": "#general", "subject": "", "body": "x".repeat(size)});
    board.append(Kind::Message, actor, body, "")
}

fn quota(refused: Result<Row>) -> String {
    match refused {
        Err(Error::Quota(said)) => said,
        other => panic!("expected a quota refusal, got {other:?}"),
    }
}

fn held_bytes(board: &Board) -> u64 {
    board.rows(board.origin()).iter().map(|r| r.size() as u64 + 1).sum()
}

#[test]
fn the_defaults_are_the_documented_constants() {
    let b = Bounds::default();
    assert_eq!((b.rows, b.bytes, b.reserved_rows, b.reserved_bytes, b.author_percent), (MAX_LOG_ROWS, MAX_LOG_BYTES, RESERVED_ROWS, RESERVED_BYTES, AUTHOR_SHARE_PERCENT));
    assert!(MAX_LOG_BYTES <= 128 * 1024 * 1024 && RESERVED_ROWS < MAX_LOG_ROWS && RESERVED_BYTES < MAX_LOG_BYTES);
}

#[test]
fn one_author_is_held_to_its_share_of_the_rows_and_the_others_still_write() {
    let dir = tempdir().unwrap();
    let mut b = bounded(&dir, 1, rows(40));
    for _ in 0..10 {
        write(&mut b, "flood", 10).unwrap();
    }
    let said = quota(write(&mut b, "flood", 10));
    assert!(said.contains("flood") && said.contains("AUTHOR_SHARE_PERCENT") && said.contains("25 percent"), "{said}");
    for actor in ["calm", "other"] {
        for _ in 0..10 {
            write(&mut b, actor, 10).unwrap();
        }
    }
    assert_eq!(b.rows(b.origin()).len(), 30, "the flood took its tenth of the log and no more");
}

#[test]
fn the_bytes_of_the_log_are_bounded_as_well_as_its_rows_and_an_author_has_a_share_of_them() {
    let dir = tempdir().unwrap();
    let bounds = Bounds { rows: 1000, bytes: 40_000, reserved_rows: 10, reserved_bytes: 4_000, author_percent: 50 };
    let mut b = bounded(&dir, 1, bounds);
    let mut said = String::new();
    for _ in 0..30 {
        match write(&mut b, "big", 2_000) {
            Ok(_) => {}
            Err(Error::Quota(m)) => {
                said = m;
                break;
            }
            Err(e) => panic!("{e:?}"),
        }
    }
    assert!(said.contains("AUTHOR_SHARE_PERCENT") && said.contains("50 percent"), "an author is stopped at its share of the bytes: {said}");
    assert!(held_bytes(&b) <= 20_000, "{}", held_bytes(&b));
    let mut stopped = String::new();
    for _ in 0..30 {
        match write(&mut b, "small", 2_000) {
            Ok(_) => {}
            Err(Error::Quota(m)) => {
                stopped = m;
                break;
            }
            Err(e) => panic!("{e:?}"),
        }
    }
    assert!(stopped.contains("MAX_LOG_BYTES") && stopped.contains("40000 bytes"), "{stopped}");
    assert!(held_bytes(&b) <= 36_000, "user rows leave the reserve of bytes alone: {}", held_bytes(&b));
}

#[test]
fn a_full_log_refuses_authors_but_still_takes_a_head_and_the_nodes_own_rows_until_it_is_whole() {
    let dir = tempdir().unwrap();
    let mut b = bounded(&dir, 1, Bounds { author_percent: 100, ..rows(40) });
    for _ in 0..36 {
        write(&mut b, "a", 10).unwrap();
    }
    let said = quota(write(&mut b, "b", 10));
    assert!(said.contains("MAX_LOG_ROWS") && said.contains("40"), "{said}");
    assert!(b.seal().unwrap().is_some(), "a head still signs the log");
    b.append_node(Kind::Identity, "a", json!({"name": "a", "retired": true})).unwrap();
    b.append_node(Kind::Audit, "node", json!({"event": "retire", "subject": "a", "detail": "ended"})).unwrap();
    b.append_node(Kind::Audit, "node", json!({"event": "retire", "subject": "b", "detail": "ended"})).unwrap();
    assert_eq!(b.rows(b.origin()).len(), 40);
    let said = match b.append_node(Kind::Audit, "node", json!({"event": "retire"})) {
        Err(Error::Quota(said)) => said,
        other => panic!("{other:?}"),
    };
    assert!(said.contains("MAX_LOG_ROWS"), "the reserve is used up: {said}");
}

#[test]
fn the_node_s_own_rows_are_not_held_to_an_authors_share() {
    let dir = tempdir().unwrap();
    let mut b = bounded(&dir, 1, rows(40));
    for _ in 0..10 {
        write(&mut b, "flood", 10).unwrap();
    }
    quota(write(&mut b, "flood", 10));
    b.append_node(Kind::Identity, "flood", json!({"name": "flood", "retired": true})).unwrap();
}

#[test]
fn what_an_author_has_written_is_counted_again_when_the_log_is_reopened() {
    let dir = tempdir().unwrap();
    {
        let mut b = bounded(&dir, 1, rows(40));
        for _ in 0..10 {
            write(&mut b, "flood", 10).unwrap();
        }
    }
    let mut b = bounded(&dir, 1, rows(40));
    quota(write(&mut b, "flood", 10));
    write(&mut b, "calm", 10).unwrap();
}

#[test]
fn a_copy_of_another_devices_log_is_held_to_the_rows_and_the_bytes_a_device_keeps() {
    let (da, db) = (tempdir().unwrap(), tempdir().unwrap());
    let mut theirs = bounded(&db, 2, Bounds { author_percent: 100, ..rows(1000) });
    for _ in 0..30 {
        write(&mut theirs, "w", 2_000).unwrap();
    }
    let mut ours = bounded(&da, 1, rows(20));
    let report = exchange(&mut ours, &mut theirs).unwrap();
    assert_eq!(report.refused.get(theirs.origin()).map(String::as_str), Some("quota"), "{report:?}");
    assert!(ours.rows(theirs.origin()).is_empty());

    let dc = tempdir().unwrap();
    let mut ours = bounded(&dc, 3, Bounds { rows: 1000, bytes: 30_000, reserved_rows: 4, reserved_bytes: 1 << 10, author_percent: 25 });
    let report = exchange(&mut ours, &mut theirs).unwrap();
    assert_eq!(report.refused.get(theirs.origin()).map(String::as_str), Some("quota"), "{report:?}");
    assert!(ours.rows(theirs.origin()).is_empty());
}

fn small_node(dir: &std::path::Path) -> poolhouse_node::node::Node {
    let mut n = node(dir);
    n.log_bounds = Bounds { rows: 80, bytes: 1 << 24, reserved_rows: 8, reserved_bytes: 1 << 10, author_percent: 25 };
    n
}

fn child_of(n: &mut poolhouse_node::node::Node, parent: &str, id: &str) -> (String, String) {
    let r = ok(req(n, "register", "demo", parent, json!({"model": "claude-haiku-5-5", "harness": "claude-code", "session": id})));
    (r["name"].as_str().unwrap().into(), r["token"].as_str().unwrap().into())
}

#[test]
fn a_session_that_wrote_its_share_is_refused_while_the_others_post_and_claim_and_it_can_still_be_ended() {
    let dir = tempdir().unwrap();
    let mut n = small_node(dir.path());
    let (_, lead) = session(&mut n, "demo", "lead");
    let (flood, flood_token) = child_of(&mut n, &lead, "flood");
    let mut said = String::new();
    for i in 0..40 {
        let reply = post(&mut n, "demo", &flood_token, &format!("message {i}"));
        if reply["ok"] == false {
            assert_eq!(code(&reply), "quota", "{reply}");
            said = reply["error"]["message"].as_str().unwrap().to_string();
            break;
        }
    }
    assert!(said.contains("AUTHOR_SHARE_PERCENT") && said.contains(&flood), "the flood is stopped at its share: {said}");
    ok(post(&mut n, "demo", &lead, "the lead still posts"));
    ok(req(&mut n, "claim", "demo", &flood_token, json!({"kind": "port", "key": "9401"})));
    ok(req(&mut n, "claim", "demo", &lead, json!({"kind": "port", "key": "9402"})));
    ok(req(&mut n, "release", "demo", &flood_token, json!({"kind": "port", "key": "9401"})));
    let ended = ok(req(&mut n, "retire", "demo", &lead, json!({"target": flood})));
    assert!(ended.get("retired").is_some() || ended.get("target").is_some() || ended.is_object(), "{ended}");
    let identities = ok(req(&mut n, "read", "demo", &lead, json!({"kind": "identity"})));
    let retired = identities["entries"].as_array().unwrap().iter().filter(|e| e["fields"]["name"] == json!(flood) && e["fields"]["retired"] == json!(true)).count();
    assert_eq!(retired, 1, "the flood's end is on the board although it had used its share: {identities}");
}

#[test]
fn when_the_log_is_full_sessions_are_refused_with_the_limit_and_a_session_can_still_be_ended() {
    let dir = tempdir().unwrap();
    let mut n = small_node(dir.path());
    let (_, lead) = session(&mut n, "demo", "lead");
    let kids: Vec<(String, String)> = (0..5).map(|i| child_of(&mut n, &lead, &format!("kid{i}"))).collect();
    let mut said = String::new();
    'fill: for round in 0..40 {
        for (_, token) in &kids {
            let reply = post(&mut n, "demo", token, &format!("round {round}"));
            if reply["ok"] == false && reply["error"]["message"].as_str().unwrap().contains("MAX_LOG_ROWS") {
                said = reply["error"]["message"].as_str().unwrap().to_string();
                break 'fill;
            }
        }
    }
    assert!(said.contains("MAX_LOG_ROWS") && said.contains("80"), "the log filled and said which limit it was: {said}");
    ok(req(&mut n, "retire", "demo", &lead, json!({"target": kids[0].0})));
    ok(req(&mut n, "retire", "demo", &lead, json!({"target": kids[1].0})));
    let again = post(&mut n, "demo", &kids[2].1, "one more");
    assert_eq!(code(&again), "quota", "{again}");
}
