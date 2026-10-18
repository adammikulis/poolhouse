//! A device named with `land_trust` is counted for tasks and grants and never for a landing review: two real nodes,
//! joined over loopback TLS, and a review written on the other one counts for nothing here.

mod kit;

use kit::netkit::*;
use poolhouse_node::netsync::sync_all;
use poolhouse_node::peer::PeerClient;
use serde_json::{json, Value};

const TIP: &str = "abcdef0123456789abcdef0123456789abcdef01";

fn sync(from: &Dev, to: &Dev) {
    let mut peer = PeerClient::connect(&from.net.me, to.addr(), Some(&to.fp())).unwrap();
    sync_all(&from.node, &mut peer).unwrap();
}

fn both_ways(a: &Dev, b: &Dev) {
    sync(a, b);
    sync(b, a);
}

/// This device (a) and one that joined its pool (b), which holds a reviewer session.
fn join() -> (Dev, Dev, String) {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    both_ways(&a, &b);
    let reviewer = b.session("demo", "b-judge").1;
    (a, b, reviewer)
}

fn reviews(d: &Dev) -> Vec<Value> {
    d.ok("land_accepts", "demo", &d.token, json!({"branch": "feat", "sha": TIP, "worktree": ""}))["reviews"].as_array().unwrap().clone()
}

fn trust(a: &Dev, b: &Dev, on: bool) -> Value {
    a.call("land_trust", "demo", &a.token, json!({"device": b.fp(), "trusted": on}))
}

#[test]
fn a_review_written_on_another_device_counts_for_nothing_here_trusted_or_not() {
    let (a, b, judge) = join();
    b.ok("land_review", "demo", &judge, json!({"branch": "feat", "sha": TIP, "verdict": "accept"}));
    assert_eq!(b.ok("land_accepts", "demo", &b.token, json!({"branch": "feat", "sha": TIP, "worktree": ""}))["reviews"].as_array().unwrap().len(), 1, "it counts where it was written");
    both_ways(&a, &b);
    assert!(reviews(&a).is_empty(), "foreign entries are not reviews here");
    assert_eq!(trust(&a, &b, true)["ok"], true);
    assert!(reviews(&a).is_empty(), "naming the device does not make its reviews count");
}

#[test]
fn naming_a_device_is_for_a_session_with_no_parent_and_only_for_an_active_member_and_is_audited() {
    let (a, b, _) = join();
    let kid = a.ok("register", "demo", &a.token, json!({"model": "claude-sonnet-5-5", "harness": "claude-code", "session": "kid"}))["token"].as_str().unwrap().to_string();
    let code = |r: Value| r["error"]["code"].as_str().unwrap_or("ok").to_string();
    assert_eq!(code(a.call("land_trust", "demo", &kid, json!({"device": b.fp(), "trusted": true}))), "denied", "a subagent never names a device");
    assert_eq!(code(a.call("land_trust", "demo", "", json!({"device": b.fp(), "trusted": true}))), "denied");
    assert_eq!(code(a.call("land_trust", "demo", &a.token, json!({"device": "ab".repeat(32), "trusted": true}))), "invalid", "not a member");
    assert_eq!(code(a.call("land_trust", "demo", &a.token, json!({"device": a.fp(), "trusted": true}))), "invalid", "this device always counts");
    assert_eq!(code(a.call("land_trust", "demo", &a.token, json!({"device": b.fp()}))), "invalid", "trusted is a boolean");
    assert_eq!(code(trust(&a, &b, true)), "ok");
    assert_eq!(code(trust(&a, &b, false)), "ok");
    let audit = a.ok("read", "demo", &a.token, json!({"kind": "audit", "limit": 50}));
    let events: Vec<&str> = audit["entries"].as_array().unwrap().iter().map(|e| e["fields"]["event"].as_str().unwrap()).collect();
    assert!(events.contains(&"land.trust") && events.contains(&"land.untrust"), "recorded on the board: {events:?}");
}
