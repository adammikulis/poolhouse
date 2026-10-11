//! Every action the node used to allow to any session is refused to a session no grant names, and works once one does:
//! the pool actions on a device, landing control and trust, and the task authority.

mod kit;

use kit::netkit::*;
use serde_json::{json, Value};

/// (the method, its board, its params, the grant it needs)
fn sites() -> Vec<(&'static str, &'static str, Value, &'static str)> {
    vec![
        ("member_remove", "", json!({"fingerprint": "ab".repeat(32)}), "member_remove"),
        ("set_join_policy", "", json!({"policy": "open"}), "set_join_policy"),
        ("add_device", "", json!({}), "add_device"),
        ("join_pool", "", json!({"host": "127.0.0.1", "port": 9, "passphrase": "x"}), "join_pool"),
        ("sync_now", "", json!({}), "sync_now"),
        ("shard_call", "", json!({"device": "nobody", "op": "shard_caps"}), "shard_call"),
        ("shard_consent", "", json!({"enabled": false}), "shard_consent"),
        ("land_trust", "demo", json!({"device": "ab".repeat(32), "trusted": false}), "land_trust"),
        ("land_brake", "demo", json!({"pause": true}), "land_control"),
        ("task_mode", "demo", json!({"mode": "strict"}), "task_authority"),
    ]
}

#[test]
fn each_action_the_stub_allowed_is_refused_without_a_grant_and_works_with_one() {
    let d = device();
    let (name, token) = d.session("demo", "plain");
    for (method, board, params, action) in sites() {
        let reply = d.call(method, board, &token, params);
        assert_eq!(reply["error"]["code"], "denied", "{method} without a grant: {reply}");
        let hint = &reply["error"]["grant"];
        assert_eq!((hint["action"].clone(), hint["name"].clone(), hint["board"].clone()), (json!(action), json!(name), json!("demo")), "{method} names what would allow it: {reply}");
        assert_eq!(hint["device"], d.fp(), "{reply}");
    }
    for (method, board, params, action) in sites() {
        d.allow("demo", &token, &name, &[action]);
        let reply = d.call(method, board, &token, params);
        assert_ne!(reply["error"]["code"], "denied", "{method} with the grant {action}: {reply}");
    }
    let log = d.ok("grant_log", "demo", &token, json!({"limit": 200}))["log"].clone();
    for (_, _, _, action) in sites() {
        let used = log.as_array().unwrap().iter().filter(|e| e["op"] == "use" && e["action"] == action && e["user"] == json!(name)).count();
        assert!(used >= 1, "{action} was used and the use is on the board: {log}");
    }
}

#[test]
fn a_grant_for_one_action_opens_no_other() {
    let d = device();
    let (name, token) = d.session("demo", "plain");
    d.allow("demo", &token, &name, &["sync_now"]);
    for (method, board, params, action) in sites() {
        let reply = d.call(method, board, &token, params);
        if action == "sync_now" {
            assert_eq!(reply["ok"], true, "{reply}");
        } else {
            assert_eq!(reply["error"]["code"], "denied", "{method}: {reply}");
        }
    }
}

#[test]
fn a_granted_pool_action_can_be_used_only_as_often_as_it_was_granted() {
    use kit::grants::{enrolled, person, Spec};
    let d = device();
    let (name, token) = d.session("demo", "plain");
    let mut call = |m: &str, b: &str, t: &str, p: Value| d.call(m, b, t, p);
    enrolled(&mut call, &token);
    let reply = person().issue(&mut call, "demo", &token, "g-00000000000000f1", &Spec::session(&name, "set_join_policy").on("open").uses(1));
    assert_eq!(reply["ok"], true, "{reply}");
    assert_eq!(d.call("set_join_policy", "", &token, json!({"policy": "secure"}))["error"]["code"], "denied", "the grant names the policy `open`");
    assert_eq!(d.call("set_join_policy", "", &token, json!({"policy": "open"}))["ok"], true);
    assert_eq!(d.call("set_join_policy", "", &token, json!({"policy": "open"}))["error"]["code"], "denied", "one use was granted");
}
