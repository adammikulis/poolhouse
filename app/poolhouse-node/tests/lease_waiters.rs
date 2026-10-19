//! A device answers a bounded number of `lease_wait` requests at once; each holds a thread while it waits.

use poolhouse_node::lease::rpc::{serve, MOST_WAITERS};
use serde_json::{json, Value};
use std::time::{Duration, Instant};

fn queued(_: &Value) -> poolhouse_node::error::Result<Value> {
    Ok(json!({"v": 1, "id": 1, "ok": true, "result": {"state": "queued"}}))
}

fn wait(ms: u64) -> Value {
    json!({"v": 1, "id": 1, "method": "lease_wait", "params": {"id": "l1", "timeout_ms": ms}})
}

#[test]
fn a_device_waits_for_a_bounded_number_of_leases_at_once_and_says_which_limit_it_is() {
    let waiters: Vec<_> = (0..MOST_WAITERS).map(|_| std::thread::spawn(|| serve(&queued, &wait(1500)).unwrap())).collect();
    std::thread::sleep(Duration::from_millis(400));
    let started = Instant::now();
    let refused = serve(&queued, &wait(1500)).unwrap();
    assert!(started.elapsed() < Duration::from_millis(500), "the refusal is at once: {:?}", started.elapsed());
    assert_eq!(refused["ok"], false, "{refused}");
    assert_eq!(refused["error"]["code"], "quota", "{refused}");
    let said = refused["error"]["message"].as_str().unwrap();
    assert!(said.contains(&format!("the limit is {MOST_WAITERS}, set by MOST_WAITERS in lease/rpc.rs")), "{said}");
    let polled = serve(&queued, &wait(0)).unwrap();
    assert_eq!(polled["ok"], true, "a request that does not wait is never counted: {polled}");
    for w in waiters {
        assert_eq!(w.join().unwrap()["result"]["state"], "queued");
    }
    let again = serve(&queued, &wait(50)).unwrap();
    assert_eq!(again["ok"], true, "the waiters ended, so a place is free: {again}");
}
