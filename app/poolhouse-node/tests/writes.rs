mod kit;

use kit::*;
use poolhouse_node::node::Node;
use serde_json::{json, Value};
use tempfile::tempdir;

fn say(n: &mut Node, token: &str, to: &str, text: &str) -> Value {
    req(n, "post", "demo", token, json!({"kind": "message", "fields": {"type": "question", "to": to, "body": text}}))
}

fn announce(n: &mut Node, token: &str, text: &str) -> Value {
    req(n, "post", "demo", token, json!({"kind": "message", "fields": {"type": "milestone", "to": "#announcements", "body": text}}))
}

fn note(n: &mut Node, token: &str, title: &str) -> Value {
    req(n, "post", "demo", token, json!({"kind": "note", "fields": {"nkind": "fact", "title": title, "body": "a note"}}))
}

fn port(n: &mut Node, token: &str, number: u32) -> Value {
    req(n, "claim", "demo", token, json!({"kind": "port", "key": number.to_string()}))
}

fn child_of(n: &mut Node, parent: &str, id: &str) -> (String, String) {
    let r = ok(req(n, "register", "demo", parent, json!({"model": "claude-haiku-5-5", "harness": "claude-code", "session": id})));
    (r["name"].as_str().unwrap().into(), r["token"].as_str().unwrap().into())
}

fn audited(n: &mut Node, token: &str, event: &str) -> usize {
    let got = ok(req(n, "read", "demo", token, json!({"kind": "audit"})));
    got["entries"].as_array().unwrap().iter().filter(|e| e["fields"]["event"] == event).count()
}

#[test]
fn a_session_writes_messages_notes_claims_events_and_announcements_as_fast_as_it_likes() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, parent) = session(&mut n, "demo", "a");
    let ((_, one), (_, two)) = (child_of(&mut n, &parent, "c1"), child_of(&mut n, &parent, "c2"));
    for (at, token) in [&parent, &one, &two].into_iter().enumerate() {
        for i in 0..40 {
            ok(say(&mut n, token, "#general", &format!("message {i}")));
            ok(note(&mut n, token, &format!("note {i}")));
            ok(port(&mut n, token, 9000 + 100 * at as u32 + i));
            let announced = ok(announce(&mut n, token, &format!("announcement {i}")));
            assert!(announced.get("over_quota").is_none(), "{announced}");
        }
    }
    assert_eq!(audited(&mut n, &parent, "announce_over_quota"), 0, "no announcement is told to the board as too many");
}

#[test]
fn messages_unread_in_an_inbox_and_from_one_sender_are_capped_until_the_recipient_reads() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((_, at), (_, bt), (_, ct), (reader, rt)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"), session(&mut n, "demo", "c"), session(&mut n, "demo", "r"));
    (n.writes.unread_per_sender, n.writes.inbox_pending) = (3, 5);
    for i in 0..3 {
        ok(say(&mut n, &at, &reader, &format!("a {i}")));
    }
    let refused = say(&mut n, &at, &reader, "a fourth");
    assert_eq!(code(&refused), "quota");
    assert!(refused["error"]["message"].as_str().unwrap().contains("unread"), "{refused}");
    ok(say(&mut n, &bt, &reader, "b 0"));
    ok(say(&mut n, &bt, &reader, "b 1"));
    assert_eq!(code(&say(&mut n, &ct, &reader, "c is held back by what waits for the reader")), "quota");
    ok(say(&mut n, &ct, "#general", "a room is not an inbox"));
    let first = ok(req(&mut n, "read", "demo", &rt, json!({"inbox": true})));
    assert_eq!(first["entries"].as_array().unwrap().len(), 5);
    assert_eq!(code(&say(&mut n, &at, &reader, "reading without moving the cursor is not having read")), "quota");
    ok(req(&mut n, "read", "demo", &rt, json!({"inbox": true, "since": first["cursor"]})));
    ok(say(&mut n, &at, &reader, "the reader moved its cursor past what was sent"));
    ok(say(&mut n, &ct, &reader, "and another sender may write"));
}

#[test]
fn an_author_keeps_so_many_notes_and_another_author_is_not_held_back() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((_, at), (_, bt)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"));
    n.writes.notes_per_author = 3;
    for i in 0..3 {
        ok(note(&mut n, &at, &format!("note {i}")));
    }
    let refused = note(&mut n, &at, "a fourth");
    assert_eq!(code(&refused), "quota");
    assert!(refused["error"]["message"].as_str().unwrap().contains("the limit is 3, set by notes_per_author in write-limits.json"), "{refused}");
    ok(note(&mut n, &bt, "b writes its first"));
    ok(say(&mut n, &at, "#general", "a message is not a note"));
}

#[test]
fn a_session_may_have_any_number_of_live_subagents() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((_, parent), (_, other)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"));
    let children: Vec<(String, String)> = (0..40).map(|i| child_of(&mut n, &parent, &format!("c{i}"))).collect();
    assert_eq!(child_of(&mut n, &parent, "c3").0, children[3].0, "a subagent registered again is the one it was");
    child_of(&mut n, &other, "d0");
    ok(req(&mut n, "retire", "demo", &children[0].1, json!({})));
    child_of(&mut n, &parent, "c40");
}

fn fill(n: &mut Node, token: &str, rows: usize) {
    for i in 0..rows {
        ok(req(n, "post", "demo", token, json!({"kind": "message", "fields": {"type": "status", "to": "#general", "body": format!("row {i}")}})));
    }
}

#[test]
fn a_post_is_checked_against_counters_kept_as_rows_arrive_and_never_folds_the_board() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((_, at), (reader, rt)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "r"));
    (n.writes.unread_per_sender, n.writes.inbox_pending, n.writes.notes_per_author) = (4, 5, 4);
    for rows in [0, 300] {
        fill(&mut n, &at, rows);
        let (folds, counted) = (n.folds, n.tally("demo").unwrap().counted);
        ok(say(&mut n, &at, &reader, "a direct message"));
        ok(note(&mut n, &at, &format!("a note after {rows} rows")));
        ok(say(&mut n, &at, "#general", "a message to the room everyone is in"));
        assert_eq!(n.folds, folds, "a post folds nothing: {rows} rows on the board");
        assert!(n.tally("demo").unwrap().counted - counted <= 6, "it counts the rows written since the last check, not the board: {rows} rows");
    }
    ok(say(&mut n, &at, &reader, "a third"));
    ok(say(&mut n, &at, &reader, "a fourth"));
    assert_eq!(code(&say(&mut n, &at, &reader, "a fifth")), "quota", "four unread from one sender");
    let all = ok(req(&mut n, "read", "demo", &rt, json!({"inbox": true})));
    assert_eq!(code(&say(&mut n, &at, &reader, "reading without moving the cursor is not having read")), "quota");
    ok(req(&mut n, "read", "demo", &rt, json!({"inbox": true, "since": all["cursor"]})));
    ok(say(&mut n, &at, &reader, "the reader has read what was waiting"));
    ok(note(&mut n, &at, "a third note"));
    ok(note(&mut n, &at, "a fourth note"));
    assert_eq!(code(&note(&mut n, &at, "a fifth note")), "quota", "four notes by one author");
}

#[test]
fn counters_are_rebuilt_from_the_log_when_the_node_starts() {
    let dir = tempdir().unwrap();
    let (at, reader);
    {
        let mut n = node(dir.path());
        let (_, token) = session(&mut n, "demo", "a");
        let (name, _) = session(&mut n, "demo", "r");
        (at, reader) = (token, name);
        for i in 0..3 {
            ok(say(&mut n, &at, &reader, &format!("waiting {i}")));
            ok(note(&mut n, &at, &format!("note {i}")));
        }
    }
    let mut n = node(dir.path());
    (n.writes.unread_per_sender, n.writes.notes_per_author) = (3, 3);
    assert_eq!(code(&say(&mut n, &at, &reader, "a fourth")), "quota", "the three waiting messages were counted again from the log");
    assert_eq!(code(&note(&mut n, &at, "a fourth")), "quota", "and the three notes");
}
