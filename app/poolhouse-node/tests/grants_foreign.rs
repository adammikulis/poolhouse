//! A grant written on another device counts here only while this device trusts that device, only for the actions that
//! may travel, and only while a person key enrolled here signed it. Two real nodes joined over loopback TLS.

mod kit;

use kit::grants::{person, Spec};
use kit::netkit::*;
use poolhouse_node::grants::{check, Scope};
use poolhouse_node::identity::Holder;
use poolhouse_node::netsync::sync_all;
use poolhouse_node::peer::PeerClient;
use poolhouse_node::row::Kind;
use serde_json::{json, Value};

fn sync(from: &Dev, to: &Dev) {
    let mut peer = PeerClient::connect(&from.net.me, to.addr(), Some(&to.fp())).unwrap();
    sync_all(&from.node, &mut peer).unwrap();
}

fn both_ways(a: &Dev, b: &Dev) {
    sync(a, b);
    sync(b, a);
}

fn state(d: &Dev, token: &str, gid: &str) -> (String, String) {
    let all = d.ok("grants", "demo", token, json!({}));
    let found = all["grants"].as_array().unwrap().iter().find(|g| g["gid"] == gid).cloned().unwrap_or(json!({"status": "absent", "why": ""}));
    (found["status"].as_str().unwrap().to_string(), found["why"].as_str().unwrap().to_string())
}

fn status(d: &Dev, token: &str, gid: &str) -> String {
    state(d, token, gid).0
}

fn holds(d: &Dev, name: &str, action: &str) -> bool {
    check(&mut d.node.lock().unwrap(), &Holder { board: "demo".into(), name: name.into() }, action, &Scope::any()).unwrap()
}

fn issue(d: &Dev, gid: &str, spec: &Spec) {
    let reply = person().issue(&mut |m: &str, b: &str, t: &str, p: Value| d.call(m, b, t, p), "demo", &d.token, gid, spec);
    assert_eq!(reply["ok"], true, "{reply}");
}

fn trust(a: &Dev, b: &Dev, on: bool) {
    a.ok("land_trust", "demo", &a.token, json!({"device": b.fp(), "trusted": on}));
}

const NV: &str = "g-00000000000000e1";
const LC: &str = "g-00000000000000e2";

#[test]
fn a_grant_from_another_device_counts_only_from_a_trusted_one_and_only_for_actions_that_travel() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    let (x, xt) = a.session("demo", "x");
    both_ways(&a, &b);
    issue(&b, NV, &Spec::session(&x, "note_verify"));
    issue(&b, LC, &Spec::session(&x, "link_remove"));
    both_ways(&a, &b);

    let (s, why) = state(&a, &xt, NV);
    assert_eq!(s, "ignored", "{why}");
    assert!(why.contains("does not trust"), "{why}");
    assert!(!holds(&a, &x, "note_verify"), "an untrusted device grants nothing here");

    trust(&a, &b, true);
    assert_eq!(status(&a, &xt, NV), "live", "once this device trusts that one");
    assert!(holds(&a, &x, "note_verify"));
    let (s, why) = state(&a, &xt, LC);
    assert_eq!(s, "ignored");
    assert!(why.contains("the device it applies to"), "landing control does not travel: {why}");
    assert!(!holds(&a, &x, "link_remove"));

    trust(&a, &b, false);
    assert_eq!(status(&a, &xt, NV), "ignored", "and not once it stops");
    assert!(!holds(&a, &x, "note_verify"));
}

#[test]
fn a_revocation_or_a_use_from_an_untrusted_device_changes_nothing() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    let (x, xt) = a.session("demo", "x");
    both_ways(&a, &b);
    issue(&a, NV, &Spec::session(&x, "note_verify").uses(1).device(&a.fp()));
    both_ways(&a, &b);

    let revoked = person().revoke(&mut |m: &str, bd: &str, t: &str, p: Value| b.call(m, bd, t, p), "demo", &b.token, NV);
    assert_eq!(revoked["ok"], true, "{revoked}");
    for _ in 0..3 {
        let body = json!({"op": "use", "gid": NV, "action": "note_verify", "target": "n", "user": "claude-aaaaaa"});
        b.node.lock().unwrap().host("demo").unwrap().board.append(Kind::Grant, "claude-bbbbbb", body, "").unwrap();
    }
    both_ways(&a, &b);
    assert_eq!(status(&a, &xt, NV), "live", "neither the revocation nor the uses of a device this one does not trust count");
    assert!(holds(&a, &x, "note_verify"));

    issue(&a, "g-00000000000000e3", &Spec::session(&x, "sync_now").uses(1).device(&a.fp()));
    trust(&a, &b, true);
    assert_eq!(status(&a, &xt, NV), "revoked", "the same revocation counts once the device is trusted");
    assert_eq!(status(&a, &xt, "g-00000000000000e3"), "live");
}

#[test]
fn a_floor_or_an_unsigned_grant_from_a_trusted_device_is_still_no_grant() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    let (x, xt) = a.session("demo", "x");
    both_ways(&a, &b);
    trust(&a, &b, true);

    let mut body = person().issue_params("demo", NV, &Spec::session(&x, "link_remove")).as_object().unwrap().clone();
    body.insert("action".into(), json!("push_main"));
    body.insert("op".into(), json!("issue"));
    body.insert("board".into(), json!("demo"));
    let mut floor = person().sign_params(body).as_object().unwrap().clone();
    floor.insert("op".into(), json!("issue"));
    floor.insert("board".into(), json!("demo"));
    let mut unsigned = person().issue_params("demo", LC, &Spec::session(&x, "note_verify")).as_object().unwrap().clone();
    unsigned.insert("op".into(), json!("issue"));
    unsigned.insert("board".into(), json!("demo"));
    unsigned.insert("sig".into(), json!("00".repeat(64)));
    for row in [floor, unsigned] {
        b.node.lock().unwrap().host("demo").unwrap().board.append(Kind::Grant, "claude-bbbbbb", Value::Object(row), "").unwrap();
    }
    both_ways(&a, &b);
    let all = a.ok("grants", "demo", &xt, json!({}));
    assert_eq!(status(&a, &xt, LC), "ignored", "{all}");
    assert!(!holds(&a, &x, "note_verify"), "a signature that does not verify makes no grant");
    assert!(all["ignored"].as_array().unwrap().iter().any(|i| i["why"].as_str().unwrap().contains("floor")), "{all}");
}

#[test]
fn a_flood_of_grant_entries_from_an_untrusted_device_is_read_newest_first_and_only_in_part() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    let (x, xt) = a.session("demo", "x");
    both_ways(&a, &b);
    let gid = |i: u32| format!("g-{i:016x}");
    for i in 1..=205 {
        issue(&b, &gid(i), &Spec::session(&x, "link_remove"));
    }
    both_ways(&a, &b);
    let all = a.ok("grants", "demo", &xt, json!({}));
    let listed: Vec<&str> = all["grants"].as_array().unwrap().iter().map(|g| g["gid"].as_str().unwrap()).collect();
    assert!(listed.len() <= 200, "a listing is bounded: {}", listed.len());
    assert!(listed.contains(&gid(150).as_str()) && !listed.contains(&gid(1).as_str()) && !listed.contains(&gid(5).as_str()), "the newest of the untrusted device's entries are read, not the oldest");
    assert_eq!(status(&a, &xt, &gid(150)), "ignored");
    assert!(!holds(&a, &x, "link_remove"), "and none of them counts");
}
