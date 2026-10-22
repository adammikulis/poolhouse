//! What a request pays to find the pool current: a deadline compare and a throttle of one fold a second, so many requests fold a quiet pool
//! never and a pool whose hold has run out once.
mod kit;

use std::time::{Duration, Instant};

use kit::phone::FakePhone;
use kit::pool::found;
use serde_json::json;

const REQUESTS: usize = 400;

fn folds(pool: &kit::pool::Founded) -> u64 {
    pool.rig.node.lock().unwrap().members.gov().unwrap().folds()
}

fn through_the_phone_endpoint(pool: &kit::pool::Founded) {
    for _ in 0..REQUESTS {
        assert_eq!(pool.rig.phone("POST", "status", &pool.phone.token, json!({})).1["ok"], true);
    }
}

fn through_the_local_api(pool: &kit::pool::Founded) {
    for _ in 0..REQUESTS {
        assert_eq!(pool.rig.call("whoami", "demo", &pool.token, json!({}))["ok"], true);
    }
}

/// Many requests by ``entry`` to a pool whose second phone is in its hold: no fold while the hold has not run out, one when it has.
fn requests_fold_a_quiet_pool_never_and_an_expired_hold_once(entry: fn(&kit::pool::Founded)) {
    let pool = found();
    let mut second = FakePhone::new("second phone");
    pool.rig.add(&mut second, Some(&pool.phone));
    assert!(pool.rig.node.lock().unwrap().gov_view()["pending"].is_object(), "the listing of the second phone is in its hold");
    let before = folds(&pool);
    let began = Instant::now();
    entry(&pool);
    assert_eq!(folds(&pool), before, "a hold that has not run out costs a request no fold");
    pool.advance(25 * 3_600_000);
    entry(&pool);
    assert_eq!(folds(&pool), before + 1, "a hold that ran out is folded by the first request after it and by no other");
    assert!(pool.authority().contains(&second.fingerprint()), "the fold made the second phone an admin key");
    assert!(began.elapsed() < Duration::from_secs(20), "{} requests took {:?}", 2 * REQUESTS, began.elapsed());
}

#[test]
fn many_phone_requests_fold_a_pool_whose_hold_has_not_run_out_never_and_one_whose_hold_has_run_out_once() {
    requests_fold_a_quiet_pool_never_and_an_expired_hold_once(through_the_phone_endpoint);
}

#[test]
fn many_requests_of_the_local_api_fold_a_pool_whose_hold_has_not_run_out_never_and_one_whose_hold_has_run_out_once() {
    requests_fold_a_quiet_pool_never_and_an_expired_hold_once(through_the_local_api);
}

#[test]
fn the_tick_a_request_pays_is_a_compare_and_not_a_fold() {
    let pool = found();
    let before = folds(&pool);
    let began = Instant::now();
    for _ in 0..20_000 {
        pool.rig.node.lock().unwrap().gov_tick().unwrap();
    }
    assert_eq!(folds(&pool), before);
    assert!(began.elapsed() < Duration::from_secs(2), "twenty thousand ticks took {:?}", began.elapsed());
}
