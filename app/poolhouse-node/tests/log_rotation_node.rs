//! A node whose board log starts a new segment: the view keeps the notes, the counters are counted again, and a restart reads it back.
#![cfg(unix)]

mod kit;

use kit::*;
use poolhouse_node::logbounds::Bounds;
use poolhouse_node::row::Kind;
use serde_json::json;
use tempfile::tempdir;

fn titles(entries: &[poolhouse_node::fold::Entry], kind: Kind) -> Vec<String> {
    entries.iter().filter(|e| e.kind == kind).map(|e| e.fields.get("title").or(e.fields.get("body")).and_then(|v| v.as_str()).unwrap_or("").to_string()).collect()
}

#[test]
fn a_board_keeps_its_notes_and_counts_them_again_when_its_log_starts_a_segment() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    n.log_bounds = Bounds { rows: 80, bytes: 1 << 24, reserved_rows: 8, reserved_bytes: 1 << 10, last_rows: 2, last_bytes: 1 << 9, author_percent: 100, lineage_percent: 100 };
    n.log_rotate_percent = 75;
    let (name, token) = session(&mut n, "demo", "agent");
    ok(req(&mut n, "post", "demo", &token, json!({"kind": "note", "fields": {"nkind": "fact", "title": "keep", "body": "a note outlives the segment"}})));
    assert_eq!(n.tally("demo").unwrap().notes_by(&name), 1);
    let first = |n: &mut poolhouse_node::node::Node| {
        let board = &n.host("demo").unwrap().board;
        board.first_seq(board.origin())
    };
    for i in 0..100 {
        if first(&mut n) > 1 {
            break;
        }
        ok(post(&mut n, "demo", &token, &format!("chatter {i}")));
    }
    assert!(first(&mut n) > 1, "the log started a segment");
    let entries = n.view("demo").unwrap();
    assert_eq!(titles(&entries, Kind::Note), vec!["keep".to_string()], "the note is in the view");
    assert!(!titles(&entries, Kind::Message).iter().any(|t| t == "chatter 0"), "the old chatter is in the archive");
    assert_eq!(n.tally("demo").unwrap().notes_by(&name), 1, "the notes are counted again from the segment, once each");
    ok(post(&mut n, "demo", &token, "after"));
    drop(n);

    let mut again = node(dir.path());
    let entries = again.view("demo").unwrap();
    assert_eq!(titles(&entries, Kind::Note), vec!["keep".to_string()], "a restart reads the segment back");
    ok(post(&mut again, "demo", &token, "after the restart"));
    assert_eq!(again.tally("demo").unwrap().notes_by(&name), 1);
}

#[test]
fn the_counters_are_counted_again_from_a_new_segment_longer_than_what_they_had_counted() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    n.log_bounds = Bounds { rows: 80, bytes: 1 << 24, reserved_rows: 8, reserved_bytes: 1 << 10, last_rows: 2, last_bytes: 1 << 9, author_percent: 100, lineage_percent: 100 };
    n.log_rotate_percent = 75;
    let (name, token) = session(&mut n, "demo", "agent");
    for i in 0..3 {
        ok(post(&mut n, "demo", &token, &format!("early {i}")));
    }
    assert_eq!(n.tally("demo").unwrap().notes_by(&name), 0);
    let fields = |title: &str, supersedes: &[String]| json!({"nkind": "fact", "title": title, "body": "x", "author": name, "supersedes": supersedes});
    let board = &mut n.host("demo").unwrap().board;
    for i in 0..6 {
        board.append(Kind::Note, &name, fields(&format!("kept {i}"), &[]), "").unwrap();
    }
    let old: Vec<String> = (0..2).map(|i| board.append(Kind::Note, &name, fields(&format!("old {i}"), &[]), "").unwrap().id()).collect();
    board.append(Kind::Note, &name, fields("newer", &old), "").unwrap();
    let origin = board.origin().to_string();
    for i in 0..100 {
        if board.first_seq(&origin) > 1 {
            break;
        }
        say(board, &name, &format!("chatter {i}"));
    }
    assert!(board.first_seq(&origin) > 1, "the log started a segment");
    assert_eq!(n.tally("demo").unwrap().notes_by(&name), 7, "the six and the one that replaced two, counted from the segment");
}
