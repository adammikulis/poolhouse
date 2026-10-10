//! A member's build is known to every member, also to those that have never dialed it: members tell each other the builds they heard first hand.

mod kit;

use kit::netkit::*;
use poolhouse_node::build::Build;
use serde_json::Value;

fn build(d: &Dev, id: &str, seq: u64) {
    d.node.lock().unwrap().build = Build { id: id.into(), seq, wire: 1, pinned: false };
}

fn row(d: &Dev, other: &Dev) -> Value {
    d.member(&other.fp()).unwrap()
}

/// a knows only b's address and c knows only b's: a and c never dial each other.
fn chain() -> (Dev, Dev, Dev) {
    let (a, b, c) = (device(), device(), device());
    b.pair_secure(&a);
    b.pair_secure(&c);
    (a, b, c)
}

#[test]
fn a_build_a_member_never_dialed_is_known_through_the_member_between() {
    let (a, b, c) = chain();
    build(&a, "aaaa", 5);
    build(&b, "bbbb", 9);
    build(&c, "cccc", 12);
    b.net.sync_once(true).unwrap();
    a.net.sync_once(true).unwrap();
    let seen = row(&a, &c);
    assert_eq!((seen["build"].as_str(), seen["build_state"].as_str(), seen["relayed"].as_bool()), (Some("cccc"), Some("newer"), Some(true)), "{seen}");
    assert_eq!(a.status()["build"]["state"], "catching up", "a is behind c, which it has never dialed");
}

#[test]
fn what_a_member_reports_itself_replaces_what_was_told() {
    let (a, b, c) = chain();
    build(&c, "cccc", 12);
    b.net.sync_once(true).unwrap();
    a.net.sync_once(true).unwrap();
    assert_eq!(row(&a, &c)["relayed"], true);
    build(&c, "dddd", 13);
    a.node.lock().unwrap().observe_build(&c.fp(), &serde_json::json!({"build": "dddd", "build_seq": 13, "wire": 1}));
    b.net.sync_once(true).unwrap();
    a.net.sync_once(true).unwrap();
    let seen = row(&a, &c);
    assert_eq!((seen["build"].as_str(), seen["relayed"].as_bool()), (Some("dddd"), Some(false)), "{seen}");
}

#[test]
fn a_build_is_told_on_through_more_than_one_member() {
    let (a, b, c, d) = (device(), device(), device(), device());
    b.pair_secure(&a);
    b.pair_secure(&c);
    c.pair_secure(&d);
    build(&d, "dddd", 20);
    for _ in 0..2 {
        for dev in [&d, &c, &b, &a] {
            dev.net.sync_once(true).unwrap();
        }
    }
    let seen = row(&a, &d);
    assert_eq!((seen["build"].as_str(), seen["relayed"].as_bool()), (Some("dddd"), Some(true)), "{seen}");
}
