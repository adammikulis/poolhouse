//! The edges of levels and floors that a non-author review broke: a removal that leaves a class out of reach, a recovery key that claims a
//! level its listers do not hold, two acts that cannot both hold ranked by level, and a joiner that must honor a delegate as the others do.

mod kit;

use kit::gov::*;
use poolhouse_node::gov::act::Act;
use poolhouse_node::gov::charter::{Charter, Floors};
use poolhouse_node::gov::keys::{Level, Role};
use poolhouse_node::gov::pool::{ActStatus, GovPool, Ingest};
use poolhouse_node::membership::Policy;
use serde_json::json;

const A: u8 = 1;
const B: u8 = 2;
const C: u8 = 3;
const D: u8 = 4;

fn add(g: &GovPool, k: K, seeds: &[u8]) -> Act {
    let since = g.state().chain.latest().n + 1;
    make(g, "admin_add", json!({"entry": entry_after(k, since, g)}), seeds)
}

fn all(level: Level) -> Floors {
    Floors { routine: level, sensitive: level, governance: level }
}

fn floors(g: &GovPool) -> Floors {
    g.state().chain.latest().charter.floors
}

/// Two phone people and a computer key under a phone floor for sensitive acts, which need both phone people.
fn two_phones_needed() -> Charter {
    let keys = [phone_admin(A, "p1"), phone_admin(B, "p2"), computer_admin(C, "c")];
    let mut c = charter_floored(&keys, Policy::Secure, Floors { routine: Level::Computer, sensitive: Level::Phone, governance: Level::Computer });
    c.thresholds.sensitive = 2;
    let anchor = c.anchor();
    c.admins = keys.iter().map(|k| entry(*k, 1, &anchor)).collect();
    c.admins.sort_by(|a, b| a.key.cmp(&b.key));
    c
}

#[test]
fn a_removal_that_leaves_a_class_out_of_reach_is_void() {
    let (_d, mut g) = pool_of(&two_phones_needed());
    let removal = make(&g, "admin_remove", json!({"key": public(B), "cut": []}), &[A, C]);
    assert_eq!(take(&mut g, removal.clone()), Ingest::Accepted);
    advance(&mut g, 25 * HOUR);
    assert!(admins(&g).contains(&public(B)), "the second phone key stays listed");
    assert!(matches!(g.status(&removal.id()), ActStatus::Void(w) if w.contains("too few admins")), "{:?}", g.status(&removal.id()));
    assert!(g.state().chain.latest().charter.reachable(), "every class stays within reach of the keys listed");
    let laptop = ident(10);
    put_admit(&mut g, &laptop, &[A, B]);
    assert!(is_member(&g, &laptop), "both phone people still admit a device");
}

#[test]
fn a_recovery_key_is_listed_at_no_level_above_the_keys_that_listed_it() {
    let (_d, mut g) = pool(&[computer_admin(A, "ann")], Policy::Secure);
    let claim = add(&g, recovery(D, "ann"), &[A]);
    assert_eq!(take(&mut g, claim.clone()), Ingest::Accepted);
    assert!(matches!(g.status(&claim.id()), ActStatus::Void(w) if w.contains("no level above")), "{:?}", g.status(&claim.id()));
    advance(&mut g, 8 * DAY);
    assert!(!g.state().chain.latest().charter.admins.iter().any(|k| k.role == Role::Recovery), "a computer key lists no phone-level recovery key");

    let honest = add(&g, K(D, "ann", Role::Recovery, Level::Computer), &[A]);
    take(&mut g, honest);
    advance(&mut g, 8 * DAY);
    assert!(g.state().chain.latest().charter.admins.iter().any(|k| k.role == Role::Recovery && k.assurance == Level::Computer), "it lists a recovery key at its own level");
}

#[test]
fn a_replacement_recovery_key_is_listed_at_no_level_above_the_keys_that_listed_it() {
    let (_d, mut g) = pool(&[computer_admin(A, "ann"), K(D, "ann", Role::Recovery, Level::Computer)], Policy::Secure);
    let since = g.state().chain.latest().n + 1;
    let claim = make(&g, "recovery_replace", json!({"entry": entry_after(recovery(C, "ann"), since, &g)}), &[A]);
    take(&mut g, claim.clone());
    assert!(matches!(g.status(&claim.id()), ActStatus::Void(w) if w.contains("no level above")), "{:?}", g.status(&claim.id()));
}

#[test]
fn an_act_signed_at_a_higher_level_outranks_one_with_more_signers() {
    let loose = charter_floored(&[phone_admin(A, "ann"), computer_admin(B, "ann"), computer_admin(C, "ann")], Policy::Secure, all(Level::Computer));
    let (_d, mut g) = pool_of(&loose);
    let thief = add(&g, computer_admin(D, "mal"), &[B, C]);
    let defence = make(&g, "set_floors", json!({"sensitive": "phone", "governance": "phone"}), &[A]);
    take(&mut g, thief.clone());
    take(&mut g, defence.clone());
    advance(&mut g, 8 * DAY);
    assert_eq!(floors(&g).sensitive, Level::Phone, "the phone key's raise of the floor took effect");
    assert!(!admins(&g).contains(&public(D)), "the act two computer keys signed did not");
    assert!(matches!(g.status(&thief.id()), ActStatus::Void(_)), "{:?}", g.status(&thief.id()));
}

/// A pool of ``keys`` with a delegate device granted by key `A`, and a joiner that took its history as settled.
fn delegated(keys: &[K]) -> (GovPool, GovPool, tempfile::TempDir, tempfile::TempDir, poolhouse_node::cert::Identity) {
    let (od, mut owner) = pool(keys, Policy::Secure);
    let delegate = ident(20);
    put_admit(&mut owner, &delegate, &[A]);
    put(&mut owner, "delegate", json!({"fingerprint": delegate.fingerprint()}), &[A]);
    advance(&mut owner, 8 * DAY);
    let jd = tempfile::tempdir().unwrap();
    let mut joiner = GovPool::create(jd.path(), found_act(&owner.state().chain.epochs[0].charter)).unwrap();
    joiner.clock.freeze();
    let acts: Vec<Act> = owner.acts().values().filter(|a| a.kind != "found").cloned().collect();
    joiner.ingest_many(acts, poolhouse_node::poolops::wall_ms(), true);
    (owner, joiner, od, jd, delegate)
}

/// Whether ``g`` honors an admit of a new device that the delegate (made from seed 20) signed.
fn delegate_admits(g: &mut GovPool, seed: u8) -> bool {
    let guest = ident(seed);
    let mut act = g.new_act("admit", admit_body(&guest, "guest", "join-code", ""), poolhouse_node::poolops::wall_ms()).unwrap();
    device_sign(&mut act, 20);
    take(g, act);
    is_member(g, &guest)
}

#[test]
fn a_joiner_honors_a_delegate_for_thirty_days_as_the_others_do() {
    for (label, keys) in [("computer only", vec![computer_admin(A, "ann")]), ("phone", vec![phone_admin(A, "ann")])] {
        let (mut owner, mut joiner, _od, _jd, delegate) = delegated(&keys);
        assert!(joiner.state().roster.delegates.contains_key(&delegate.fingerprint()), "{label}: the joiner knows the delegate");
        advance(&mut owner, 21 * DAY);
        advance(&mut joiner, 21 * DAY);
        assert!(delegate_admits(&mut owner, 40), "{label}: the others honor the delegate inside its 30 days");
        assert!(delegate_admits(&mut joiner, 40), "{label}: so does the joiner");
        advance(&mut owner, 10 * DAY);
        advance(&mut joiner, 10 * DAY);
        assert!(!delegate_admits(&mut owner, 41), "{label}: the others refuse it after its 30 days");
        assert!(!delegate_admits(&mut joiner, 41), "{label}: so does the joiner");
        assert!(!joiner.state().roster.delegates.contains_key(&delegate.fingerprint()), "{label}: the delegate has ended for the joiner");
    }
}
