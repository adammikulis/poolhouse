//! Build identity between two real loopback nodes: what each advertises, which is stale, the
//! catching-up gate, and a peer on another wire version shown with its fix.

mod kit;

use kit::netkit::*;
use poolhouse_node::build::Build;
use poolhouse_node::peer::PeerClient;
use serde_json::{json, Value};

fn build(d: &Dev, id: &str, seq: u64, wire: u32) {
    d.node.lock().unwrap().build = Build { id: id.into(), seq, wire, pinned: false };
}

fn pair() -> (Dev, Dev) {
    let (a, b) = (device(), device());
    a.pair_secure(&b);
    (a, b)
}

fn gpu(d: &Dev) -> Value {
    d.call("lease_acquire", "demo", &d.token, json!({"resources": [{"type": "gpu"}], "wait": false}))
}

fn row(d: &Dev, other: &Dev) -> Value {
    d.member(&other.fp()).unwrap()
}

#[test]
fn hello_carries_the_build_and_the_wire_version() {
    let (a, b) = pair();
    build(&b, "bbbb", 9, 1);
    let mut c = PeerClient::connect(&a.net.me, b.addr(), Some(&b.fp())).unwrap();
    let hello = c.call(&json!({"op": "hello"})).unwrap();
    assert_eq!((hello["build"].as_str(), hello["build_seq"].as_u64(), hello["wire"].as_u64()), (Some("bbbb"), Some(9), Some(1)));
}

#[test]
fn a_device_that_hears_of_a_newer_build_is_stale_and_the_newer_one_is_not() {
    let (a, b) = pair();
    build(&a, "aaaa", 5, 1);
    build(&b, "bbbb", 9, 1);
    a.net.sync_once().unwrap();
    assert_eq!(a.status()["build"]["state"], "catching up");
    assert_eq!(row(&a, &b)["build_state"], "newer");
    assert_eq!(row(&a, &b)["build"], "bbbb");
    // b learned a's build from the hello a sent: it needs no sync of its own to know a is behind.
    assert_eq!(b.status()["build"]["state"], "current");
    assert_eq!(row(&b, &a)["build_state"], "older");
}

#[test]
fn a_catching_up_device_takes_no_lease_until_it_is_current() {
    let (a, b) = pair();
    build(&a, "aaaa", 5, 1);
    build(&b, "bbbb", 9, 1);
    a.net.sync_once().unwrap();
    let refused = gpu(&a);
    assert_eq!(refused["ok"], false, "{refused}");
    assert_eq!(refused["error"]["code"], "denied");
    assert!(refused["error"]["message"].as_str().unwrap().contains("catching up"), "{refused}");
    // The newer device is not held back.
    assert_eq!(gpu(&b)["ok"], true);
    build(&a, "bbbb", 9, 1);
    assert_eq!(a.status()["build"]["state"], "current");
    assert_eq!(gpu(&a)["ok"], true);
}

#[test]
fn a_pinned_stale_device_still_takes_work_and_is_shown_pinned() {
    let (a, b) = pair();
    build(&a, "aaaa", 5, 1);
    a.node.lock().unwrap().build.pinned = true;
    build(&b, "bbbb", 9, 1);
    a.net.sync_once().unwrap();
    assert_eq!(a.status()["build"]["state"], "pinned");
    assert_eq!(gpu(&a)["ok"], true);
    b.net.sync_once().unwrap();
    assert_eq!(row(&b, &a)["build_state"], "pinned");
}

#[test]
fn a_peer_on_another_wire_version_is_shown_needing_an_update_with_the_fix_and_is_not_synced() {
    let (a, b) = pair();
    build(&a, "aaaa", 5, 1);
    build(&b, "bbbb", 9, 2);
    a.say("demo", &a.token, "from a");
    let reached = a.net.sync_once().unwrap();
    assert_eq!(reached, 0, "no sync with a peer on another wire");
    let theirs = row(&a, &b);
    assert_eq!(theirs["build_state"], "needs update");
    assert!(theirs["fix"].as_str().unwrap().contains("poolhouse runtime ensure"), "{theirs}");
    assert!(theirs["last_error"].as_str().unwrap().contains("needs update"), "{theirs}");
    assert_eq!(a.status()["build"]["state"], "needs update");
    // It does not make this device stale: the wire differs, so its seq says nothing about the pool's.
    assert_eq!(gpu(&a)["ok"], true);
}

#[test]
fn pool_status_counts_the_leases_so_an_updater_can_tell_whether_the_device_may_be_interrupted() {
    let a = device();
    assert_eq!(a.status()["leases"], json!({"held": 0, "queued": 0}));
    assert_eq!(gpu(&a)["ok"], true);
    assert_eq!(a.status()["leases"]["held"], 1);
}

#[test]
fn a_peer_with_no_build_fields_is_an_older_wire_and_shown_needing_an_update() {
    let (a, b) = pair();
    a.node.lock().unwrap().observe_build(&b.fp(), &json!({"pool": "x"}));
    let theirs = row(&a, &b);
    assert_eq!(theirs["build_state"], "needs update");
    assert_eq!(theirs["wire"], 0);
}
