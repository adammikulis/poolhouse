//! A device nothing can dial (behind NAT, as WSL2 is) is still reached: it dials a member, keeps that connection open, and the member
//! sends its requests down it. Sync needs no such channel (the device that dials swaps boards in both directions); test shards do.

mod kit;

use kit::netkit::*;
use serde_json::{json, Value};

/// ``a`` can be dialed, ``b`` cannot: the address ``a`` holds for ``b`` is one nothing listens on.
fn behind_nat() -> (Dev, Dev) {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    a.node.lock().unwrap().facts.set_addr(&b.fp(), "127.0.0.1:9").unwrap();
    (a, b)
}

fn caps(a: &Dev, b: &Dev) -> Value {
    a.call("shard_call", "", &a.token, json!({"device": b.fp(), "op": "shard_caps"}))
}

#[test]
fn a_member_nothing_can_dial_is_reached_over_the_connection_it_opened() {
    let (a, b) = behind_nat();
    let before = caps(&a, &b);
    assert_eq!(before["ok"], false, "{before}");
    b.net.sync_once(true).unwrap();
    eventually("a to reach b over the connection b opened", 10, || caps(&a, &b)["ok"] == true);
    assert!(a.member(&b.fp()).unwrap()["connected"] == true, "the member shows as connected while its channel is open");
}

#[test]
fn a_member_that_cannot_be_reached_is_named_with_what_to_do() {
    let (a, b) = behind_nat();
    let refused = caps(&a, &b);
    let text = refused["error"]["message"].as_str().unwrap();
    assert!(text.contains("has not connected to this device") && text.contains("sync"), "{text}");
}

#[test]
fn the_channel_of_a_removed_member_is_not_used() {
    let (a, b) = behind_nat();
    b.net.sync_once(true).unwrap();
    eventually("the channel", 10, || caps(&a, &b)["ok"] == true);
    a.ok("member_remove", "", &a.token, json!({"fingerprint": b.fp()}));
    let after = caps(&a, &b);
    assert_eq!(after["ok"], false, "{after}");
}

#[test]
fn a_failing_address_is_not_dialed_again_at_once() {
    let (a, b) = behind_nat();
    a.net.sync_once(false).unwrap();
    let first = a.member(&b.fp()).unwrap()["last_error"].as_str().unwrap().to_string();
    assert!(!first.is_empty(), "the dial to a dead address must leave its error");
    a.node.lock().unwrap().facts.peer(&b.fp()).last_error = "marker".into();
    a.net.sync_once(false).unwrap();
    assert_eq!(a.member(&b.fp()).unwrap()["last_error"], "marker", "the second attempt came before the backoff ended");
    a.ok("sync_now", "", &a.token, json!({}));
    assert_ne!(a.member(&b.fp()).unwrap()["last_error"], "marker", "an explicit sync_now dials regardless");
}
