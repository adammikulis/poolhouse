//! Every action the node used to allow to any session is refused to a session no grant names, and works once one does:
//! the pool actions on a device, landing control and trust, and the task authority.

mod kit;

use kit::netkit::*;
use serde_json::{json, Value};

/// (the method, its board, its params, the grant it needs)
fn sites() -> Vec<(&'static str, &'static str, Value, &'static str)> {
    vec![
        ("member_remove", "", json!({"fingerprint": "ab".repeat(32)}), "member_remove"),
        ("pool_leave", "", json!({}), "pool_leave"),
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
        assert!(reply["error"]["grant"].is_null(), "{method} with the grant {action} is refused for no grant: {reply}");
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
            assert_eq!(reply["error"]["grant"]["action"], json!(action), "{method} is refused for want of its own grant: {reply}");
        }
    }
}

#[test]
fn a_granted_pool_action_can_be_used_only_as_often_as_it_was_granted() {
    use kit::grants::{enrolled, person, Spec};
    let d = device();
    kit::govnode::found(&d, kit::gov::person(kit::govnode::ANN, "ann"), "secure");
    let (name, token) = d.session("demo", "plain");
    let mut call = |m: &str, b: &str, t: &str, p: Value| d.call(m, b, t, p);
    enrolled(d.dir.path(), &mut call, &token);
    let reply = person().issue(&mut call, "demo", &token, "g-00000000000000f1", &Spec::session(&name, "pool_leave").uses(1));
    assert_eq!(reply["ok"], true, "{reply}");
    assert_eq!(d.call("pool_leave", "", &token, json!({}))["ok"], true);
    let again = d.call("pool_leave", "", &token, json!({}));
    assert_eq!((again["error"]["code"].clone(), again["error"]["grant"]["action"].clone()), (json!("denied"), json!("pool_leave")), "one use was granted: {again}");
}

fn used(d: &Dev, board: &str, token: &str, gid: &str) -> u64 {
    let all = d.ok("grants", board, token, json!({}));
    all["grants"].as_array().unwrap().iter().find(|g| g["gid"] == gid).unwrap()["used"].as_u64().unwrap()
}

#[test]
fn a_use_of_a_grant_is_spent_only_when_the_thing_it_allows_was_done() {
    use kit::grants::{enrolled, person, Spec};
    let d = device();
    let (name, token) = d.session("boarda", "lead");
    d.session("boardb", "x");
    let mut call = |m: &str, b: &str, t: &str, p: Value| d.call(m, b, t, p);
    enrolled(d.dir.path(), &mut call, &token);
    for (gid, action) in [("g-00000000000000f2", "link_read"), ("g-00000000000000f3", "link_remove"), ("g-00000000000000f4", "source_add")] {
        let reply = person().issue(&mut call, "boarda", &token, gid, &Spec::session(&name, action).uses(1));
        assert_eq!(reply["ok"], true, "{reply}");
    }

    let bad = d.call("link", "boarda", &token, json!({"to": "boardb", "channels": ["no-hash"], "mode": "ro"}));
    assert_eq!(bad["error"]["code"], "invalid", "{bad}");
    assert_eq!(used(&d, "boarda", &token, "g-00000000000000f2"), 0, "a link that could not be made spent nothing");
    let link = d.ok("link", "boarda", &token, json!({"to": "boardb", "channels": [], "mode": "ro"}));
    assert_eq!(used(&d, "boarda", &token, "g-00000000000000f2"), 1, "the link that was made spent its one use");
    assert_eq!(d.call("link", "boarda", &token, json!({"to": "boardb", "channels": [], "mode": "ro"}))["error"]["code"], "denied");

    let gone = d.call("unlink", "boarda", &token, json!({"id": "link-99"}));
    assert_eq!(gone["error"]["code"], "invalid", "{gone}");
    assert_eq!(used(&d, "boarda", &token, "g-00000000000000f3"), 0, "ending a link that is not there spent nothing");
    d.ok("unlink", "boarda", &token, json!({"id": link["id"]}));
    assert_eq!(used(&d, "boarda", &token, "g-00000000000000f3"), 1);

    let nowhere = d.call("source_add", "", &token, json!({"id": "boarda", "kind": "folder", "path": "/definitely/not/here"}));
    assert_eq!(nowhere["error"]["code"], "invalid", "{nowhere}");
    assert_eq!(used(&d, "boarda", &token, "g-00000000000000f4"), 0, "a place that is not there spent nothing");
}

#[test]
fn a_grant_held_on_one_board_is_not_used_on_another_board_through_a_link() {
    let d = device();
    let (lead, lead_token) = d.session("boarda", "lead");
    d.allow("boarda", &lead_token, &lead, &["note_verify", "quarantine_release", "file_delete"]);
    let (other, other_token) = d.session("boardb", "other");
    d.allow("boardb", &other_token, &other, &["link_write"]);
    let note = d.ok("post", "boardb", &other_token, json!({"kind": "note", "fields": {"nkind": "fact", "title": "t", "body": "b", "verify_cmd": "echo hi"}}));
    let id = note["seq"].to_string();
    d.ok("link", "boardb", &other_token, json!({"to": "boarda", "channels": [], "mode": "rw"}));
    let reached = |method: &str, params: Value| d.call(method, "boardb", &lead_token, params);
    for (method, params) in [("note_verify", json!({"note": id, "exit": 0, "out_sha": "ab".repeat(32)})), ("quarantine_release", json!({"qid": "q-nothing"})),
                             ("file_delete", json!({"handle": "0".repeat(12)}))] {
        let reply = reached(method, params);
        assert_eq!(reply["error"]["code"], "denied", "{method}: the grant on boarda does not reach boardb: {reply}");
        assert!(reply["error"].get("grant").is_none(), "{method}: no grant on boardb would be named for a board the session does not hold a token for: {reply}");
    }
    let log = d.ok("grant_log", "boarda", &lead_token, json!({"limit": 200}))["log"].clone();
    assert!(log.as_array().unwrap().iter().all(|e| e["op"] != "use"), "boarda's grants were not spent on boardb: {log}");
    let notes = d.ok("notes", "boardb", &other_token, json!({}));
    assert_eq!(notes["notes"][0]["trust"], "agent-claimed", "boardb's note was not verified: {notes}");
}
