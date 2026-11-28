mod kit;

use kit::netkit::*;
use poolhouse_node::error::Error;
use poolhouse_node::netsync::sync_all;
use poolhouse_node::peer::PeerClient;
use poolhouse_node::sync::{MAX_BATCH_BYTES, MAX_ROWS};
use serde_json::{json, Value};

fn reach(from: &Dev, to: &Dev) -> PeerClient {
    PeerClient::connect(&from.net.me, to.addr(), Some(&to.fp())).unwrap()
}

fn sync(from: &Dev, to: &Dev) {
    sync_all(&from.node, &mut reach(from, to)).unwrap();
}

fn origin_of(d: &Dev) -> String {
    d.node.lock().unwrap().boards["demo"].board.origin().to_string()
}

/// Three members that all hold `demo`, each with its own session.
fn trio() -> [(Dev, String); 3] {
    let (a, b, c) = (device(), device(), device());
    a.join_with_code(&b);
    a.join_with_code(&c);
    b.know(&c);
    c.know(&b);
    sync(&b, &a);
    sync(&c, &a);
    // Distinct session ids: a foreign row from a name that is also a local session's is refused.
    let mut n = 0;
    [a, b, c].map(|d| {
        n += 1;
        let t = d.session("demo", &format!("writer-{n}")).1;
        (d, t)
    })
}

/// The boards ``to`` lists for ``from``, in `boards` and in `hello`.
fn listed(from: &Dev, to: &Dev) -> (Value, Value) {
    let mut c = reach(from, to);
    (c.call(&json!({"op": "boards"})).unwrap()["boards"].clone(), c.call(&json!({"op": "hello"})).unwrap()["boards"].clone())
}

/// Whether ``to`` refuses ``from`` the board `demo` for a pull, a vector and a push, each as a denial.
fn refused(from: &Dev, to: &Dev) -> bool {
    let mut c = reach(from, to);
    let origin = origin_of(from);
    [json!({"op": "pull", "board": "demo", "origin": origin, "vector": {}}), json!({"op": "vector", "board": "demo", "origin": origin}),
     json!({"op": "push", "board": "demo", "origin": origin, "logs": {}, "trusted_vector": {}})].iter().all(|ask| matches!(c.call(ask), Err(Error::Denied(_))))
}

/// An open pool founded on the first device, a second that joined it with a code and a third that joined it without one.
fn open_pool_of_three() -> (Dev, Dev, Dev) {
    let (a, member, guest) = (device(), device(), device());
    kit::govnode::found(&a, kit::gov::person(kit::govnode::ANN, "ann"), "open");
    a.join_with_code(&member);
    guest.net.join_pool(a.addr(), "", &a.fp()).unwrap();
    assert_eq!(a.member(&guest.fp()).unwrap()["by"], "open");
    sync(&member, &a);
    (a, member, guest)
}

#[test]
fn a_device_that_joined_without_a_code_gets_nothing_of_a_board_nobody_shared_with_it() {
    let (a, member, guest) = open_pool_of_three();
    let at = a.session("demo", "writer").1;
    let reader = member.session("demo", "reader").1;
    let theirs = guest.session("demo", "visitor").1;
    a.say("demo", &at, "for the owner's devices");
    guest.say("demo", &theirs, "from the guest");
    sync(&member, &a);
    assert!(member.texts("demo", &reader).contains(&"for the owner's devices".to_string()), "a device that joined with the code holds the board");
    for serving in [&a, &member] {
        assert_eq!(listed(&guest, serving), (json!([]), json!([])), "a board nobody shared with the caller is not listed");
        assert!(refused(&guest, serving), "nor pulled, compared or pushed to");
    }
    // the caller's own client, and this device's when it is the one that dials, move nothing either
    sync(&guest, &a);
    sync(&a, &guest);
    sync(&member, &guest);
    assert_eq!(guest.texts("demo", &theirs), vec!["from the guest"]);
    assert!(!a.texts("demo", &at).contains(&"from the guest".to_string()));
}

#[test]
fn the_device_that_serves_a_board_decides_with_whom_it_is_shared() {
    let (a, member, guest) = open_pool_of_three();
    let at = a.session("demo", "writer").1;
    let theirs = guest.session("demo", "visitor").1;
    a.say("demo", &at, "shared by choice");
    let share = |token: &str, device: &Dev, board: &str, shared: bool| a.call("board_share", "", token, json!({"device": device.fp(), "board": board, "shared": shared}));
    assert_eq!(share(&at, &guest, "demo", true)["error"]["code"], "denied", "a session without the grant shares nothing");
    kit::govnode::grant(&a, "demo", &a.token, &["board_share"]);
    assert_eq!(share(&a.token, &guest, "demo", true)["ok"], true);
    assert_eq!(listed(&guest, &a), (json!(["demo"]), json!(["demo"])), "the one board shared, and no other");
    sync(&guest, &a);
    assert!(guest.texts("demo", &theirs).contains(&"shared by choice".to_string()));

    assert!(!refused(&member, &a));
    assert_eq!(share(&a.token, &member, "demo", false)["ok"], true);
    assert!(refused(&member, &a), "a board withheld from a member is refused to it");
    assert!(!listed(&member, &a).0.as_array().unwrap().contains(&json!("demo")));
    a.say("demo", &at, "after it was withheld");
    sync(&a, &member);
    sync(&member, &a);
    let reader = member.session("demo", "reader").1;
    assert!(!member.texts("demo", &reader).contains(&"after it was withheld".to_string()));

    assert_eq!(share(&a.token, &guest, "", false)["ok"], true);
    assert_eq!(listed(&guest, &a), (json!([]), json!([])), "the latest choice for every board replaces the one for a board");
}

#[test]
fn a_third_device_gets_rows_through_a_relay_and_they_still_verify() {
    let [(a, at), (b, bt), (c, ct)] = trio();
    a.say("demo", &at, "written on a");
    sync(&b, &a);
    sync(&c, &b);
    assert!(c.texts("demo", &ct).contains(&"written on a".to_string()), "a reached c though c never spoke to a");
    let read = c.ok("read", "demo", &ct, json!({"limit": 1000}));
    let entry = read["entries"].as_array().unwrap().iter().find(|e| e["fields"]["body"] == "written on a").unwrap();
    assert_eq!(entry["foreign"], true);
    assert!(entry["sender"].as_str().unwrap().contains("@d"), "a foreign sender is shown as name@dN");
    let _ = (bt, b);
}

#[test]
fn a_forged_copy_from_a_relay_is_refused_and_does_not_mark_the_origin_damaged() {
    let [(a, at), (b, _), (c, ct)] = trio();
    a.say("demo", &at, "genuine");
    sync(&b, &a);
    let origin = origin_of(&a);
    let mut rows: Vec<Value> = {
        let n = b.node.lock().unwrap();
        n.boards["demo"].board.trusted(&origin).iter().map(|r| serde_json::to_value(r).unwrap()).collect()
    };
    let last = rows.len() - 1;
    let mut forged = rows[last].clone();
    forged["body"] = json!({"type": "status", "from": "someone", "to": "#general", "subject": "", "body": "forged"});
    rows[last] = forged;
    let c_origin = origin_of(&c);
    let mut relay = reach(&b, &c);
    let reply = relay.call(&json!({"op": "push", "board": "demo", "origin": origin_of(&b), "logs": {origin.clone(): rows}, "trusted_vector": {}})).unwrap();
    assert_eq!(reply["refused"][&origin], "damaged", "{reply}");
    let damaged = c.node.lock().unwrap().boards["demo"].board.damaged().clone();
    assert!(damaged.is_empty(), "a relay's bad copy never marks the origin: {damaged:?}");
    sync(&c, &a);
    assert!(c.texts("demo", &ct).contains(&"genuine".to_string()), "the genuine rows still arrive from their owner");
    let _ = c_origin;
}

#[test]
fn the_owner_of_a_log_that_sends_a_forged_copy_of_it_is_marked_damaged() {
    let [(a, at), (_, _), (c, _)] = trio();
    a.say("demo", &at, "genuine");
    a.say("demo", &at, "second");
    sync(&c, &a);
    let origin = origin_of(&a);
    let mut rows: Vec<Value> = {
        let n = a.node.lock().unwrap();
        n.boards["demo"].board.trusted(&origin).iter().map(|r| serde_json::to_value(r).unwrap()).collect()
    };
    let last = rows.len() - 1;
    rows[last]["hash"] = json!("0".repeat(64));
    let mut owner = reach(&a, &c);
    let reply = owner.call(&json!({"op": "push", "board": "demo", "origin": origin, "logs": {origin.clone(): rows}, "trusted_vector": {}})).unwrap();
    assert_eq!(reply["refused"][&origin], "damaged");
    assert!(c.node.lock().unwrap().boards["demo"].board.damaged().contains_key(&origin));
}

#[test]
fn a_pull_carries_at_most_the_rows_and_bytes_of_a_batch() {
    let [(a, at), (b, bt), _] = trio();
    for i in 0..MAX_ROWS + 20 {
        a.say("demo", &at, &format!("row {i}"));
    }
    let mut c = reach(&b, &a);
    let reply = c.call(&json!({"op": "pull", "board": "demo", "origin": origin_of(&b), "vector": {}})).unwrap();
    let rows: usize = reply["logs"].as_object().unwrap().values().map(|v| v.as_array().unwrap().len()).sum();
    assert!(rows <= MAX_ROWS && rows > 0, "{rows} rows in one reply");
    sync(&b, &a);
    assert_eq!(b.texts("demo", &bt).iter().filter(|t| t.starts_with("row ")).count(), MAX_ROWS + 20, "many rounds finish what one batch could not");

    let big = "x".repeat(60_000);
    let before = a.texts("demo", &at).len();
    for _ in 0..40 {
        a.say("demo", &at, &big);
    }
    let mut c = reach(&b, &a);
    let vector = b.node.lock().unwrap().boards["demo"].board.vector();
    let reply = c.call(&json!({"op": "pull", "board": "demo", "origin": origin_of(&b), "vector": vector})).unwrap();
    let bytes = serde_json::to_vec(&reply["logs"]).unwrap().len();
    assert!(bytes < MAX_BATCH_BYTES + 130 * 1024, "{bytes} bytes in one reply");
    sync(&b, &a);
    assert_eq!(b.texts("demo", &bt).len(), before + 40);
}

#[test]
fn a_push_past_the_request_bound_is_refused_whole() {
    let [(a, _), (b, _), _] = trio();
    let many: serde_json::Map<String, Value> = (0..20).map(|i| (format!("{i:032x}"), json!([]))).collect();
    let mut c = reach(&b, &a);
    let r = c.call(&json!({"op": "push", "board": "demo", "origin": origin_of(&b), "logs": many, "trusted_vector": {}}));
    assert!(matches!(r, Err(Error::Invalid(_))), "{r:?}");
}

#[test]
fn a_device_that_writes_a_different_log_than_it_presented_is_refused() {
    let [(a, _), (b, _), _] = trio();
    sync(&b, &a);
    let mut c = reach(&b, &a);
    let r = c.call(&json!({"op": "vector", "board": "demo", "origin": "f".repeat(32)}));
    assert!(matches!(r, Err(Error::Denied(_))), "{r:?}");
}

#[test]
fn a_long_unsigned_run_is_signed_as_it_grows_so_a_batch_always_holds_a_head() {
    let dir = tempfile::tempdir().unwrap();
    let mut board = kit::board(&dir, 1);
    for i in 0..200 {
        kit::say(&mut board, "claude-aaaaaa", &format!("m{i}"));
    }
    let origin = board.origin().to_string();
    let rows = board.rows(&origin);
    let heads: Vec<usize> = rows.iter().enumerate().filter(|(_, r)| r.kind == poolhouse_node::row::Kind::Head).map(|(i, _)| i).collect();
    assert!(heads.len() >= 3, "{heads:?}");
    assert!(heads.windows(2).all(|w| w[1] - w[0] <= poolhouse_node::board::HEAD_EVERY_ROWS + 1), "{heads:?}");
}

#[test]
fn two_partitioned_devices_converge_when_they_meet() {
    let [(a, at), (b, bt), _] = trio();
    a.say("demo", &at, "a1");
    b.say("demo", &bt, "b1");
    a.say("demo", &at, "a2");
    b.say("demo", &bt, "b2");
    sync(&a, &b);
    let (mut x, mut y) = (a.texts("demo", &at), b.texts("demo", &bt));
    assert_eq!(x, y, "same rows in the same merged order");
    x.sort();
    y.sort();
    assert_eq!(x, vec!["a1", "a2", "b1", "b2"]);
    let r = a.ok("sync_now", "", &a.token, json!({}));
    assert!(r["reached"].as_u64().unwrap() >= 1);
}
