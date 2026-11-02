//! What a session, a lead with its subagents and a process tree can do to a node's bounds, and what an honest session still gets
//! afterwards: ending and registering again, a tree's share of a log, a person's decisions on a full log, the boards a tree makes,
//! the leases a tree keeps, and the subscriptions of a session that ended.
#![cfg(unix)]

mod kit;

use kit::phone::{ask, FakePhone, Rig};
use kit::procs::{done, orphan, peer, process};
use kit::*;
use poolhouse_node::lineage::Peer;
use poolhouse_node::logbounds::Bounds;
use poolhouse_node::node::{Node, MOST_BOARDS_PER_LINEAGE};
use poolhouse_node::row::{Kind, Row};
use serde_json::{json, Value};
use std::path::Path;
use tempfile::tempdir;

/// A node whose boards hold eighty rows, eight of them for the node's own, a quarter for one process tree and a tenth for one session.
fn small(dir: &Path, tweak: impl FnOnce(&mut Bounds)) -> Node {
    let mut n = node(dir);
    let mut bounds = Bounds { rows: 80, bytes: 1 << 24, reserved_rows: 8, reserved_bytes: 1 << 10, last_rows: 2, last_bytes: 1 << 9, author_percent: 10, lineage_percent: 25 };
    tweak(&mut bounds);
    n.log_bounds = bounds;
    n
}

fn register_from(n: &mut Node, from: &Peer, board: &str, parent: &str, session: &str) -> Value {
    req_from(n, from, "register", board, parent, json!({"model": "claude-sonnet-5-5", "harness": "claude-code", "session": session}))
}

fn token_of(reply: &Value) -> String {
    assert_eq!(reply["ok"], true, "{reply}");
    reply["result"]["token"].as_str().unwrap().to_string()
}

fn rows_of(n: &mut Node, board: &str) -> Vec<Row> {
    let hosted = n.host(board).unwrap();
    hosted.board.rows(hosted.board.origin()).to_vec()
}

fn said(reply: &Value) -> String {
    reply["error"]["message"].as_str().unwrap_or("").to_string()
}

/// Post as ``token`` until the log refuses a row for the whole log; how many rows it holds then.
fn fill(n: &mut Node, board: &str, token: &str) -> usize {
    for i in 0..500 {
        let reply = post(n, board, token, &format!("fill {i}"));
        if reply["ok"] == false {
            assert!(said(&reply).contains("MAX_LOG_ROWS"), "{reply}");
            return rows_of(n, board).len();
        }
    }
    panic!("the log never filled");
}

#[test]
fn one_session_ending_and_registering_again_is_stopped_at_its_share_and_the_log_stays_whole_for_the_others() {
    let dir = tempdir().unwrap();
    let mut n = small(dir.path(), |_| {});
    let cycler = process(true);
    let honest = token_of(&register_from(&mut n, &me(), "demo", "", "honest"));
    let (mut name, mut stopped) = (String::new(), None);
    for _ in 0..100 {
        let reply = register_from(&mut n, &peer(&cycler), "demo", "", "cycler");
        if reply["ok"] != true {
            stopped = Some(reply);
            break;
        }
        name = reply["result"]["name"].as_str().unwrap().to_string();
        ok(req_from(&mut n, &peer(&cycler), "retire", "demo", &token_of(&reply), json!({"target": "", "reason": "done"})));
    }
    let stopped = stopped.expect("registering again and again is stopped");
    assert_eq!(code(&stopped), "quota", "{stopped}");
    assert!(said(&stopped).contains("AUTHOR_SHARE_PERCENT"), "{stopped}");
    assert_eq!(n.host("demo").unwrap().names.get(&name).map(|i| i.retired), Some(true), "a registration the log refused leaves the session as it was: ended");
    let all = rows_of(&mut n, "demo");
    assert!(all.len() <= 20, "the cycling took a quarter of the log at most: {} rows", all.len());
    let endings = all.iter().filter(|r| r.kind == Kind::Identity && r.body["name"] == json!(name) && r.body["retired"] == json!(true) && r.body["retired_by"] == json!(name)).count();
    assert!(endings >= 1, "a session's ending is an identity entry that names who ended it");
    assert!(all.iter().all(|r| !(r.kind == Kind::Audit && r.body["event"] == "retire")), "and no audit entry besides it");
    ok(post(&mut n, "demo", &honest, "still here"));
    assert!(n.host("demo").unwrap().board.seal().is_ok(), "the log can still be signed");
    ok(req_from(&mut n, &me(), "retire", "demo", &honest, json!({"target": "", "reason": "done"})));
    done(vec![cycler]);
}

#[test]
fn a_lead_and_its_subagents_write_one_trees_share_of_the_log_between_them_and_another_tree_still_writes() {
    let dir = tempdir().unwrap();
    let mut n = small(dir.path(), |b| b.author_percent = 25);
    let lead = token_of(&register_from(&mut n, &me(), "demo", "", "lead"));
    let mut sessions = vec![lead.clone()];
    for i in 0..4 {
        sessions.push(token_of(&register_from(&mut n, &me(), "demo", &lead, &format!("kid{i}"))));
    }
    let mut refusal = Value::Null;
    for round in 0..40 {
        for token in &sessions {
            let reply = post(&mut n, "demo", token, &format!("round {round}"));
            if reply["ok"] == false {
                refusal = reply;
            }
        }
    }
    assert_eq!(code(&refusal), "quota", "{refusal}");
    assert!(said(&refusal).contains("LINEAGE_SHARE_PERCENT"), "the tree is told it used its share: {refusal}");
    let held = rows_of(&mut n, "demo").len();
    assert!(held <= 22, "five sessions of one tree hold a quarter of eighty rows, not eighty: {held}");
    let refused = register_from(&mut n, &me(), "demo", &lead, "one-more");
    assert_eq!(code(&refused), "quota", "a tree that used its share registers no more subagents: {refused}");
    assert!(n.host("demo").unwrap().names.find("claude-code", "one-more").is_none(), "and the refused registration left no name behind");
    let other = process(true);
    let theirs = token_of(&register_from(&mut n, &peer(&other), "demo", "", "other"));
    for i in 0..10 {
        ok(post(&mut n, "demo", &theirs, &format!("mine {i}")));
    }
    done(vec![other]);
}

#[test]
fn a_persons_grant_and_its_revocation_are_written_when_sessions_have_filled_the_log() {
    let dir = tempdir().unwrap();
    let mut n = small(dir.path(), |b| (b.author_percent, b.lineage_percent) = (100, 100));
    let (name, token) = session(&mut n, "demo", "agent");
    let before = fill(&mut n, "demo", &token);
    allow(&mut n, "demo", &token, &name, &["source_add"]);
    revoke(&mut n, "demo", &token, &name, "source_add");
    assert!(rows_of(&mut n, "demo").len() > before, "the person's two rows are in the log");
    let again = req(&mut n, "grant_revoke", "demo", &token, json!({"gid": "none"}));
    assert_eq!(code(&again), "invalid", "{again}");
}

#[test]
fn a_persons_approval_on_the_phone_writes_its_grants_when_the_log_is_full_for_sessions() {
    let rig = Rig::new();
    rig.node.lock().unwrap().log_bounds = Bounds { rows: 80, bytes: 1 << 24, reserved_rows: 8, reserved_bytes: 1 << 10, last_rows: 2, last_bytes: 1 << 9, author_percent: 100, lineage_percent: 100 };
    let mut phone = FakePhone::new("Pixel");
    rig.add(&mut phone, None);
    let (name, token) = rig.session("demo", "agent-1");
    let made = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({"note": "adding the Linux box", "task": ""})));
    fill(&mut rig.node.lock().unwrap(), "demo", &token);
    let seen = rig.get(&phone, made["id"].as_str().unwrap());
    let (status, reply) = rig.phone("POST", "decide", "", phone.decision(&seen, "approve"));
    assert_eq!((status, &reply["ok"]), (200, &json!(true)), "{reply}");
    let listing = ok(rig.call("grants", "demo", &token, json!({})));
    assert!(listing["grants"].as_array().unwrap().iter().any(|g| g["carried_by"] == "node" && g["to"]["name"] == json!(name)), "{listing}");
}

fn word(mut n: usize) -> String {
    let mut s = String::from("b");
    loop {
        s.push((b'a' + (n % 26) as u8) as char);
        n /= 26;
        if n == 0 {
            return s;
        }
    }
}

#[test]
fn a_process_tree_that_makes_and_ends_boards_is_stopped_at_its_share_of_them_and_another_tree_still_makes_one() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let attacker = process(true);
    let (mut made, mut stopped) = (0, None);
    'rounds: for round in 0..10 {
        let mut tokens = Vec::new();
        for i in 0..MOST_BOARDS_PER_LINEAGE {
            let board = word(round * MOST_BOARDS_PER_LINEAGE + i);
            let reply = register_from(&mut n, &peer(&attacker), &board, "", "s");
            if reply["ok"] != true {
                stopped = Some(reply);
                break 'rounds;
            }
            tokens.push((board, token_of(&reply)));
            made += 1;
        }
        for (board, token) in tokens {
            ok(req_from(&mut n, &peer(&attacker), "retire", &board, &token, json!({"target": "", "reason": "done"})));
        }
    }
    let stopped = stopped.expect("the tree is stopped");
    assert_eq!(made, MOST_BOARDS_PER_LINEAGE, "a tree makes its share of the boards however many sessions it ends");
    assert!(said(&stopped).contains("MOST_BOARDS_PER_LINEAGE"), "{stopped}");
    assert!(n.boards.len() <= MOST_BOARDS_PER_LINEAGE, "{} boards", n.boards.len());
    let honest = register_from(&mut n, &me(), "honest", "", "h");
    assert_eq!(honest["ok"], true, "{honest}");
    done(vec![attacker]);
}

#[test]
fn a_board_that_holds_only_the_ends_of_its_sessions_is_removed_when_its_tree_needs_room_and_no_other_is() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    n.board_grace_ms = 0;
    let tree = process(true);
    let mut tokens = Vec::new();
    for i in 0..MOST_BOARDS_PER_LINEAGE {
        tokens.push((format!("b{i}"), token_of(&register_from(&mut n, &peer(&tree), &format!("b{i}"), "", "s"))));
    }
    ok(post(&mut n, "b0", &tokens[0].1, "a message is content"));
    n.host("b1").unwrap().board.bind_peer("another-device", "feedfeedfeedfeed").unwrap();
    for (board, token) in tokens.iter().skip(1).filter(|(b, _)| b != "b2") {
        ok(req_from(&mut n, &peer(&tree), "retire", board, token, json!({"target": "", "reason": "done"})));
    }
    ok(req_from(&mut n, &peer(&tree), "retire", "b0", &tokens[0].1, json!({"target": "", "reason": "done"})));
    let next = register_from(&mut n, &peer(&tree), "next", "", "s");
    assert_eq!(next["ok"], true, "ended boards with nothing in them make room: {next}");
    for kept in ["b0", "b1", "b2", "next"] {
        assert!(n.boards.contains_key(kept), "{kept} holds a message, was shared with another device, or has a live session: it stays");
    }
    for gone in ["b3", "b9", "b31"] {
        assert!(!n.boards.contains_key(gone), "{gone} held only the end of its session");
        assert!(!dir.path().join("boards").join(gone).exists(), "{gone} left its folder");
    }
    let again = register_from(&mut n, &peer(&tree), "b3", "", "s");
    assert_eq!(again["ok"], true, "a removed board is made again from nothing: {again}");
    done(vec![tree]);
}

fn lease_node(dir: &Path, per_holder: usize, per_tree: usize) -> Node {
    let mut n = node(dir);
    n.leases.cfg.max_leases_per_holder = per_holder;
    n.leases.cfg.max_leases_per_lineage = per_tree;
    n
}

fn reserve_ports(n: &mut Node, from: &Peer, token: &str, first: usize, count: usize) -> Value {
    let list: Vec<Value> = (0..count).map(|i| json!({"kind": "port", "key": format!("{}", first + i)})).collect();
    req_from(n, from, "reserve", "demo", token, json!({"resources": list}))
}

#[test]
fn the_sessions_of_one_process_tree_keep_a_bounded_number_of_leases_between_them_and_another_tree_keeps_its_own() {
    let dir = tempdir().unwrap();
    let mut n = lease_node(dir.path(), 8, 20);
    let lead = token_of(&register_from(&mut n, &me(), "demo", "", "lead"));
    let kids: Vec<String> = (0..3).map(|i| token_of(&register_from(&mut n, &me(), "demo", &lead, &format!("kid{i}")))).collect();
    ok(reserve_ports(&mut n, &me(), &lead, 9000, 8));
    ok(reserve_ports(&mut n, &me(), &kids[0], 9100, 8));
    let refused = reserve_ports(&mut n, &me(), &kids[1], 9200, 8);
    assert_eq!(code(&refused), "quota", "{refused}");
    assert!(said(&refused).contains("max_leases_per_lineage") && said(&refused).contains("the limit is 20"), "{refused}");
    ok(reserve_ports(&mut n, &me(), &kids[1], 9200, 4));
    let other = process(true);
    let theirs = token_of(&register_from(&mut n, &peer(&other), "demo", "", "other"));
    ok(reserve_ports(&mut n, &peer(&other), &theirs, 9300, 8));
    done(vec![other]);
}

#[test]
fn a_reserve_of_as_many_resources_as_a_session_may_hold_holds_the_node_for_a_short_time() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let token = token_of(&register_from(&mut n, &me(), "demo", "", "reserver"));
    let most = n.leases.cfg.max_leases_per_holder;
    let started = std::time::Instant::now();
    ok(reserve_ports(&mut n, &me(), &token, 10_000, most));
    let held = started.elapsed();
    assert!(held < std::time::Duration::from_millis(1000), "one reserve held the node for {held:?}");
}

#[test]
fn ending_a_session_drops_its_subscriptions() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let made = register_from(&mut n, &me(), "demo", "", "reader");
    let token = token_of(&made);
    let name = made["result"]["name"].as_str().unwrap().to_string();
    ok(req(&mut n, "subscribe", "demo", &token, json!({"stype": "mentions", "target": "", "mode": "digest"})));
    ok(req(&mut n, "subscribe", "demo", &token, json!({"stype": "kind", "target": "task", "mode": "digest"})));
    assert_eq!(n.host("demo").unwrap().subs.of(&name).len(), 2);
    ok(req(&mut n, "retire", "demo", &token, json!({"target": "", "reason": "done"})));
    assert!(n.host("demo").unwrap().subs.of(&name).is_empty(), "a session that ended keeps no subscription");
    let saved = std::fs::read_to_string(dir.path().join("boards/demo/subs.json")).unwrap();
    assert!(!saved.contains(&name), "and none is left in the file: {saved}");
}

#[test]
fn the_sessions_of_one_tree_that_write_to_a_linked_board_share_the_trees_part_of_its_log() {
    let dir = tempdir().unwrap();
    let mut n = small(dir.path(), |b| b.author_percent = 25);
    let made = register_from(&mut n, &me(), "away", "", "owner");
    let (owner, owner_token) = (made["result"]["name"].as_str().unwrap().to_string(), token_of(&made));
    allow(&mut n, "away", &owner_token, &owner, &["link_write"]);
    register_from(&mut n, &me(), "home", "", "first");
    ok(req(&mut n, "link", "away", &owner_token, json!({"to": "home", "channels": [], "mode": "rw"})));
    let lead = token_of(&register_from(&mut n, &me(), "home", "", "lead"));
    let kid = token_of(&register_from(&mut n, &me(), "home", &lead, "kid"));
    for round in 0..30 {
        for token in [&lead, &kid] {
            post(&mut n, "away", token, &format!("round {round}"));
        }
    }
    let linked = rows_of(&mut n, "away").iter().filter(|r| r.actor.contains('@')).count();
    assert!(linked <= 20, "the lead and its subagent write a quarter of eighty rows between them to a board they reach by a link, not a quarter each: {linked}");
    assert!(linked >= 10, "and they did write: {linked}");
}

#[test]
fn the_sessions_of_one_tree_that_link_a_board_to_another_share_the_trees_part_of_the_log_it_audits_into() {
    let dir = tempdir().unwrap();
    let mut n = small(dir.path(), |b| b.author_percent = 15);
    let owned = register_from(&mut n, &me(), "home", "", "owner");
    let (owner, owner_token) = (owned["result"]["name"].as_str().unwrap().to_string(), token_of(&owned));
    allow(&mut n, "home", &owner_token, &owner, &["link_write"]);
    let made = register_from(&mut n, &me(), "away", "", "lead");
    let lead_token = token_of(&made);
    ok(req(&mut n, "link", "home", &owner_token, json!({"to": "away", "channels": [], "mode": "rw"})));
    for i in 0..12 {
        ok(post(&mut n, "home", &lead_token, &format!("the lead writes {i}")));
    }
    let writer = token_of(&register_from(&mut n, &me(), "away", &lead_token, "writer"));
    for i in 0..4 {
        ok(post(&mut n, "home", &writer, &format!("a subagent writes {i}")));
    }
    let mut kids = Vec::new();
    for i in 0..2 {
        let kid = register_from(&mut n, &me(), "away", &lead_token, &format!("kid{i}"));
        let (name, token) = (kid["result"]["name"].as_str().unwrap().to_string(), token_of(&kid));
        allow(&mut n, "away", &token, &name, &["link_write", "link_remove"]);
        kids.push(token);
    }
    for _ in 0..6 {
        for token in &kids {
            let made = req(&mut n, "link", "away", token, json!({"to": "home", "channels": [], "mode": "rw"}));
            if let Some(id) = made["result"]["id"].as_str() {
                req(&mut n, "unlink", "away", token, json!({"id": id}));
            }
        }
    }
    let rows = rows_of(&mut n, "home");
    let written = rows.iter().filter(|r| r.kind == Kind::Message && r.actor.contains('@')).count();
    let audits = rows.iter().filter(|r| r.kind == Kind::Audit && r.actor.contains('@')).count();
    assert_eq!(written, 16, "the lead and a subagent wrote messages through the link");
    assert_eq!(audits, 0, "the tree had written its quarter of eighty rows by then, so the audit of a link a subagent made into the board is refused like any row of the tree's");
}

#[test]
fn a_board_holds_a_bounded_number_of_live_main_sessions_however_many_trees_they_come_from_and_an_ending_frees_a_place() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let most = n.writes.live_main_sessions;
    let daemons: Vec<u32> = (0..most + 2).map(|_| orphan()).collect();
    let from = |i: usize| Peer { pid: Some(daemons[i]) };
    let mut tokens = Vec::new();
    for i in 0..most {
        tokens.push(token_of(&register_from(&mut n, &from(i), "demo", "", &format!("daemon{i}"))));
    }
    let refused = register_from(&mut n, &from(most), "demo", "", "one-more");
    assert_eq!(code(&refused), "quota", "{refused}");
    assert!(said(&refused).contains(&format!("the limit is {most}, set by live_main_sessions in write-limits.json")), "{refused}");
    assert_eq!(n.host("demo").unwrap().names.all().len(), most, "a registration that was refused leaves no session behind");
    assert_eq!(register_from(&mut n, &from(0), "demo", "", "daemon0")["ok"], true, "a live session registering again is the one it was");
    let kids: Vec<Value> = (0..40).map(|i| register_from(&mut n, &from(0), "demo", &tokens[0], &format!("kid{i}"))).collect();
    assert!(kids.iter().all(|k| k["ok"] == true), "subagents are not main sessions and the bound does not count them");
    ok(req_from(&mut n, &from(1), "retire", "demo", &tokens[1], json!({"target": "", "reason": "done"})));
    let taken = register_from(&mut n, &from(most), "demo", "", "one-more");
    assert_eq!(taken["ok"], true, "an ending frees a place: {taken}");
    let again = register_from(&mut n, &from(1), "demo", "", "daemon1");
    assert_eq!(code(&again), "quota", "a main session that ended comes back only into a free place: {again}");
    daemons.into_iter().for_each(kit::kill_hard);
}
