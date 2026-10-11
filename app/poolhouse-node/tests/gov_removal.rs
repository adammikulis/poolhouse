//! What a removal takes with it: the entries the removed device wrote after the point its removal names, whoever relays them, and the
//! devices it admitted.

mod kit;

use kit::gov::*;
use kit::govnode::*;
use kit::netkit::*;
use poolhouse_node::board::Board;
use poolhouse_node::netsync::sync_all;
use poolhouse_node::row::Kind;
use serde_json::{json, Value};

/// ``from`` syncs with ``to`` directly: boards, members and acts.
fn pair(from: &Dev, to: &Dev) {
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
    pair(&a, &c);
    pair(&b, &c);
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
    pair(&b, &c);
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
    pair(&b, &c);
    assert!(b.texts("demo", &tokens[1]).contains(&"after the removal".to_string()));
    pair(&b, &a);
    assert_eq!(held(&b, &origin), at_removal, "b takes the removal from a and the point it names with it");
    let texts = b.texts("demo", &tokens[1]);
    assert!(texts.contains(&"before the removal".to_string()) && !texts.contains(&"after the removal".to_string()), "{texts:?}");
    pair(&b, &a);
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
    assert!(c_removal.body["cut"].as_array().unwrap().contains(&json!({"board": "demo", "origin": origin, "seq": at_removal})), "{}", c_removal.body);
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
