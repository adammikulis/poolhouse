//! What a removed admin key leaves behind: a grant it signed stops counting when its removal takes effect, whatever the date it carries and
//! whether or not anything ticked the pool, and a revoke beats the grant it names in either order.
mod kit;

use ed25519_dalek::SigningKey;
use kit::gov::{entry_after, person, put, strong, public};
use kit::govnode::{approved, enrol_phone_key, found as found_dev, ANN, BEN};
use kit::grants::{Person, Spec};
use kit::netkit::device;
use kit::phone::FakePhone;
use kit::pool::{found, Founded};
use kit::*;
use serde_json::json;

const HOURS: i64 = 3_600_000;
const NOT_NOW: &str = "not an admin of this pool now";

/// A pool of two strong admin keys of one person with a grant of the second one live, and the removal of the second one decided but not yet
/// in force.
fn removal_pending() -> (Founded, FakePhone, String) {
    let pool = found();
    let mut second = FakePhone::new("second phone");
    pool.rig.add(&mut second, Some(&pool.phone));
    pool.add_admin(&second);
    assert_eq!(pool.authority().len(), 2, "both phones are admin keys here");
    pool.approve_with(&second, "sync_now", "*");
    let gid = pool.gids().remove(0);
    assert_eq!(pool.state(&gid).0, "live");
    ok(pool.act("admin_remove", json!({"key": second.public})));
    assert_eq!(pool.state(&gid).0, "live", "a removal that waits out its hold removes nothing yet");
    (pool, second, gid)
}

#[test]
fn a_removed_admins_earlier_grant_stops_counting_when_the_removal_takes_effect() {
    let (pool, second, gid) = removal_pending();
    pool.pass(48 * HOURS);
    assert_eq!(pool.authority(), vec![pool.phone.fingerprint()]);
    let (state, why) = pool.state(&gid);
    assert_eq!(state, "ignored");
    assert!(why.contains(NOT_NOW), "{why}");
    let spec = Spec::session(&pool.name, "sync_now");
    pool.append_approved("g-00000000000000f1", &spec.window(-3_600_000, 3_600_000), &second);
    let (state, why) = pool.state("g-00000000000000f1");
    assert!((state.as_str(), why.contains(NOT_NOW)) == ("ignored", true), "a grant dated before the removal, arriving after it: {state} {why}");
}

#[test]
fn a_removal_whose_hold_ran_out_unticked_is_seen_by_the_local_api() {
    let (pool, _second, gid) = removal_pending();
    pool.advance(48 * HOURS);
    let (state, why) = pool.state(&gid);
    assert!((state.as_str(), why.contains(NOT_NOW)) == ("ignored", true), "the request ticks the pool first: {state} {why}");
}

#[test]
fn a_removal_whose_hold_ran_out_unticked_is_seen_by_the_phone_endpoint() {
    let (pool, second, _gid) = removal_pending();
    let id = pool.request("task_authority", "*");
    let seen = pool.rig.get(&pool.phone, &id);
    pool.advance(48 * HOURS);
    let refused = pool.rig.phone("POST", "decide", "", second.decision(&seen, "approve")).1;
    assert!(Founded::denial(&refused).contains("not an admin key of this pool"), "{refused}");
    assert_eq!(pool.rig.node.lock().unwrap().approvals.get(&id).unwrap().state, "pending");
}

#[test]
fn a_revoke_beats_the_grant_it_names_whichever_arrives_first() {
    let pool = found();
    let spec = Spec::session(&pool.name, "sync_now");
    let revoke = |gid: &str| pool.rig.call("grant_revoke", "demo", &pool.token, pool.typed.revoke_params("demo", gid, "ended"));
    pool.approve_with(&pool.phone, "sync_now", "*");
    let first = pool.gids().remove(0);
    assert_eq!(revoke(&first)["ok"], true);
    assert_eq!(pool.state(&first).0, "revoked", "the grant came first");
    let ahead = "g-00000000000000f2";
    let mut row = pool.typed.revoke_params("demo", ahead, "ended").as_object().unwrap().clone();
    row.insert("op".into(), json!("revoke"));
    row.insert("board".into(), json!("demo"));
    pool.rig.node.lock().unwrap().host("demo").unwrap().board.append_person(poolhouse_node::row::Kind::Grant, &pool.name, serde_json::Value::Object(row)).unwrap();
    pool.append_approved(ahead, &spec, &pool.phone);
    assert_eq!(pool.state(ahead).0, "revoked", "the revoke came first");
}

#[test]
fn a_removal_whose_hold_ran_out_unticked_is_seen_by_the_network_path() {
    let dev = device();
    found_dev(&dev, person(ANN, "ann"), "secure");
    enrol_phone_key(&dev, BEN);
    {
        let mut node = dev.node.lock().unwrap();
        let gov = node.members.gov_mut().unwrap();
        let entry = entry_after(strong(BEN, "ann"), gov.context().0 + 1, gov);
        put(gov, "admin_add", json!({"entry": entry}), &[ANN]);
        kit::gov::advance(gov, 48 * HOURS);
    }
    let (_, session) = dev.session("demo", "syncer");
    let ben = Person { key: SigningKey::from_bytes(&[BEN; 32]) };
    approved(&mut |m, b, t, p| dev.call(m, b, t, p), "demo", &session, &["sync_now"], &|made| ben.decision(made));
    assert_eq!(dev.call("sync_now", "", &session, json!({}))["ok"], true, "while ben is an admin the grant counts");
    {
        let mut node = dev.node.lock().unwrap();
        let gov = node.members.gov_mut().unwrap();
        put(gov, "admin_remove", json!({"key": public(BEN), "cut": []}), &[ANN]);
        gov.clock.advance_ms(48 * HOURS);
    }
    let after = dev.call("sync_now", "", &session, json!({}));
    assert_eq!(after["ok"], false, "the removal is in force once the clock says so, ticked or not: {after}");
}
