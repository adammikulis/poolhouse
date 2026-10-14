//! An act's signatures are good until its signed `not_after`: a person's signer hands a pool a new act, or a new signature, only
//! before it, a charter is founded only while its acceptance is fresh, and what a member relays is taken as it comes.

mod kit;

use kit::gov::*;
use kit::govnode::enrol_phone_key;
use kit::netkit::*;
use poolhouse_node::gov::act::{Act, LONG_WINDOW_MS, WINDOW_MS};
use poolhouse_node::gov::charter::Unit;
use poolhouse_node::gov::pool::Ingest;
use poolhouse_node::membership::Policy;
use poolhouse_node::poolops::wall_ms;
use serde_json::json;

const A: u8 = 1;
const B: u8 = 2;
const HOUR_U: u64 = 3_600_000;

fn refused(i: Ingest) -> String {
    match i {
        Ingest::Refused(why) => why,
        other => panic!("expected a refusal, got {other:?}"),
    }
}

/// An `admit` of a new device written ``ago`` ms ago and signed by ann.
fn old_admit(g: &poolhouse_node::gov::pool::GovPool, ago: u64) -> Act {
    let mut act = g.new_act("admit", admit_body(&ident(10), "laptop", "join-code", ""), wall_ms() - ago).unwrap();
    sign(&mut act, A);
    act
}

#[test]
fn an_act_signed_and_kept_past_its_window_is_refused_when_a_person_hands_it_in() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let stale = old_admit(&g, 2 * HOUR_U);
    assert_eq!(stale.not_after - stale.at_ms, WINDOW_MS, "an act a person signs alone is good for an hour");
    assert!(refused(take(&mut g, stale.clone())).contains("ran out"), "the signature was good for an hour and it has been two");
    assert!(!is_member(&g, &ident(10)), "so the device is not in");
    let fresh = old_admit(&g, 10 * 60_000);
    assert_eq!(take(&mut g, fresh), Ingest::Accepted, "the same act inside its hour is taken");
}

#[test]
fn a_window_a_signer_did_not_sign_is_no_window() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let mut stale = old_admit(&g, 2 * HOUR_U);
    stale.not_after = wall_ms() + HOUR_U;
    assert!(refused(take(&mut g, stale)).contains("does not verify"), "moving not_after after the signature makes the signature fail");
    let mut resigned = g.new_act("admit", admit_body(&ident(10), "laptop", "join-code", ""), wall_ms() - 2 * HOUR_U).unwrap();
    resigned.not_after = wall_ms() + HOUR_U;
    sign(&mut resigned, A);
    assert_eq!(take(&mut g, resigned), Ingest::Accepted, "a window the key signed is the window: the signer chose a long one and the pool takes it");
}

#[test]
fn a_window_that_is_empty_or_longer_than_a_week_is_no_act() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    for (what, window) in [("none", 0), ("a week and a millisecond", LONG_WINDOW_MS + 1), ("a year", 365 * 24 * HOUR_U)] {
        let mut act = g.new_act("admit", admit_body(&ident(10), "laptop", "join-code", ""), wall_ms()).unwrap();
        act.not_after = act.at_ms + window;
        sign(&mut act, A);
        assert!(refused(take(&mut g, act)).contains("good for a time"), "{what}");
    }
    let mut before = g.new_act("admit", admit_body(&ident(10), "laptop", "join-code", ""), wall_ms()).unwrap();
    before.not_after = before.at_ms - 1;
    sign(&mut before, A);
    assert!(refused(take(&mut g, before)).contains("good for a time"), "a window that ended before the act was written");
    let mut week = g.new_act("admit", admit_body(&ident(10), "laptop", "join-code", ""), wall_ms()).unwrap();
    week.not_after = week.at_ms + LONG_WINDOW_MS;
    sign(&mut week, A);
    assert_eq!(take(&mut g, week), Ingest::Accepted, "a week exactly is the most");
}

#[test]
fn an_act_of_the_version_before_is_refused_in_one_sentence() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let mut old = g.new_act("admit", admit_body(&ident(10), "laptop", "join-code", ""), wall_ms()).unwrap();
    old.v = 1;
    sign(&mut old, A);
    assert!(refused(take(&mut g, old)).contains("another version of Poolhouse"));
    let without: Result<Act, _> = serde_json::from_value(json!({"v": 2, "pool": g.id, "epoch": 1, "kind": "admit", "body": {}, "at_ms": 1, "nonce": "00".repeat(16), "prev": "", "signatures": []}));
    assert!(without.is_err(), "an act with no window is not read");
}

#[test]
fn a_second_signature_after_the_window_is_refused_and_one_inside_it_is_merged() {
    let (_d, mut g) = pool(&[strong(A, "ann"), strong(B, "ben")], Policy::Secure);
    let added = entry_after(strong(5, "eve"), 2, &g);
    let act = make(&g, "admin_add", json!({"entry": added}), &[A]);
    assert_eq!(act.not_after - act.at_ms, LONG_WINDOW_MS, "an act that needs two people is good for a week");
    assert_eq!(take(&mut g, act.clone()), Ingest::Accepted);
    let mut both = act.clone();
    sign(&mut both, B);
    let in_a_week = wall_ms() + LONG_WINDOW_MS - HOUR_U;
    let in_eight_days = wall_ms() + LONG_WINDOW_MS + 24 * HOUR_U;
    assert!(refused(g.ingest(both.clone(), in_eight_days)).contains("ran out"), "ben's signature comes after the week and adds nothing");
    assert_eq!(g.ingest(both, in_a_week), Ingest::Merged, "inside the week it is added");
}

#[test]
fn an_act_that_is_already_held_is_known_after_its_window_not_refused() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let act = old_admit(&g, 0);
    assert_eq!(take(&mut g, act.clone()), Ingest::Accepted);
    assert_eq!(g.ingest(act, wall_ms() + 30 * 24 * HOUR_U), Ingest::Known, "a repeat changes nothing, so it is no refusal");
}

#[test]
fn what_a_member_relays_is_taken_after_its_window_so_a_removal_reaches_a_device_that_was_away() {
    let c = charter(&[strong(A, "ann")], Policy::Secure, Unit::Person);
    let ((_d, mut g), (_e, mut away)) = (pool_of(&c), pool_of(&c));
    let (laptop, phone) = (ident(10), ident(11));
    let admits: Vec<Act> = [&laptop, &phone].iter().map(|d| admit(&g, d, &[A])).collect();
    admits.iter().for_each(|a| drop(take(&mut g, a.clone())));
    away.ingest_many(admits, wall_ms(), true);
    let gone = make(&g, "remove", json!({"fingerprint": laptop.fingerprint()}), &[A]);
    let also = make(&g, "remove", json!({"fingerprint": phone.fingerprint()}), &[A]);
    let a_month_later = wall_ms() + 30 * 24 * HOUR_U;
    away.ingest_many(vec![gone], a_month_later, false);
    assert!(is_removed(&away, &laptop), "the removal was signed a month ago and the device that was away takes it from the member that relays it");
    assert!(refused(away.ingest(also, a_month_later)).contains("ran out"), "an act handed in by a person that late is refused");
    assert!(!is_removed(&away, &phone));
}

fn drafted_at(dev: &Dev, at_ms: u64) -> serde_json::Value {
    charter_of(&[strong(A, "ann")], Policy::Secure, Unit::Person, at_ms, first_of(&dev.net.me.der)).body()
}

#[test]
fn a_charter_whose_acceptance_is_older_than_an_hour_or_dated_ahead_founds_nothing() {
    let a = device();
    enrol_phone_key(&a, A);
    let before = a.pool();
    for (what, at_ms) in [("two hours old", wall_ms() - 2 * HOUR_U), ("ten minutes ahead", wall_ms() + 10 * 60_000)] {
        let refused = a.call("pool_found", "", &a.token, json!({"charter": drafted_at(&a, at_ms)}));
        assert_eq!(refused["ok"], false, "{what}: {refused}");
        assert!(refused["error"]["say"].as_str().unwrap().contains("too long ago or is dated ahead"), "{what}: {refused}");
        assert_eq!(a.pool(), before, "{what}: it is still in no pool");
    }
    let ok = a.call("pool_found", "", &a.token, json!({"charter": drafted_at(&a, wall_ms() - 10 * 60_000)}));
    assert_eq!(ok["ok"], true, "a charter signed ten minutes ago founds: {ok}");
}
