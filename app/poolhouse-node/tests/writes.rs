mod kit;

use kit::*;
use poolhouse_node::node::Node;
use serde_json::{json, Value};
use std::time::Duration;
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
fn a_session_sends_so_many_messages_notes_and_claims_in_a_window_and_a_refusal_counts_for_nothing() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((_, at), (_, bt)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"));
    for i in 0..10 {
        ok(say(&mut n, &at, "#general", &format!("message {i}")));
        ok(note(&mut n, &at, &format!("note {i}")));
        ok(port(&mut n, &at, 9000 + i));
    }
    for refused in [say(&mut n, &at, "#general", "one more"), note(&mut n, &at, "one more"), port(&mut n, &at, 9100)] {
        assert_eq!(code(&refused), "quota", "{refused}");
    }
    ok(say(&mut n, &bt, "#general", "another session has its own count"));
    n.writes.window_s = 0.06;
    std::thread::sleep(Duration::from_millis(100));
    ok(say(&mut n, &at, "#general", "the window moved on"));
    ok(say(&mut n, &at, "#general", "and what was refused was never counted"));
}

#[test]
fn a_subagent_sends_fewer_and_what_it_sends_counts_against_its_parents_subagent_allowance_not_its_own() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, parent) = session(&mut n, "demo", "a");
    let ((_, one), (_, two)) = (child_of(&mut n, &parent, "c1"), child_of(&mut n, &parent, "c2"));
    for i in 0..10 {
        ok(say(&mut n, &one, "#general", &format!("child {i}")));
    }
    assert_eq!(code(&say(&mut n, &one, "#general", "an eleventh")), "quota", "a subagent sends ten in a window");
    for i in 0..30 {
        ok(say(&mut n, &parent, "#general", &format!("parent {i}")));
    }
    assert_eq!(code(&say(&mut n, &parent, "#general", "a thirty-first")), "quota", "the parent sends thirty of its own, whatever its subagent sent");
    for i in 0..10 {
        ok(say(&mut n, &two, "#general", &format!("sibling {i}")));
    }
    assert_eq!(code(&say(&mut n, &two, "#general", "an eleventh")), "quota", "a sibling has a window of its own");
}

#[test]
fn the_subagents_of_one_session_share_a_larger_allowance_of_their_own_and_the_parent_is_not_held_back_by_it() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, parent) = session(&mut n, "demo", "a");
    let ((_, one), (_, two)) = (child_of(&mut n, &parent, "c1"), child_of(&mut n, &parent, "c2"));
    n.writes.subagent_sends = 15;
    for i in 0..10 {
        ok(say(&mut n, &one, "#general", &format!("child {i}")));
    }
    for i in 0..5 {
        ok(say(&mut n, &two, "#general", &format!("sibling {i}")));
    }
    let held_back = say(&mut n, &two, "#general", "a sixth, past what its parent's subagents may send together");
    assert_eq!(code(&held_back), "quota");
    for i in 0..30 {
        ok(say(&mut n, &parent, "#general", &format!("parent {i}")));
    }
}

#[test]
fn an_announcement_is_never_refused_and_the_board_is_told_when_a_session_announces_too_much() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((_, at), (_, bt)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"));
    for i in 0..6 {
        ok(announce(&mut n, &at, &format!("announcement {i}")));
    }
    assert_eq!(audited(&mut n, &bt, "announce_over_quota"), 0, "six in the window is within it");
    for i in 6..9 {
        ok(announce(&mut n, &at, &format!("announcement {i}")));
    }
    assert_eq!(audited(&mut n, &bt, "announce_over_quota"), 3, "the three past the sixth are told to the board and written");
    n.writes.sends = 3;
    n.writes.announces = 1000;
    n.writes.window_s = 60.0;
    ok(announce(&mut n, &bt, "one"));
    ok(announce(&mut n, &bt, "two"));
    ok(announce(&mut n, &bt, "three"));
    let before = audited(&mut n, &bt, "announce_over_quota");
    ok(announce(&mut n, &bt, "four, past what it may send"));
    assert_eq!(audited(&mut n, &bt, "announce_over_quota"), before + 1, "an announcement sent faster than the window allows is written and told");
}

#[test]
fn messages_unread_in_an_inbox_and_from_one_sender_are_capped_until_the_recipient_reads() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((_, at), (_, bt), (_, ct), (reader, rt)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"), session(&mut n, "demo", "c"), session(&mut n, "demo", "r"));
    (n.writes.sends, n.writes.unread_per_sender, n.writes.inbox_pending) = (1000, 3, 5);
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
    (n.writes.sends, n.writes.notes_per_author) = (1000, 3);
    for i in 0..3 {
        ok(note(&mut n, &at, &format!("note {i}")));
    }
    let refused = note(&mut n, &at, "a fourth");
    assert_eq!(code(&refused), "quota");
    assert!(refused["error"]["message"].as_str().unwrap().contains("3 notes"), "{refused}");
    ok(note(&mut n, &bt, "b writes its first"));
    ok(say(&mut n, &at, "#general", "a message is not a note"));
}

#[test]
fn a_session_has_so_many_live_subagents_and_a_retired_one_makes_room() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((_, parent), (_, other)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"));
    let children: Vec<(String, String)> = (0..8).map(|i| child_of(&mut n, &parent, &format!("c{i}"))).collect();
    let refused = req(&mut n, "register", "demo", &parent, json!({"model": "claude-haiku-5-5", "harness": "claude-code", "session": "c8"}));
    assert_eq!(code(&refused), "quota");
    assert!(refused["error"]["message"].as_str().unwrap().contains("8 live subagents"), "{refused}");
    assert_eq!(child_of(&mut n, &parent, "c3").0, children[3].0, "a subagent registered again is the one it was");
    child_of(&mut n, &other, "d0");
    ok(req(&mut n, "retire", "demo", &children[0].1, json!({})));
    child_of(&mut n, &parent, "c8");
}

fn sleeper() -> std::process::Child {
    #[cfg(unix)]
    let mut command = { let mut c = std::process::Command::new("sleep"); c.arg("120"); c };
    #[cfg(windows)]
    let mut command = { let mut c = std::process::Command::new("ping"); c.args(["-n", "120", "127.0.0.1"]); c };
    command.stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).spawn().unwrap()
}

fn age_children(dir: &std::path::Path, parent: &str, seen_ms_ago: u64) {
    let file = dir.join("boards").join("demo").join("names.json");
    let mut names: Value = serde_json::from_slice(&std::fs::read(&file).unwrap()).unwrap();
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis() as u64;
    for ident in names.as_object_mut().unwrap().values_mut().filter(|i| i["parent"] == parent) {
        ident["seen_ms"] = json!(now - seen_ms_ago);
    }
    std::fs::write(&file, serde_json::to_vec(&names).unwrap()).unwrap();
}

#[test]
fn subagents_nobody_has_heard_from_and_that_hold_nothing_do_not_count_as_live_and_those_holding_a_claim_do() {
    let dir = tempdir().unwrap();
    let (parent_name, parent, children);
    {
        let mut n = node(dir.path());
        let (name, token) = session(&mut n, "demo", "a");
        (parent_name, parent) = (name, token);
        children = (0..8).map(|i| child_of(&mut n, &parent, &format!("c{i}"))).collect::<Vec<_>>();
        for (i, (_, token)) in children.iter().enumerate() {
            ok(port(&mut n, token, 9400 + i as u32));
        }
    }
    age_children(dir.path(), &parent_name, 2 * 3600 * 1000);
    let mut n = node(dir.path());
    let register = |n: &mut Node, id: &str| req(n, "register", "demo", &parent, json!({"model": "claude-haiku-5-5", "harness": "claude-code", "session": id}));
    let refused = register(&mut n, "c8");
    assert_eq!(code(&refused), "quota", "eight subagents each holding a claim are live, however long ago they spoke: {refused}");
    ok(req(&mut n, "release", "demo", &parent, json!({"kind": "port", "key": "9400", "held_by": children[0].0})));
    ok(register(&mut n, "c8"));
    let refused = register(&mut n, "c9");
    assert_eq!(code(&refused), "quota", "the new one is heard from now, so it counts");
    for (i, (name, _)) in children.iter().enumerate().skip(1) {
        ok(req(&mut n, "release", "demo", &parent, json!({"kind": "port", "key": (9400 + i).to_string(), "held_by": name})));
    }
    ok(register(&mut n, "c9"));
}

#[test]
fn a_retired_subagent_is_not_live_whatever_it_holds() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, parent) = session(&mut n, "demo", "a");
    let children: Vec<(String, String)> = (0..8).map(|i| child_of(&mut n, &parent, &format!("c{i}"))).collect();
    ok(port(&mut n, &children[0].1, 9500));
    ok(req(&mut n, "retire", "demo", &children[0].1, json!({})));
    ok(req(&mut n, "register", "demo", &parent, json!({"model": "claude-haiku-5-5", "harness": "claude-code", "session": "c8"})));
}

#[test]
fn a_subagent_whose_only_claim_is_held_by_a_process_that_has_ended_is_not_live() {
    let dir = tempdir().unwrap();
    let (parent_name, parent);
    let mut sleepers: Vec<std::process::Child> = Vec::new();
    {
        let mut n = node(dir.path());
        let (name, token) = session(&mut n, "demo", "a");
        (parent_name, parent) = (name, token);
        for i in 0..8 {
            let (_, token) = child_of(&mut n, &parent, &format!("c{i}"));
            let sleeper = sleeper();
            ok(req(&mut n, "claim", "demo", &token, json!({"kind": "port", "key": (9600 + i).to_string(), "pid": sleeper.id()})));
            sleepers.push(sleeper);
        }
    }
    age_children(dir.path(), &parent_name, 2 * 3600 * 1000);
    let mut n = node(dir.path());
    let register = |n: &mut Node, id: &str| req(n, "register", "demo", &parent, json!({"model": "claude-haiku-5-5", "harness": "claude-code", "session": id}));
    assert_eq!(code(&register(&mut n, "c8")), "quota", "eight subagents whose claims are held by running processes are live");
    sleepers[0].kill().unwrap();
    sleepers[0].wait().unwrap();
    ok(register(&mut n, "c8"));
    for mut sleeper in sleepers {
        let _ = sleeper.kill();
        let _ = sleeper.wait();
    }
}
