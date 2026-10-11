//! What a removal takes with it: the entries the removed device wrote after the point its removal names, whoever relays them, and the
//! devices it admitted.

mod kit;

use kit::gov::*;
use kit::govnode::*;
use kit::netkit::*;
use std::collections::{BTreeMap, BTreeSet};

use poolhouse_node::board::Board;
use poolhouse_node::netsync::sync_all;
use poolhouse_node::sync::exchange;
use poolhouse_node::row::Kind;
use serde_json::{json, Value};

/// ``from`` syncs with ``to`` directly: boards, members and acts.
fn meet(from: &Dev, to: &Dev) {
    sync_all(&from.node, &mut link(from, to)).unwrap();
}

fn origin_of(dev: &Dev) -> String {
    dev.node.lock().unwrap().boards["demo"].board.origin().to_string()
}

fn held(dev: &Dev, origin: &str) -> usize {
    dev.node.lock().unwrap().boards["demo"].board.rows(origin).len()
}

fn entries_of(dev: &Dev, origin: &str) -> Vec<Value> {
    dev.node.lock().unwrap().boards["demo"].board.rows(origin).iter().map(|r| serde_json::to_value(r).unwrap()).collect()
}

/// Three devices of one pool with a session of their own on `demo`: c has written one entry that a and b both hold; then a removes c.
/// Returns the devices, their tokens, c's origin and how many rows of c's log a held when it removed c.
fn c_removed_by_a() -> ((Dev, Dev, Dev), [String; 3], String, usize) {
    let (a, b, c) = pool_of_three();
    let tokens = [&a, &b, &c].map(|d| d.session("demo", &format!("writer-{}", &d.fp()[..6])).1);
    c.say("demo", &tokens[2], "before the removal");
    meet(&a, &c);
    meet(&b, &c);
    let origin = origin_of(&c);
    let at_removal = held(&a, &origin);
    assert!(at_removal > 0 && held(&b, &origin) == at_removal, "a and b hold what c wrote");
    a.ok("member_remove", "", &a.token, json!({"fingerprint": c.fp()}));
    ((a, b, c), tokens, origin, at_removal)
}

#[test]
fn a_member_that_has_not_heard_of_a_removal_cannot_relay_the_removed_devices_new_entries_to_one_that_has() {
    let ((a, b, c), tokens, origin, at_removal) = c_removed_by_a();
    c.say("demo", &tokens[2], "after the removal");
    meet(&b, &c);
    assert!(held(&b, &origin) > at_removal, "b has not heard of the removal and takes what c wrote after it");
    let mut relay = link(&b, &a);
    let reply = relay.call(&json!({"op": "push", "board": "demo", "origin": origin_of(&b), "logs": {origin.clone(): entries_of(&b, &origin)}, "trusted_vector": {}})).unwrap();
    assert_eq!(reply["refused"][&origin], "invalid", "{reply}");
    assert_eq!(held(&a, &origin), at_removal, "a holds what its removal named and nothing past it");
}

#[test]
fn a_member_that_learns_of_a_removal_drops_what_the_device_wrote_after_the_point_it_names() {
    let ((a, b, c), tokens, origin, at_removal) = c_removed_by_a();
    c.say("demo", &tokens[2], "after the removal");
    meet(&b, &c);
    assert!(b.texts("demo", &tokens[1]).contains(&"after the removal".to_string()));
    meet(&b, &a);
    assert_eq!(held(&b, &origin), at_removal, "b takes the removal from a and the point it names with it");
    let texts = b.texts("demo", &tokens[1]);
    assert!(texts.contains(&"before the removal".to_string()) && !texts.contains(&"after the removal".to_string()), "{texts:?}");
    meet(&b, &a);
    assert_eq!(held(&a, &origin), at_removal);
}

#[test]
fn a_removal_names_the_logs_of_the_device_by_the_key_that_signed_them_and_not_by_what_a_caller_says() {
    let ((a, b, c), _tokens, origin, at_removal) = c_removed_by_a();
    let removal = |dev: &Dev, fp: &str| {
        let n = dev.node.lock().unwrap();
        n.members.gov().unwrap().acts().values().find(|x| x.kind == "remove" && x.body["fingerprint"] == fp).cloned().unwrap()
    };
    let c_removal = removal(&a, &c.fp());
    let tip = a.node.lock().unwrap().boards["demo"].board.rows(&origin).last().unwrap().hash.clone();
    assert!(c_removal.body["cut"].as_array().unwrap().contains(&json!({"board": "demo", "origin": origin, "seq": at_removal, "hash": tip})), "{}", c_removal.body);
    let hostile = json!({"fingerprint": b.fp(), "cut": [{"board": "demo", "origin": origin, "seq": 999_999}]});
    a.ok("pool_propose", "", &a.token, json!({"kind": "remove", "body": hostile}));
    let b_removal = removal(&a, &b.fp());
    let named: Vec<&str> = b_removal.body["cut"].as_array().unwrap().iter().map(|c| c["origin"].as_str().unwrap()).collect();
    assert!(!named.contains(&origin.as_str()), "a removal names the logs of the device it removes, not those a caller says: {}", b_removal.body);
}

#[test]
fn a_removed_device_that_starts_another_log_gets_no_entry_of_it_taken_from_any_relay() {
    let ((a, b, c), _tokens, _origin, _) = c_removed_by_a();
    let (key, pool) = {
        let n = c.node.lock().unwrap();
        (n.key.clone(), n.pool.clone())
    };
    let dir = tempfile::tempdir().unwrap();
    let mut fresh = Board::open(dir.path(), "demo", &pool, key).unwrap();
    fresh.append(Kind::Note, "mallory", json!({"nkind": "fact", "title": "t", "body": "b", "author": "mallory"}), "k1").unwrap();
    fresh.seal().unwrap();
    let rows: Vec<Value> = fresh.rows(fresh.origin()).iter().map(|r| serde_json::to_value(r).unwrap()).collect();
    let mut relay = link(&b, &a);
    let reply = relay.call(&json!({"op": "push", "board": "demo", "origin": origin_of(&b), "logs": {fresh.origin(): rows}, "trusted_vector": {}})).unwrap();
    assert_eq!(reply["refused"][fresh.origin()], "invalid", "{reply}");
    assert_eq!(held(&a, fresh.origin()), 0);
}

#[test]
fn removing_a_device_removes_the_devices_it_admitted_and_an_admin_can_admit_one_again() {
    let (_d, mut g) = pool(&[strong(1, "ann")], poolhouse_node::membership::Policy::Open);
    let (admitter, guest) = (ident(20), ident(30));
    put_admit(&mut g, &admitter, &[1]);
    let mut act = g.new_act("admit", admit_body(&guest, "guest", "open", "segment"), poolhouse_node::poolops::wall_ms()).unwrap();
    device_sign(&mut act, 20);
    take(&mut g, act);
    assert!(is_member(&g, &guest));
    put(&mut g, "remove", json!({"fingerprint": admitter.fingerprint()}), &[1]);
    assert!(is_removed(&g, &admitter) && !is_member(&g, &guest), "what the removed device admitted goes with it");
    std::thread::sleep(std::time::Duration::from_millis(5));
    put_admit(&mut g, &guest, &[1]);
    assert!(is_member(&g, &guest), "an admin's own admit brings it back");
}

fn note(board: &mut Board, text: &str) {
    board.append(Kind::Note, "bob", json!({"nkind": "fact", "title": text, "body": "b", "author": "bob"}), text).unwrap();
}

#[test]
fn a_log_of_a_removed_device_that_no_removal_names_stands_as_far_as_this_device_holds_it() {
    let (dir_a, dir_c) = (tempfile::tempdir().unwrap(), tempfile::tempdir().unwrap());
    let mut a = Board::open(dir_a.path(), "demo", "pool", kit::key(1)).unwrap();
    let mut c = Board::open(dir_c.path(), "demo", "pool", kit::key(2)).unwrap();
    note(&mut c, "one");
    exchange(&mut a, &mut c).unwrap();
    let origin = c.origin().to_string();
    let held = a.rows(&origin).len();
    assert!(held > 0);
    let writer = a.writer_fingerprint(&origin).unwrap();
    a.close_removed(BTreeSet::from([writer]), &BTreeMap::new()).unwrap();
    assert_eq!(a.cut_of(&origin), Some(held as u64));
    note(&mut c, "two");
    let report = exchange(&mut a, &mut c).unwrap();
    assert_eq!(a.rows(&origin).len(), held, "nothing it wrote later is taken");
    assert!(report.refused.contains_key(&origin), "{report:?}");
}

#[test]
fn a_cut_holds_for_a_log_even_when_this_device_does_not_know_which_key_was_removed() {
    let (dir_a, dir_c) = (tempfile::tempdir().unwrap(), tempfile::tempdir().unwrap());
    let mut a = Board::open(dir_a.path(), "demo", "pool", kit::key(1)).unwrap();
    let mut c = Board::open(dir_c.path(), "demo", "pool", kit::key(2)).unwrap();
    note(&mut c, "one");
    exchange(&mut a, &mut c).unwrap();
    let origin = c.origin().to_string();
    let held = a.rows(&origin).len() as u64;
    let tip = a.rows(&origin).last().unwrap().hash.clone();
    a.cut(&origin, held, &tip).unwrap();
    note(&mut c, "two");
    let report = exchange(&mut a, &mut c).unwrap();
    assert_eq!(a.rows(&origin).len() as u64, held, "nothing past the cut is taken, whoever the key was");
    assert!(report.refused.contains_key(&origin), "{report:?}");
}

#[test]
fn two_removals_of_one_device_that_name_different_points_stand_at_the_least() {
    let (_d, mut g) = pool(&[strong(1, "ann")], poolhouse_node::membership::Policy::Secure);
    let victim = ident(40);
    put_admit(&mut g, &victim, &[1]);
    let (origin, hash) = ("ab".repeat(16), "cd".repeat(32));
    for seq in [5u64, 3, 9] {
        put(&mut g, "remove", json!({"fingerprint": victim.fingerprint(), "cut": [{"board": "demo", "origin": origin, "seq": seq, "hash": hash}]}), &[1]);
    }
    assert_eq!(g.state().roster.log_cuts[&("demo".to_string(), origin)], (3, hash));
}

#[test]
fn a_board_opened_after_a_removal_is_closed_to_the_logs_it_names() {
    let (a, _b, c) = pool_of_three();
    let origin = "cd".repeat(16);
    let body = json!({"fingerprint": c.fp(), "cut": [{"board": "late", "origin": origin, "seq": 2, "hash": "ef".repeat(32)}]});
    let mut n = a.node.lock().unwrap();
    n.with_gov(|g, wall| {
        let mut act = g.new_act("remove", body, wall)?;
        sign(&mut act, ANN);
        g.ingest(act, wall);
        Ok(())
    }).unwrap();
    assert!(!n.boards.contains_key("late"));
    assert_eq!(n.host("late").unwrap().board.cut_of(&origin), Some(2));
}

#[test]
fn a_log_that_does_not_pass_through_the_point_a_removal_named_is_not_taken() {
    let (dir_a, dir_c) = (tempfile::tempdir().unwrap(), tempfile::tempdir().unwrap());
    let mut a = Board::open(dir_a.path(), "demo", "pool", kit::key(1)).unwrap();
    let mut c = Board::open(dir_c.path(), "demo", "pool", kit::key(2)).unwrap();
    note(&mut c, "one");
    exchange(&mut a, &mut c).unwrap();
    let origin = c.origin().to_string();
    let held = a.rows(&origin).len() as u64;
    a.cut(&origin, held, &"00".repeat(32)).unwrap();
    assert_eq!(a.rows(&origin).len(), 0, "a copy that does not reach the named point is not the log the removal saw");
    let report = exchange(&mut a, &mut c).unwrap();
    assert_eq!(a.rows(&origin).len(), 0, "and the log is not taken again from a device that holds another");
    assert!(report.refused.contains_key(&origin), "{report:?}");
}

#[test]
fn entries_up_to_the_cut_still_arrive_through_a_relay_that_has_not_heard_of_the_removal() {
    let (a, b, c) = pool_of_three();
    let d = device();
    a.join_with_code(&d);
    sync(&b);
    let tokens = [&a, &b, &c, &d].map(|x| x.session("demo", &format!("writer-{}", &x.fp()[..6])).1);
    c.say("demo", &tokens[2], "before the removal");
    meet(&a, &c);
    meet(&b, &c);
    let origin = origin_of(&c);
    let at_removal = held(&a, &origin);
    a.ok("member_remove", "", &a.token, json!({"fingerprint": c.fp()}));
    c.say("demo", &tokens[2], "after the removal");
    meet(&b, &c);
    assert!(held(&b, &origin) > at_removal, "b has not heard of the removal and takes what c wrote after it");
    poolhouse_node::gov::wire::sync_acts(&d.node, &mut link(&d, &a)).unwrap();
    assert!(d.member(&c.fp()).is_some_and(|m| m["status"] == "revoked"), "d holds the removal and the cut it carries");
    assert_eq!(held(&d, &origin), 0, "and none of c's entries");
    poolhouse_node::netsync::sync_board(&b.node, &mut link(&b, &d), "demo").unwrap();
    assert_eq!(held(&d, &origin), at_removal, "d took what c wrote up to the cut from b, which pushed all of it, and nothing past it");
    assert!(d.texts("demo", &tokens[3]).contains(&"before the removal".to_string()));
    assert!(!d.texts("demo", &tokens[3]).contains(&"after the removal".to_string()));
}
