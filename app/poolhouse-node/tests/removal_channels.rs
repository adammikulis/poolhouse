//! A device that records a removal closes the long-lived connections it keeps with the removed device, opens none, and shows it as not
//! connected.

mod kit;

use kit::netkit::*;
use poolhouse_node::peer::PeerClient;
use serde_json::json;

fn remove(by: &Dev, who: &Dev) {
    by.ok("member_remove", "", &by.token, json!({"fingerprint": who.fp()}));
}

fn connected(of: &Dev, who: &Dev) -> bool {
    of.member(&who.fp()).unwrap()["connected"] == true
}

#[test]
fn the_connection_a_member_opened_to_this_device_is_closed_when_the_member_is_removed() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    b.net.sync_once(true).unwrap();
    eventually("b to keep a connection open to a, and a to hold it", 10, || connected(&a, &b) && connected(&b, &a));
    remove(&a, &b);
    eventually("a to show b as not connected", 10, || !connected(&a, &b));
    eventually("b to see the connection a held end", 10, || !connected(&b, &a));
}

#[test]
fn the_connection_this_device_keeps_open_to_a_member_is_closed_when_the_member_is_removed() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    a.net.sync_once(true).unwrap();
    eventually("a to keep a connection open to b", 10, || connected(&a, &b));
    remove(&a, &b);
    eventually("a to close its connection to b", 10, || !connected(&a, &b));
}

#[test]
fn a_connection_is_not_opened_to_a_member_removed_before_the_attach_thread_starts() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    let last_seen = || b.member(&a.fp()).unwrap()["last_seen_ms"].clone();
    let before = last_seen();
    let mut held = a.node.lock().unwrap();
    let thread = a.net.attach(&b.fp(), b.addr()).expect("no attach was running");
    held.members.revoke(&b.fp(), "test", 1).unwrap();
    drop(held);
    eventually("the attach thread to end", 10, || thread.is_finished());
    thread.join().unwrap();
    assert_eq!(last_seen(), before, "the attach thread dialed a member that was removed before it started");
    assert!(!connected(&a, &b));
}

#[test]
fn a_connection_a_removed_member_opens_is_not_held() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    let client = PeerClient::connect(&a.net.me, b.addr(), Some(&b.fp())).unwrap();
    remove(&a, &b);
    a.net.register_reverse(client);
    assert!(!a.node.lock().unwrap().facts.peer(&b.fp()).channel, "a connection of a removed member was recorded as its channel");
}
