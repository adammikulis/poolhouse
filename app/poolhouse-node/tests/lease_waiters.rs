//! A device answers a bounded number of `lease_wait` requests at once, and the processes of one process tree a share of them; each holds a
//! thread while it waits.

#[cfg(unix)]
mod kit;

use poolhouse_node::lease::rpc::{serve, MOST_WAITERS, MOST_WAITERS_PER_LINEAGE};
use poolhouse_node::lineage::Peer;
use serde_json::{json, Value};
use std::time::{Duration, Instant};

/// The places are the device's, so the tests of them take turns.
static TURN: std::sync::Mutex<()> = std::sync::Mutex::new(());

fn queued(_: &Value) -> poolhouse_node::error::Result<Value> {
    Ok(json!({"v": 1, "id": 1, "ok": true, "result": {"state": "queued"}}))
}

fn wait(ms: u64) -> Value {
    json!({"v": 1, "id": 1, "method": "lease_wait", "params": {"id": "l1", "timeout_ms": ms}})
}

#[test]
fn a_device_waits_for_a_bounded_number_of_leases_at_once_and_says_which_limit_it_is() {
    let _turn = TURN.lock().unwrap_or_else(|e| e.into_inner());
    let waiters: Vec<_> = (0..MOST_WAITERS).map(|_| std::thread::spawn(|| serve(&queued, &wait(1500), &Peer::none()).unwrap())).collect();
    std::thread::sleep(Duration::from_millis(400));
    let started = Instant::now();
    let refused = serve(&queued, &wait(1500), &Peer::none()).unwrap();
    assert!(started.elapsed() < Duration::from_millis(500), "the refusal is at once: {:?}", started.elapsed());
    assert_eq!(refused["ok"], false, "{refused}");
    assert_eq!(refused["error"]["code"], "quota", "{refused}");
    let said = refused["error"]["message"].as_str().unwrap();
    assert!(said.contains(&format!("the limit is {MOST_WAITERS}, set by MOST_WAITERS in lease/rpc.rs")), "{said}");
    let polled = serve(&queued, &wait(0), &Peer::none()).unwrap();
    assert_eq!(polled["ok"], true, "a request that does not wait is never counted: {polled}");
    for w in waiters {
        assert_eq!(w.join().unwrap()["result"]["state"], "queued");
    }
    let again = serve(&queued, &wait(50), &Peer::none()).unwrap();
    assert_eq!(again["ok"], true, "the waiters ended, so a place is free: {again}");
}

#[cfg(unix)]
#[test]
fn one_process_tree_waits_for_no_more_than_its_share_of_the_places_and_another_tree_still_waits() {
    let _turn = TURN.lock().unwrap_or_else(|e| e.into_inner());
    use kit::procs::{done, peer, process};
    let (flood_tree, calm_tree) = (process(true), process(true));
    let (flooding, calm) = (peer(&flood_tree), peer(&calm_tree));
    let waiters: Vec<_> = (0..MOST_WAITERS_PER_LINEAGE).map(|_| std::thread::spawn(move || serve(&queued, &wait(1500), &flooding).unwrap())).collect();
    std::thread::sleep(Duration::from_millis(400));
    let started = Instant::now();
    let refused = serve(&queued, &wait(1500), &flooding).unwrap();
    assert!(started.elapsed() < Duration::from_millis(500), "the refusal is at once: {:?}", started.elapsed());
    assert_eq!(refused["error"]["code"], "quota", "{refused}");
    let said = refused["error"]["message"].as_str().unwrap();
    assert!(said.contains(&format!("the limit is {MOST_WAITERS_PER_LINEAGE}, set by MOST_WAITERS_PER_LINEAGE in lease/rpc.rs")), "{said}");
    let elsewhere = serve(&queued, &wait(50), &calm).unwrap();
    assert_eq!(elsewhere["ok"], true, "another process tree still has places: {elsewhere}");
    for w in waiters {
        assert_eq!(w.join().unwrap()["result"]["state"], "queued");
    }
    let again = serve(&queued, &wait(50), &flooding).unwrap();
    assert_eq!(again["ok"], true, "the waiters ended, so the tree has its places again: {again}");
    done(vec![flood_tree, calm_tree]);
}
