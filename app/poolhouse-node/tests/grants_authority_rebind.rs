//! Moving a main session in a pool: it asks nobody. The terminal that runs `rebind` takes the session over at once, in a pool whose admin
//! phone is added and in one whose phone is not, with no signature, no approval and no tap, and nothing is left waiting for a person.
#![cfg(unix)]

mod kit;

use kit::pool::{found, Founded};
use kit::procs::*;
use kit::*;
use poolhouse_node::lineage::Peer;
use serde_json::{json, Value};

fn rebind(pool: &Founded, from: &Peer) -> Value {
    req_from(&mut pool.rig.node.lock().unwrap(), from, "rebind", "demo", "", json!({"harness": "claude-code", "session": "lead"}))
}

fn lead_works(pool: &Founded) -> bool {
    req(&mut pool.rig.node.lock().unwrap(), "whoami", "demo", &pool.token, json!({}))["ok"] == true
}

fn registers_from(pool: &Founded, from: &Peer) -> Value {
    req_from(&mut pool.rig.node.lock().unwrap(), from, "register", "demo", "", json!({"model": "claude-sonnet-5-5", "harness": "claude-code", "session": "lead"}))
}

fn lineage_of_lead(pool: &Founded) -> Vec<u32> {
    pool.rig.node.lock().unwrap().boards["demo"].names.get(&pool.name).unwrap().lineage.iter().map(|a| a.pid).collect()
}

#[test]
fn in_a_pool_with_an_admin_phone_a_session_move_is_made_at_once_and_waits_for_nobody() {
    let pool = found();
    let there = process(true);
    assert!(lead_works(&pool));
    let moved = ok(rebind(&pool, &peer(&there)));
    assert_eq!((moved["name"].clone(), moved["moved"].clone()), (json!(pool.name), json!(true)), "{moved}");
    assert!(moved["tokens_ended"].as_u64().unwrap() >= 1);
    assert!(!lead_works(&pool), "the tokens it held ended");
    assert_eq!(lineage_of_lead(&pool), vec![there.id()], "the session is registered from the terminal that ran the command");
    assert!(pool.rig.node.lock().unwrap().approvals.all().is_empty() && pool.rig.pushed.sent().is_empty(), "no request waits on a phone");
    assert_eq!(registers_from(&pool, &me())["ok"], true, "the old terminal registers the session again, as a live sibling");
    assert_eq!(lineage_of_lead(&pool), vec![there.id()], "and the session stays with the terminal that took it");
    done(vec![there]);
}

#[test]
fn a_device_with_no_admin_phone_moves_a_session_the_same_way() {
    let pool = found().joined();
    let there = process(true);
    let moved = ok(rebind(&pool, &peer(&there)));
    assert_eq!(moved["moved"], true, "{moved}");
    assert!(pool.rig.node.lock().unwrap().approvals.all().is_empty() && pool.rig.pushed.sent().is_empty());
    done(vec![there]);
}

#[test]
fn a_move_from_a_process_that_cannot_be_read_or_for_a_session_nobody_registered_moves_nothing() {
    let pool = found();
    let there = process(true);
    assert_eq!(code(&rebind(&pool, &Peer::none())), "denied");
    let other = req_from(&mut pool.rig.node.lock().unwrap(), &peer(&there), "rebind", "demo", "", json!({"harness": "claude-code", "session": "nobody"}));
    assert_eq!(code(&other), "denied");
    assert!(lead_works(&pool), "neither ended the lead's token");
    done(vec![there]);
}
