//! What a device is told when a member refuses it at the handshake: the reason a person can act on, not a bare TLS alert.

mod kit;

use kit::netkit::*;
use serde_json::json;

#[test]
fn a_device_put_out_of_the_pool_is_told_so_when_it_next_dials() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    b.net.sync_once(true).unwrap();
    a.ok("member_remove", "", &a.token, json!({"fingerprint": b.fp()}));
    b.net.sync_once(true).unwrap();
    let error = b.member(&a.fp()).unwrap()["last_error"].as_str().unwrap().to_string();
    assert!(error.contains("put this device out of the pool"), "{error}");
}

#[test]
fn a_peer_that_refuses_our_log_is_named_in_the_member_listing_after_a_sync_that_otherwise_worked() {
    use poolhouse_node::peer::PeerClient;
    use serde_json::Value;

    let (a, b) = (device(), device());
    a.join_with_code(&b);
    let (_, at) = a.session("demo", "writer-a");
    a.say("demo", &at, "genuine");
    a.say("demo", &at, "second");
    b.net.sync_once(true).unwrap();
    let origin = a.node.lock().unwrap().boards["demo"].board.origin().to_string();
    let mut rows: Vec<Value> = a.node.lock().unwrap().boards["demo"].board.trusted(&origin).iter().map(|r| serde_json::to_value(r).unwrap()).collect();
    let last = rows.len() - 1;
    rows[last]["hash"] = json!("0".repeat(64));
    let mut owner = PeerClient::connect(&a.net.me, b.addr(), Some(&b.fp())).unwrap();
    let reply = owner.call(&json!({"op": "push", "board": "demo", "origin": origin, "logs": {origin.clone(): rows}, "trusted_vector": {}})).unwrap();
    assert_eq!(reply["refused"][&origin], "damaged");
    a.say("demo", &at, "after the forgery");
    a.net.sync_once(true).unwrap();
    let said = a.member(&b.fp()).unwrap()["last_error"].as_str().unwrap().to_string();
    assert!(said.contains("refused") && said.contains(&format!("demo/{origin}: damaged")), "{said}");
}

#[test]
fn rows_a_sync_refused_are_named_by_board_and_origin_for_the_member_listing() {
    use poolhouse_node::netsync::refused_note;
    use poolhouse_node::sync::Report;
    use std::collections::BTreeMap;

    let clean = Report { pulled: 3, ..Report::default() };
    let mut bad = Report::default();
    bad.refused.insert("d7".into(), "damaged".into());
    bad.refused.insert("d9".into(), "gap".into());
    let reports: BTreeMap<String, Report> = [("demo".to_string(), clean), ("other".to_string(), bad)].into();
    let note = refused_note(&reports);
    assert!(note.contains("other/d7: damaged") && note.contains("other/d9: gap"), "{note}");
    assert!(!note.contains("demo/"), "{note}");
    assert_eq!(refused_note(&BTreeMap::new()), "");
}
