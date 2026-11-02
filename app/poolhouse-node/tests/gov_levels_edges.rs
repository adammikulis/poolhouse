//! The edges of claims and floors that a non-author review broke: a removal that leaves a class out of reach, a recovery key that states a
//! claim, and a joiner that must honor a delegate as the others do.

mod kit;

use kit::gov::*;
use poolhouse_node::gov::act::Act;
use poolhouse_node::gov::keys::{Level, Role};
use poolhouse_node::gov::pool::{ActStatus, GovPool, Ingest};
use poolhouse_node::membership::Policy;
use serde_json::json;

const A: u8 = 1;
const B: u8 = 2;
const D: u8 = 4;

fn add(g: &GovPool, k: K, seeds: &[u8]) -> Act {
    let since = g.state().chain.latest().n + 1;
    make(g, "admin_add", json!({"entry": entry_after(k, since, g)}), seeds)
}

#[test]
fn a_removal_that_leaves_a_class_out_of_reach_is_void() {
    let (_d, mut g) = pool_with_two_needed();
    let removal = make(&g, "admin_remove", json!({"key": public(B), "cut": []}), &[A]);
    assert_eq!(take(&mut g, removal.clone()), Ingest::Accepted);
    advance(&mut g, 8 * DAY);
    assert!(admins(&g).contains(&public(B)), "the second key stays listed");
    assert!(matches!(g.status(&removal.id()), ActStatus::Void(w) if w.contains("too few admins")), "{:?}", g.status(&removal.id()));
    assert!(g.state().chain.latest().charter.reachable(), "every class stays within reach of the keys listed");
    let laptop = ident(10);
    put_admit(&mut g, &laptop, &[A, B]);
    assert!(is_member(&g, &laptop), "both people still admit a device");
}

/// Two people, each with a key that counts, and sensitive acts that need both.
fn pool_with_two_needed() -> (tempfile::TempDir, GovPool) {
    let keys = [phone_admin(A, "p1"), computer_admin(B, "p2")];
    let mut c = charter(&keys, Policy::Secure, poolhouse_node::gov::charter::Unit::Person);
    c.thresholds.sensitive = 2;
    let anchor = c.anchor();
    c.admins = keys.iter().map(|k| entry(*k, 1, &anchor)).collect();
    c.admins.sort_by(|a, b| a.key.cmp(&b.key));
    pool_of(&c)
}

#[test]
fn a_recovery_key_that_states_a_claim_is_no_recovery_key() {
    let (_d, mut g) = pool(&[computer_admin(A, "ann")], Policy::Secure);
    let since = g.state().chain.latest().n + 1;
    let mut entry = entry_after(recovery(D, "ann"), since, &g);
    entry.claim = Some(claim_at(Level::Computer));
    entry.acceptance = poolhouse_node::gov::signer::RigSigner::sign_as(D, &entry.acceptance_message(&g.id, &g.context().1));
    let act = make(&g, "admin_add", json!({"entry": entry}), &[A]);
    let kept = take(&mut g, act.clone());
    assert!(matches!(kept, Ingest::Refused(ref why) if why.contains("no claim")) || matches!(g.status(&act.id()), ActStatus::Void(_)), "{kept:?} {:?}", g.status(&act.id()));
    advance(&mut g, 8 * DAY);
    assert!(!g.state().chain.latest().charter.admins.iter().any(|k| k.role == Role::Recovery), "no recovery key was listed");

    let honest = add(&g, recovery(D, "ann"), &[A]);
    take(&mut g, honest);
    advance(&mut g, 8 * DAY);
    assert!(g.state().chain.latest().charter.admins.iter().any(|k| k.role == Role::Recovery && k.claim.is_none()), "a recovery key states none and counts for nothing");
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
    for (label, keys) in [("computer", vec![computer_admin(A, "ann")]), ("phone", vec![phone_admin(A, "ann")])] {
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
