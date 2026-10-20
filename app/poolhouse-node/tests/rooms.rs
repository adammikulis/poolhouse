mod kit;

use kit::*;
#[cfg(unix)]
use kit::procs;
use poolhouse_node::node::Node;
use poolhouse_node::rooms::{MEMBERS_PER_ROOM, MOST_ROOMS_PER_LINEAGE};
use poolhouse_node::subs::{mentions, MOST_SUBS};
use poolhouse_node::sync::exchange_nodes;
use serde_json::{json, Value};
use tempfile::tempdir;

fn room(n: &mut Node, token: &str, op: &str, name: &str, extra: Value) -> Value {
    let mut params = json!({"op": op, "room": name});
    for (k, v) in extra.as_object().unwrap() {
        params[k] = v.clone();
    }
    req(n, "room", "demo", token, params)
}

fn say(n: &mut Node, token: &str, to: &str, text: &str, extra: Value) -> Value {
    let mut fields = json!({"type": "status", "to": to, "body": text});
    for (k, v) in extra.as_object().unwrap() {
        fields[k] = v.clone();
    }
    req(n, "post", "demo", token, json!({"kind": "message", "fields": fields}))
}

fn bodies(n: &mut Node, token: &str, params: Value) -> Vec<String> {
    let got = ok(req(n, "read", "demo", token, params));
    got["entries"].as_array().unwrap().iter().filter(|e| e["kind"] == "message").map(|e| e["fields"]["body"].as_str().unwrap().to_string()).collect()
}

fn names(n: &mut Node, token: &str) -> Vec<String> {
    ok(req(n, "rooms", "demo", token, json!({})))["rooms"].as_array().unwrap().iter().map(|r| r["name"].as_str().unwrap().to_string()).collect()
}

fn kid(n: &mut Node, parent: &str, id: &str) -> (String, String) {
    let r = ok(req(n, "register", "demo", parent, json!({"model": "claude-haiku-5-5", "harness": "claude-code", "session": id})));
    (r["name"].as_str().unwrap().into(), r["token"].as_str().unwrap().into())
}

#[test]
fn a_room_is_made_by_a_session_and_its_messages_are_read_and_written_only_by_its_members() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((alice, at), (_, bt)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"));
    ok(room(&mut n, &at, "create", "#ops", json!({"title": "operations"})));
    assert_eq!(code(&room(&mut n, &bt, "create", "#ops", json!({}))), "invalid", "a room exists once");
    for bad in ["ops", "#Ops", "#general", "#dm", "#notes", "#", "#a b", "#x..y"] {
        assert_eq!(code(&room(&mut n, &at, "create", bad, json!({}))), "invalid", "{bad}");
    }
    ok(say(&mut n, &at, "#ops", "plan", json!({})));
    assert_eq!(code(&say(&mut n, &bt, "#ops", "let me in", json!({}))), "denied", "a non-member cannot write");
    assert_eq!(code(&say(&mut n, &at, "#nothere", "x", json!({}))), "denied");
    assert!(bodies(&mut n, &bt, json!({})).is_empty(), "a non-member reads nothing of the room, whatever the filter");
    assert!(bodies(&mut n, &bt, json!({"channel": "#ops"})).is_empty());
    ok(room(&mut n, &bt, "join", "#ops", json!({})));
    assert_eq!(bodies(&mut n, &bt, json!({"channel": "#ops"})), vec!["plan"], "an open room's members read it from its start");
    ok(say(&mut n, &bt, "#ops", "ack", json!({})));
    ok(room(&mut n, &bt, "leave", "#ops", json!({})));
    assert!(bodies(&mut n, &bt, json!({})).is_empty());
    assert_eq!(code(&room(&mut n, &bt, "leave", "#ops", json!({}))), "invalid");
    let listed = ok(req(&mut n, "rooms", "demo", &at, json!({})))["rooms"].clone();
    let ops = listed.as_array().unwrap().iter().find(|r| r["name"] == "#ops").unwrap().clone();
    assert_eq!((ops["created_by"].clone(), ops["title"].clone(), ops["open"].clone(), ops["posts"].clone(), ops["members"].clone()), (json!(alice), json!("operations"), json!(true), json!(2), json!([alice])));
    assert_eq!(names(&mut n, &at)[..2], ["#general", "#announcements"]);
}

#[test]
fn a_private_room_is_joined_only_through_its_makers_add_and_is_not_listed_to_others() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((_, at), (bob, bt), (_, ct)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"), session(&mut n, "demo", "c"));
    ok(room(&mut n, &at, "create", "#secret", json!({"private": true})));
    assert!(!names(&mut n, &bt).contains(&"#secret".to_string()));
    assert_eq!(code(&room(&mut n, &bt, "join", "#secret", json!({}))), "denied");
    assert_eq!(code(&room(&mut n, &bt, "add", "#secret", json!({"who": bob}))), "denied", "only the maker adds");
    assert_eq!(code(&room(&mut n, &at, "add", "#secret", json!({"who": "claude-000000"}))), "invalid", "a name nobody holds");
    ok(room(&mut n, &at, "add", "#secret", json!({"who": bob})));
    ok(say(&mut n, &at, "#secret", "for members", json!({})));
    assert_eq!(bodies(&mut n, &bt, json!({})), vec!["for members"]);
    assert!(bodies(&mut n, &ct, json!({})).is_empty());
    assert_eq!(code(&room(&mut n, &ct, "add", "#secret", json!({"who": bob}))), "denied");
    assert!(names(&mut n, &bt).contains(&"#secret".to_string()));
}

#[test]
fn a_subagent_does_not_make_join_or_subscribe() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, lead) = session(&mut n, "demo", "lead");
    let (_, child) = kid(&mut n, &lead, "kid");
    assert_eq!(code(&room(&mut n, &child, "create", "#kids", json!({}))), "denied");
    assert_eq!(code(&req(&mut n, "subscribe", "demo", &child, json!({"stype": "mentions"}))), "denied");
}

#[test]
fn the_sessions_of_one_process_tree_keep_a_bounded_number_of_rooms_between_them() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, lead) = session(&mut n, "demo", "lead");
    let (_, second) = session(&mut n, "demo", "second");
    for i in 0..MOST_ROOMS_PER_LINEAGE {
        ok(room(&mut n, &lead, "create", &format!("#r{i}"), json!({})));
    }
    for token in [&lead, &second] {
        let refused = room(&mut n, token, "create", "#one-more", json!({}));
        assert_eq!(code(&refused), "quota", "{refused}");
        let said = refused["error"]["message"].as_str().unwrap();
        assert!(said.contains(&format!("the limit is {MOST_ROOMS_PER_LINEAGE}, set by MOST_ROOMS_PER_LINEAGE in rooms.rs")), "a fresh session of the tree has no room of its own to make: {said}");
    }
}

#[cfg(unix)]
#[test]
fn another_process_tree_makes_rooms_and_a_board_holds_no_more_than_it_may() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, lead) = session(&mut n, "demo", "lead");
    for i in 0..MOST_ROOMS_PER_LINEAGE {
        ok(room(&mut n, &lead, "create", &format!("#r{i}"), json!({})));
    }
    let trees: Vec<_> = (0..14).map(|_| procs::process(true)).collect();
    let mut made = MOST_ROOMS_PER_LINEAGE;
    let mut said = String::new();
    'fill: for (t, tree) in trees.iter().enumerate() {
        let registered = ok(req_from(&mut n, &procs::peer(tree), "register", "demo", "", json!({"model": "claude-sonnet-5-5", "harness": "claude-code", "session": format!("tree{t}")})));
        let token = registered["token"].as_str().unwrap().to_string();
        for i in 0..MOST_ROOMS_PER_LINEAGE {
            let reply = room(&mut n, &token, "create", &format!("#t{t}-{i}"), json!({}));
            if reply["ok"] == false {
                assert_eq!(code(&reply), "quota", "{reply}");
                said = reply["error"]["message"].as_str().unwrap().to_string();
                break 'fill;
            }
            made += 1;
        }
    }
    assert_eq!(made, 200, "a board holds 200 rooms between all its trees");
    assert!(said.contains("the limit is 200, set by ROOMS_TOTAL in rooms.rs"), "{said}");
    procs::done(trees);
}

#[test]
fn a_room_holds_no_more_members_than_it_may() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    n.writes.live_main_sessions = MEMBERS_PER_ROOM + 2;
    let (_, lead) = session(&mut n, "demo", "lead");
    ok(room(&mut n, &lead, "create", "#crowd", json!({})));
    let mut refused = Value::Null;
    for i in 1..MEMBERS_PER_ROOM + 2 {
        let (_, token) = session(&mut n, "demo", &format!("m{i}"));
        let reply = room(&mut n, &token, "join", "#crowd", json!({}));
        if reply["ok"] == false {
            refused = reply;
            assert_eq!(i, MEMBERS_PER_ROOM, "the maker is the first member");
            break;
        }
    }
    assert_eq!(code(&refused), "quota", "{refused}");
    assert!(refused["error"]["message"].as_str().unwrap().contains(&format!("the limit is {MEMBERS_PER_ROOM}, set by MEMBERS_PER_ROOM in rooms.rs")), "{refused}");
}

#[test]
fn a_room_is_the_same_on_every_device_and_a_reply_stays_in_its_room() {
    let (da, db) = (tempdir().unwrap(), tempdir().unwrap());
    let (mut a, mut b) = (node(da.path()), node(db.path()));
    let ((_, at), (_, bt)) = (session(&mut a, "demo", "alice"), session(&mut b, "demo", "bob"));
    ok(room(&mut a, &at, "create", "#ops", json!({})));
    let first = ok(say(&mut a, &at, "#ops", "from alice", json!({})));
    exchange_nodes(&mut a, &mut b).unwrap();
    ok(room(&mut b, &bt, "join", "#ops", json!({})));
    ok(say(&mut b, &bt, "#ops", "from bob", json!({"reply_id": first["id"]})));
    exchange_nodes(&mut a, &mut b).unwrap();
    assert_eq!(bodies(&mut a, &at, json!({"channel": "#ops"})), vec!["from alice", "from bob"]);
    assert_eq!(bodies(&mut b, &bt, json!({"channel": "#ops"})), vec!["from alice", "from bob"]);
    ok(room(&mut a, &at, "create", "#else", json!({})));
    assert_eq!(code(&say(&mut a, &at, "#else", "elsewhere", json!({"reply_id": first["id"]}))), "invalid", "a reply stays in the room of the message it answers");
}

#[test]
fn a_reply_names_a_message_of_its_own_room() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, at) = session(&mut n, "demo", "a");
    ok(room(&mut n, &at, "create", "#one", json!({})));
    ok(room(&mut n, &at, "create", "#two", json!({})));
    let one = ok(say(&mut n, &at, "#one", "in one", json!({})))["seq"].to_string();
    assert_eq!(code(&say(&mut n, &at, "#two", "wrong room", json!({"reply_id": one}))), "invalid");
    ok(say(&mut n, &at, "#one", "right room", json!({"reply_id": one})));
}

#[test]
fn a_subscription_delivers_only_what_arrives_after_it_in_its_mode() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((_, at), (bob, bt)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"));
    ok(room(&mut n, &at, "create", "#ops", json!({})));
    ok(room(&mut n, &bt, "join", "#ops", json!({})));
    ok(say(&mut n, &at, "#ops", "before", json!({})));
    let got = ok(req(&mut n, "subscribe", "demo", &bt, json!({"stype": "board", "target": "#ops", "mode": "inbox"})));
    assert_eq!((got["type"].clone(), got["target"].clone(), got["mode"].clone()), (json!("board"), json!("#ops"), json!("inbox")));
    ok(say(&mut n, &at, "#ops", "after", json!({})));
    ok(say(&mut n, &at, "#general", "unsubscribed", json!({})));
    ok(say(&mut n, &at, &bob, "direct", json!({})));
    assert_eq!(bodies(&mut n, &bt, json!({"inbox": true})), vec!["after", "direct"], "the room after the subscription, and what is addressed to the session");
    ok(req(&mut n, "subscribe", "demo", &bt, json!({"stype": "board", "target": "#ops", "mode": "digest"})));
    ok(say(&mut n, &at, "#ops", "later", json!({})));
    assert_eq!(bodies(&mut n, &bt, json!({"inbox": true})), vec!["direct"], "digest mode never enters the inbox");
    assert_eq!(bodies(&mut n, &bt, json!({"digest": true})), vec!["later"]);
    ok(req(&mut n, "subscribe", "demo", &bt, json!({"stype": "board", "target": "#ops", "mode": "silent"})));
    assert!(bodies(&mut n, &bt, json!({"digest": true})).is_empty());
    let subs = ok(req(&mut n, "subs", "demo", &bt, json!({})))["subs"].clone();
    assert_eq!(subs, json!([{"type": "board", "target": "#ops", "mode": "silent"}]));
    ok(req(&mut n, "unsubscribe", "demo", &bt, json!({"stype": "board", "target": "#ops"})));
    assert_eq!(code(&req(&mut n, "unsubscribe", "demo", &bt, json!({"stype": "board", "target": "#ops"}))), "invalid");
}

#[test]
fn a_mention_reaches_the_inbox_by_default_and_an_announcement_the_digest() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((_, at), (bob, bt)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"));
    ok(say(&mut n, &at, "#general", &format!("hello @{bob}, a question"), json!({})));
    ok(say(&mut n, &at, "#general", &format!("not @{bob}x"), json!({})));
    ok(say(&mut n, &at, "#general", "nobody", json!({})));
    ok(req(&mut n, "post", "demo", &at, json!({"kind": "message", "fields": {"type": "milestone", "to": "#announcements", "body": "landed", "subject": "milestone"}})));
    assert_eq!(bodies(&mut n, &bt, json!({"inbox": true})), vec![format!("hello @{bob}, a question")]);
    assert_eq!(bodies(&mut n, &bt, json!({"digest": true})), vec!["landed"]);
    assert_eq!(bodies(&mut n, &bt, json!({"mentions": true})), vec![format!("hello @{bob}, a question")]);
    assert!(bodies(&mut n, &at, json!({"mentions": true})).is_empty());
    ok(req(&mut n, "subscribe", "demo", &bt, json!({"stype": "mentions", "mode": "digest"})));
    assert!(bodies(&mut n, &bt, json!({"inbox": true})).is_empty(), "a mention in digest mode leaves the inbox");
    ok(req(&mut n, "subscribe", "demo", &bt, json!({"stype": "board", "target": "#announcements", "mode": "silent"})));
    assert!(bodies(&mut n, &bt, json!({"digest": true})).iter().all(|b| !b.contains("landed")));
    assert_eq!(code(&req(&mut n, "subscribe", "demo", &bt, json!({"stype": "board", "target": "#announcements", "mode": "inbox"}))), "invalid");
}

#[test]
fn subscribing_to_threads_senders_and_kinds_checks_the_target_and_counts_the_cost() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((alice, at), (_, bt), (_, ct)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"), session(&mut n, "demo", "c"));
    let root = ok(say(&mut n, &at, "#general", "topic", json!({})))["seq"].to_string();
    let sub = |n: &mut Node, token: &str, stype: &str, target: &str, extra: Value| {
        let mut p = json!({"stype": stype, "target": target, "mode": "inbox"});
        for (k, v) in extra.as_object().unwrap() {
            p[k] = v.clone();
        }
        req(n, "subscribe", "demo", token, p)
    };
    assert_eq!(code(&sub(&mut n, &bt, "thread", "999", json!({}))), "invalid");
    assert_eq!(code(&sub(&mut n, &bt, "agent", "claude-000000", json!({}))), "invalid");
    assert_eq!(code(&sub(&mut n, &bt, "kind", "gossip", json!({}))), "invalid");
    assert_eq!(code(&sub(&mut n, &bt, "board", "#nothere", json!({}))), "denied");
    assert_eq!(code(&sub(&mut n, &bt, "color", "x", json!({}))), "invalid");
    ok(sub(&mut n, &bt, "thread", &root, json!({})));
    ok(sub(&mut n, &bt, "agent", &alice, json!({})));
    ok(sub(&mut n, &bt, "kind", "question", json!({})));
    assert_eq!(code(&sub(&mut n, &bt, "kind", "task", json!({}))), "quota", "three loud subscriptions are free");
    let forced = ok(sub(&mut n, &bt, "kind", "task", json!({"force": true})));
    assert!(forced["cost"].as_str().unwrap().contains("forced"));
    ok(say(&mut n, &at, "#general", "a reply", json!({"reply_id": root})));
    ok(say(&mut n, &at, "#general", "a question", json!({"type": "question"})));
    assert_eq!(bodies(&mut n, &bt, json!({"inbox": true})), vec!["a reply", "a question"], "the thread, then the sender; each entry once");
    assert!(bodies(&mut n, &ct, json!({"inbox": true})).is_empty());
    for kind in ["task", "status", "handoff", "question", "answer", "claim", "release", "note", "file"] {
        ok(req(&mut n, "subscribe", "demo", &ct, json!({"stype": "kind", "target": kind, "mode": "digest"})));
    }
    ok(req(&mut n, "subscribe", "demo", &ct, json!({"stype": "mentions", "mode": "digest"})));
    ok(req(&mut n, "subscribe", "demo", &ct, json!({"stype": "board", "target": "#general", "mode": "digest"})));
    ok(req(&mut n, "subscribe", "demo", &ct, json!({"stype": "board", "target": "#announcements", "mode": "silent"})));
    ok(req(&mut n, "subscribe", "demo", &ct, json!({"stype": "thread", "target": root, "mode": "digest"})));
    assert_eq!(ok(req(&mut n, "subs", "demo", &ct, json!({})))["subs"].as_array().unwrap().len(), 13, "a session keeps more than twelve subscriptions");
}

#[test]
fn head_returns_the_cursor_of_now_without_entries() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, at) = session(&mut n, "demo", "a");
    ok(say(&mut n, &at, "#general", "one", json!({})));
    let head = ok(req(&mut n, "read", "demo", &at, json!({"head": true})));
    assert!(head["entries"].as_array().unwrap().is_empty());
    ok(say(&mut n, &at, "#general", "two", json!({})));
    let after = ok(req(&mut n, "read", "demo", &at, json!({"since": head["cursor"]})));
    assert_eq!(bodies(&mut n, &at, json!({"since": head["cursor"]})), vec!["two"]);
    assert!(after["cursor"].as_object().unwrap().len() == 1);
}

#[test]
fn a_mention_is_a_whole_name_after_an_at_sign() {
    assert!(mentions("ping @bob please", "bob"));
    assert!(mentions("@bob", "bob") && mentions("hi @bob.", "bob") && mentions("(@bob)", "bob"));
    assert!(!mentions("a@bob.example", "bob"), "an address is not a mention");
    assert!(!mentions("@bobby", "bob") && !mentions("@bob/child", "bob") && !mentions("@@bob", "bob") && !mentions("no one", "bob"));
}

#[test]
fn a_foreign_message_to_a_room_nobody_made_or_a_member_forged_is_read_by_no_one() {
    let (da, db) = (tempdir().unwrap(), tempdir().unwrap());
    let (mut a, mut b) = (node(da.path()), node(db.path()));
    let (_, bt) = session(&mut b, "demo", "b");
    let evil = a.host("demo").unwrap();
    let body = |to: &str, text: &str| json!({"type": "status", "from": "mallory", "to": to, "subject": "", "body": text});
    evil.board.append(poolhouse_node::row::Kind::Message, "mallory", body("#nobody", "to a room that was never made"), "").unwrap();
    evil.board.append(poolhouse_node::row::Kind::Room, "mallory", json!({"op": "create", "room": "#mine", "title": "", "open": false}), "").unwrap();
    evil.board.append(poolhouse_node::row::Kind::Room, "mallory", json!({"op": "add", "room": "#mine", "who": "someone"}), "").unwrap();
    evil.board.append(poolhouse_node::row::Kind::Message, "mallory", body("#mine", "to mallory's private room"), "").unwrap();
    exchange_nodes(&mut a, &mut b).unwrap();
    assert!(bodies(&mut b, &bt, json!({})).is_empty());
    let join = room(&mut b, &bt, "join", "#mine", json!({}));
    assert_eq!(code(&join), "denied", "a private room of another device is not joined");
    let other = ok(req(&mut b, "rooms", "demo", &bt, json!({})))["rooms"].as_array().unwrap().iter().map(|r| r["name"].clone()).collect::<Vec<_>>();
    assert!(!other.contains(&json!("#mine")));
}

fn watch(n: &mut Node, token: &str, agent: &str) -> Value {
    req(n, "subscribe", "demo", token, json!({"stype": "agent", "target": agent, "mode": "inbox", "force": true}))
}

fn kids_watched(n: &mut Node, most: usize) -> (String, Vec<(String, String)>) {
    let (_, lead) = session(n, "demo", "lead");
    n.boards.get_mut("demo").unwrap().subs.most = most;
    let kids: Vec<(String, String)> = (0..most + 1).map(|i| kid(n, &lead, &format!("kid{i}"))).collect();
    for (name, _) in &kids[..most] {
        ok(watch(n, &lead, name));
    }
    (lead, kids)
}

#[test]
fn the_default_bound_on_the_subscriptions_of_a_session_is_the_constant() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    session(&mut n, "demo", "lead");
    assert_eq!((MOST_SUBS, n.boards["demo"].subs.most), (256, 256));
}

#[test]
fn a_session_keeps_a_bounded_number_of_subscriptions_and_the_refusal_names_the_limit() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (lead, kids) = kids_watched(&mut n, 6);
    let refused = watch(&mut n, &lead, &kids[6].0);
    assert_eq!(code(&refused), "quota", "{refused}");
    assert!(refused["error"]["message"].as_str().unwrap().contains("the limit is 6, set by MOST_SUBS in subs.rs"), "{refused}");
    ok(watch(&mut n, &lead, &kids[0].0));
}

#[test]
fn a_full_set_of_subscriptions_drops_the_ones_to_subagents_that_ended_before_it_refuses() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (lead, kids) = kids_watched(&mut n, 6);
    for (name, _) in &kids[..2] {
        ok(req(&mut n, "retire", "demo", &lead, json!({"target": name})));
    }
    ok(watch(&mut n, &lead, &kids[6].0));
    let held = ok(req(&mut n, "subs", "demo", &lead, json!({})))["subs"].as_array().unwrap().len();
    assert_eq!(held, 5, "the two ended subagents were dropped to make room");
}
