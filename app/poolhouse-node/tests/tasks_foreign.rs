//! Tasks of a pool: a task another device wrote counts here only while this device names that device, as landing does.

mod kit;

use kit::netkit::*;
use poolhouse_node::netsync::sync_all;
use poolhouse_node::peer::PeerClient;
use serde_json::json;

fn sync(from: &Dev, to: &Dev) {
    let mut peer = PeerClient::connect(&from.net.me, to.addr(), Some(&to.fp())).unwrap();
    sync_all(&from.node, &mut peer).unwrap();
}

fn both_ways(a: &Dev, b: &Dev) {
    sync(a, b);
    sync(b, a);
}

#[test]
fn a_task_of_another_device_counts_once_this_device_names_it_and_carries_its_makers_label() {
    let (a, b) = (device(), device());
    a.pair_secure(&b);
    both_ways(&a, &b);
    let (maker, token) = b.session("demo", "b-maker");
    let made = b.ok("task_create", "demo", &token, json!({"title": "Remote work", "acceptance": ["It runs"]}));
    both_ways(&a, &b);

    let listed = a.ok("task_list", "demo", &a.token, json!({}));
    assert_eq!((listed["tasks"].as_array().unwrap().len(), listed["foreign_ignored"].clone()), (0, json!(1)), "{listed}");

    assert_eq!(a.call("land_trust", "demo", &a.token, json!({"device": b.fp(), "trusted": true}))["ok"], true);
    let listed = a.ok("task_list", "demo", &a.token, json!({}));
    let task = &listed["tasks"][0];
    assert_eq!((task["title"].clone(), task["state"].clone(), listed["foreign_ignored"].clone()), (json!("Remote work"), json!("queued"), json!(0)), "{listed}");
    assert!(task["created_by"].as_str().unwrap().starts_with(&format!("{maker}@d")), "the maker is shown with its device label: {task}");
    assert_eq!(task["id"], task["entry"], "a task of another device is named by its entry id");
    assert_ne!(task["id"], made["id"], "this device names its own tasks task-N, and the other device's id differs");
}
