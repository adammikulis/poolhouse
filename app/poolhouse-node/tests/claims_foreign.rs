mod kit;

use kit::*;
use serde_json::json;
use tempfile::tempdir;

/// A claim another device of the pool holds is refused to this device with the holder named, not with a guess about "your" sessions.
#[test]
fn a_branch_another_device_holds_is_refused_naming_that_device_s_session() {
    let (da, db) = (tempdir().unwrap(), tempdir().unwrap());
    let (mut a, mut b) = (node(da.path()), node(db.path()));
    let ((name_a, ta), (_, tb)) = (session(&mut a, "demo", "a"), session(&mut b, "demo", "b"));
    ok(req(&mut a, "claim", "demo", &ta, json!({"kind": "branch", "key": "feat", "ttl_s": 600})));
    poolhouse_node::sync::exchange_nodes(&mut a, &mut b).unwrap();
    let refused = req(&mut b, "claim", "demo", &tb, json!({"kind": "branch", "key": "feat"}));
    assert_eq!(code(&refused), "denied");
    let message = refused["error"]["message"].as_str().unwrap();
    assert!(message.contains(&name_a) && !message.contains("of yours"), "the refusal must name the holder on the other device: {message}");
}

/// Two devices that could not see each other both took the same branch. When their boards meet the pool holds it for exactly one: the one
/// whose acquire is first in the board's order keeps it, and the other device's lease ends.
#[test]
fn a_branch_two_devices_both_took_while_apart_is_kept_by_the_first_only() {
    let (da, db) = (tempdir().unwrap(), tempdir().unwrap());
    let (mut a, mut b) = (node(da.path()), node(db.path()));
    let ((_, ta), (_, tb)) = (session(&mut a, "demo", "a"), session(&mut b, "demo", "b"));
    ok(req(&mut a, "claim", "demo", &ta, json!({"kind": "branch", "key": "feat", "ttl_s": 600})));
    std::thread::sleep(std::time::Duration::from_millis(20));
    ok(req(&mut b, "claim", "demo", &tb, json!({"kind": "branch", "key": "feat", "ttl_s": 600})));
    poolhouse_node::sync::exchange_nodes(&mut a, &mut b).unwrap();
    let held = |n: &mut poolhouse_node::node::Node, t: &str| ok(req(n, "claims", "demo", t, json!({})))["claims"].as_array().unwrap().len();
    assert_eq!((held(&mut a, &ta), held(&mut b, &tb)), (1, 0), "the first acquire keeps the branch; the other device's lease must end once the boards meet");
}

/// A reserve whose last resource another device holds is refused whole: what it had already taken is given back.
#[test]
fn a_reserve_refused_by_a_branch_another_device_holds_gives_back_what_it_took() {
    let (da, db) = (tempdir().unwrap(), tempdir().unwrap());
    let (mut a, mut b) = (node(da.path()), node(db.path()));
    let ((name_a, ta), (_, tb)) = (session(&mut a, "demo", "a"), session(&mut b, "demo", "b"));
    ok(req(&mut a, "claim", "demo", &ta, json!({"kind": "branch", "key": "feat", "ttl_s": 600})));
    poolhouse_node::sync::exchange_nodes(&mut a, &mut b).unwrap();
    let wanted = json!({"resources": [{"kind": "port", "key": "9300"}, {"kind": "server", "key": "api"}, {"kind": "branch", "key": "feat"}]});
    let refused = req(&mut b, "reserve", "demo", &tb, wanted);
    assert_eq!(code(&refused), "denied");
    assert!(refused["error"]["message"].as_str().unwrap().contains(&name_a), "{refused}");
    let mine = ok(req(&mut b, "claims", "demo", &tb, json!({})))["claims"].as_array().unwrap().len();
    assert_eq!(mine, 0, "the port and the server taken before the branch was refused are given back");
}
