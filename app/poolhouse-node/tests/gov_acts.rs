//! The rules for the acts of a governed pool, against a real pool on disk with its awake clock frozen and moved by hand: what each
//! kind needs to take effect, and what a forged, replayed, below-threshold, backdated or cloned act does.

mod kit;

use kit::gov::*;
use poolhouse_node::gov::charter::Unit;
use poolhouse_node::gov::keys::Role;
use poolhouse_node::gov::pool::{ActStatus, Ingest};
use poolhouse_node::gov::signer::RigSigner;
use poolhouse_node::membership::Policy;
use poolhouse_node::poolops::wall_ms;
use serde_json::json;

const A: u8 = 1;
const B: u8 = 2;
const C: u8 = 3;
const D: u8 = 4;

fn refused(i: Ingest) -> String {
    match i {
        Ingest::Refused(why) => why,
        other => panic!("expected a refusal, got {other:?}"),
    }
}

/// An `admin_add` of the key ``k`` signed by ``seeds`` and the key's own acceptance.
fn add(g: &poolhouse_node::gov::pool::GovPool, k: K, seeds: &[u8]) -> poolhouse_node::gov::act::Act {
    let since = g.state().chain.latest().n + 1;
    let anchor = g.id.clone();
    make(g, "admin_add", json!({"entry": entry(k, since, &anchor)}), seeds)
}

fn put_add(g: &mut poolhouse_node::gov::pool::GovPool, k: K, seeds: &[u8]) -> Ingest {
    let act = add(g, k, seeds);
    take(g, act)
}

#[test]
fn a_pool_id_is_the_hash_of_its_first_charter_so_a_copy_with_other_admins_is_another_pool() {
    let one = charter(&[strong(A, "ann")], Policy::Secure, Unit::Person);
    let other = charter(&[strong(B, "ben")], Policy::Secure, Unit::Person);
    assert_eq!(one.pool_id().len(), 32);
    assert_ne!(one.pool_id(), other.pool_id());
    let (_d, g) = pool_of(&one);
    assert_eq!(g.id, one.pool_id());
    let mut copy = one.clone();
    copy.admins = other.admins.clone();
    assert_ne!(copy.pool_id(), one.pool_id(), "other admins, another pool");
    assert!(poolhouse_node::gov::pool::founding(&found_act(&copy)).is_err(), "and the acceptances of the other admins were made for the other charter");
}

#[test]
fn a_charter_whose_key_did_not_accept_it_founds_nothing() {
    let mut c = charter(&[strong(A, "ann"), strong(B, "ben")], Policy::Secure, Unit::Person);
    c.admins[0].acceptance = c.admins[1].acceptance.clone();
    let dir = tempfile::tempdir().unwrap();
    assert!(poolhouse_node::gov::pool::GovPool::create(dir.path(), found_act(&c)).is_err(), "a key is listed only with its own acceptance");
}

// -- who may sign what ---------------------------------------------------------------------------------------------------

#[test]
fn a_member_with_no_admin_key_cannot_remove_admit_or_change_admins() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let laptop = ident(10);
    put_admit(&mut g, &laptop, &[A]);
    assert!(is_member(&g, &laptop));
    let mut sneaky = g.new_act("remove", json!({"fingerprint": laptop.fingerprint()}), wall_ms()).unwrap();
    sign(&mut sneaky, 99);
    assert!(refused(take(&mut g, sneaky)).contains("not an admin"), "a key the pool never listed signs nothing");
    let mut by_device = g.new_act("remove", json!({"fingerprint": laptop.fingerprint()}), wall_ms()).unwrap();
    device_sign(&mut by_device, 10);
    assert!(refused(take(&mut g, by_device)).contains("signed by a device"), "a member's device signs its own leave, never a removal");
    assert!(is_member(&g, &laptop));
    let stranger = ident(11);
    let mut self_admit = g.new_act("admit", kit::gov::admit_body(&stranger, "mine", "join-code", ""), wall_ms()).unwrap();
    sign(&mut self_admit, 98);
    assert!(refused(take(&mut g, self_admit)).contains("not an admin"));
    assert!(!is_member(&g, &stranger));
}

#[test]
fn a_forged_act_is_refused_and_recorded_with_its_reason() {
    let (_d, mut g) = pool(&[strong(A, "ann"), strong(B, "ben")], Policy::Secure);
    let laptop = ident(10);
    put_admit(&mut g, &laptop, &[A]);
    let good = make(&g, "remove", json!({"fingerprint": laptop.fingerprint()}), &[A]);
    let mut wrong_pool = good.clone();
    wrong_pool.pool = "0".repeat(32);
    assert!(refused(take(&mut g, wrong_pool)).contains("another pool"));
    let mut bad_signature = good.clone();
    bad_signature.signatures[0].sig = "ab".repeat(64);
    assert!(refused(take(&mut g, bad_signature)).contains("does not verify"));
    let mut wrong_epoch = g.new_act("remove", json!({"fingerprint": laptop.fingerprint()}), wall_ms()).unwrap();
    wrong_epoch.prev = "ab".repeat(32);
    sign(&mut wrong_epoch, A);
    assert!(refused(take(&mut g, wrong_epoch)).contains("never had"));
    let mut tampered = good.clone();
    tampered.body = json!({"fingerprint": "ab".repeat(32)});
    assert!(refused(take(&mut g, tampered)).contains("does not verify"), "a signature covers the body");
    assert!(is_member(&g, &laptop), "none of them took effect");
    assert!(g.refusals().len() >= 4, "each is recorded: {:?}", g.refusals());
    assert!(g.refusals().iter().all(|r| !r.why.is_empty()));
}

#[test]
fn a_replayed_act_is_a_no_op_and_a_signature_counted_twice_counts_once() {
    let (_d, mut g) = pool(&[strong(A, "ann"), strong(B, "ben")], Policy::Secure);
    let laptop = ident(10);
    let admit_act = admit(&g, &laptop, &[A]);
    assert_eq!(take(&mut g, admit_act.clone()), Ingest::Accepted);
    let before = g.acts().len();
    assert_eq!(take(&mut g, admit_act), Ingest::Known);
    assert_eq!(g.acts().len(), before);
    let mut twice = make(&g, "admin_add", json!({"entry": entry(strong(C, "cy"), 2, &g.id)}), &[A]);
    let dup = twice.signatures[0].clone();
    twice.signatures.push(dup);
    take(&mut g, twice.clone());
    advance(&mut g, 25 * HOUR);
    assert!(!admins(&g).contains(&public(C)), "one person signed it twice; two are needed once a second person is listed");
    assert!(matches!(g.status(&twice.id()), ActStatus::Short(1, 2) | ActStatus::Void(_)), "{:?}", g.status(&twice.id()));
}

#[test]
fn an_act_that_names_an_epoch_this_pool_has_not_reached_waits_and_one_that_follows_a_charter_it_never_had_is_refused() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let mut ahead = poolhouse_node::gov::act::Act::new(&g.id, 5, "remove", json!({"fingerprint": "ab".repeat(32)}), wall_ms(), &"cd".repeat(32)).unwrap();
    sign(&mut ahead, A);
    assert_eq!(take(&mut g, ahead), Ingest::Waiting);
    let mut sideways = poolhouse_node::gov::act::Act::new(&g.id, 1, "remove", json!({"fingerprint": "ab".repeat(32)}), wall_ms(), &"cd".repeat(32)).unwrap();
    sign(&mut sideways, A);
    assert!(refused(take(&mut g, sideways)).contains("never had"));
}

// -- thresholds ----------------------------------------------------------------------------------------------------------

#[test]
fn a_sensitive_act_needs_both_people_once_there_are_two_and_one_person_with_two_keys_counts_once() {
    let (_d, mut g) = pool(&[strong(A, "ann"), strong(B, "ann"), strong(C, "ben")], Policy::Secure);
    assert_eq!(g.state().chain.latest().charter.needed(poolhouse_node::gov::charter::Class::Sensitive), 2);
    let act = make(&g, "set_holds", json!({"admin_add_h": 48}), &[A, B]);
    take(&mut g, act.clone());
    advance(&mut g, 49 * HOUR);
    assert_eq!(g.state().chain.latest().charter.holds.admin_add_h, 24, "ann's two keys are one person");
    assert_eq!(g.status(&act.id()), ActStatus::Short(1, 2));
    let mut both = act.clone();
    sign(&mut both, C);
    assert_eq!(take(&mut g, both), Ingest::Merged);
    advance(&mut g, 25 * HOUR);
    assert_eq!(g.state().chain.latest().charter.holds.admin_add_h, 48, "two people signed it");
}

#[test]
fn under_unit_key_two_keys_of_one_person_count_twice() {
    let mut c = charter(&[strong(A, "ann"), strong(B, "ann")], Policy::Secure, Unit::Key);
    c.thresholds.sensitive = 2;
    let anchor = c.anchor();
    c.admins = [strong(A, "ann"), strong(B, "ann")].iter().map(|k| entry(*k, 1, &anchor)).collect();
    c.admins.sort_by(|a, b| a.key.cmp(&b.key));
    let (_d, mut g) = pool_of(&c);
    let act = add(&g, strong(C, "cy"), &[A]);
    take(&mut g, act.clone());
    assert_eq!(g.status(&act.id()), ActStatus::Short(1, 2));
    put_add(&mut g, strong(C, "cy"), &[A, B]);
    advance(&mut g, 25 * HOUR);
    assert!(admins(&g).contains(&public(C)), "two keys of one person are two keys");
    let mut people = charter(&[strong(A, "ann")], Policy::Secure, Unit::Person);
    people.thresholds.sensitive = 2;
    assert!(poolhouse_node::gov::pool::founding(&found_act(&people)).is_err(), "two people are needed and one is listed: the pool would lock itself");
}

#[test]
fn a_weak_key_counts_toward_no_sensitive_act_no_removal_and_no_cancel() {
    let (_d, mut g) = pool(&[strong(A, "ann"), weak(B, "ann")], Policy::Secure);
    let laptop = ident(10);
    put_admit(&mut g, &laptop, &[A]);
    let weak_remove = make(&g, "remove", json!({"fingerprint": laptop.fingerprint()}), &[B]);
    assert!(refused(take(&mut g, weak_remove)).contains("weak key"));
    assert!(is_member(&g, &laptop));
    let weak_add = add(&g, strong(C, "cy"), &[B]);
    assert!(refused(take(&mut g, weak_add)).contains("weak key"));
    let pending = add(&g, strong(C, "cy"), &[A]);
    take(&mut g, pending.clone());
    let weak_cancel = make(&g, "cancel", json!({"act": pending.id()}), &[B]);
    assert!(refused(take(&mut g, weak_cancel)).contains("weak key"));
    advance(&mut g, 25 * HOUR);
    assert!(admins(&g).contains(&public(C)), "the weak key cancelled nothing");
}

#[test]
fn a_pool_with_only_weak_keys_is_flagged_and_changes_nothing_sensitive() {
    let (_d, mut g) = pool(&[weak(A, "ann")], Policy::Secure);
    assert!(g.state().chain.latest().charter.weak_only());
    let laptop = ident(10);
    assert!(refused(put_admit(&mut g, &laptop, &[A])).contains("weak key"));
    let act = add(&g, strong(B, "ben"), &[A]);
    assert!(refused(take(&mut g, act)).contains("weak key"));
}

#[test]
fn a_weak_key_may_end_a_delegate_and_nothing_else() {
    let (_d, mut g) = pool(&[strong(A, "ann"), weak(B, "ann")], Policy::Secure);
    let laptop = ident(10);
    put_admit(&mut g, &laptop, &[A]);
    put(&mut g, "delegate", json!({"fingerprint": laptop.fingerprint()}), &[A]);
    assert!(g.state().roster.delegates.contains_key(&laptop.fingerprint()));
    assert_eq!(put(&mut g, "delegate_end", json!({"fingerprint": laptop.fingerprint()}), &[B]), Ingest::Accepted);
    assert!(!g.state().roster.delegates.contains_key(&laptop.fingerprint()), "a reduction of power needs no strong key");
}

// -- the recovery key --------------------------------------------------------------------------------------------------

#[test]
fn the_recovery_key_alone_signs_no_admin_add_remove_admin_remove_or_admit() {
    let (_d, mut g) = pool(&[strong(A, "ann"), recovery(D, "ann")], Policy::Secure);
    let laptop = ident(10);
    put_admit(&mut g, &laptop, &[A]);
    for act in [
        add(&g, strong(C, "cy"), &[D]),
        make(&g, "remove", json!({"fingerprint": laptop.fingerprint()}), &[D]),
        make(&g, "admin_remove", json!({"key": public(A), "cut": []}), &[D]),
        make(&g, "admit", admit_body(&ident(11), "x", "join-code", ""), &[D]),
    ] {
        assert!(refused(take(&mut g, act)).contains("recovery key"));
    }
    assert!(is_member(&g, &laptop) && admins(&g).len() == 2);
}

#[test]
fn removing_the_recovery_key_needs_its_own_signature() {
    let (_d, mut g) = pool(&[strong(A, "ann"), strong(B, "ben"), recovery(D, "ann")], Policy::Secure);
    let without = make(&g, "admin_remove", json!({"key": public(D), "cut": []}), &[A, B]);
    take(&mut g, without.clone());
    advance(&mut g, 25 * HOUR);
    assert!(admins(&g).contains(&public(D)), "the admins alone cannot remove the recovery key");
    let mut with = without;
    sign(&mut with, D);
    take(&mut g, with);
    advance(&mut g, 25 * HOUR);
    assert!(!admins(&g).contains(&public(D)), "with its own signature it goes, after the hold");
}

// -- time ----------------------------------------------------------------------------------------------------------------

#[test]
fn an_admin_add_takes_effect_only_after_its_hold_counted_on_this_devices_clock() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let act = add(&g, strong(B, "ben"), &[A]);
    take(&mut g, act.clone());
    assert!(!admins(&g).contains(&public(B)));
    assert_eq!(g.status(&act.id()), ActStatus::Pending);
    advance(&mut g, 23 * HOUR);
    assert!(!admins(&g).contains(&public(B)), "23 hours is not 24");
    advance(&mut g, 2 * HOUR);
    assert!(admins(&g).contains(&public(B)));
    assert_eq!(g.state().chain.latest().n, 2);
}

#[test]
fn a_partly_signed_act_is_held_from_the_moment_it_is_complete_and_not_from_the_moment_it_was_first_seen() {
    let (_d, mut g) = pool(&[strong(A, "ann"), strong(B, "ben")], Policy::Secure);
    let act = add(&g, strong(C, "cy"), &[A]);
    take(&mut g, act.clone());
    advance(&mut g, 30 * HOUR);
    assert_eq!(g.status(&act.id()), ActStatus::Short(1, 2));
    let mut whole = act.clone();
    sign(&mut whole, B);
    assert_eq!(take(&mut g, whole), Ingest::Merged);
    advance(&mut g, 23 * HOUR);
    assert!(!admins(&g).contains(&public(C)), "a thief with all the signatures but one gets no head start");
    advance(&mut g, 2 * HOUR);
    assert!(admins(&g).contains(&public(C)));
}

#[test]
fn a_backdated_act_and_a_wall_clock_a_month_ahead_shorten_no_hold() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let mut act = g.new_act("admin_add", json!({"entry": entry(strong(B, "ben"), 2, &g.id)}), wall_ms() - 3 * 24 * 3_600_000).unwrap();
    sign(&mut act, A);
    let month_ahead = wall_ms() + 30 * 24 * 3_600_000;
    assert_eq!(g.ingest(act, month_ahead), Ingest::Accepted);
    advance(&mut g, HOUR);
    assert!(!admins(&g).contains(&public(B)), "the signer wrote three days ago and the wall clock says a month later: the hold is awake time here");
    advance(&mut g, 24 * HOUR);
    assert!(admins(&g).contains(&public(B)));
}

#[test]
fn a_ticket_is_no_act_and_no_kind_this_build_does_not_know_is_taken() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let mut ticket = poolhouse_node::gov::act::Act::new(&g.id, 1, "ticket", json!({"code": "123456"}), wall_ms(), &g.context().1).unwrap();
    sign(&mut ticket, A);
    assert!(refused(take(&mut g, ticket)).contains("does not know that kind"));
    let mut handoff = poolhouse_node::gov::act::Act::new(&g.id, 1, "handoff", json!({}), wall_ms(), &g.context().1).unwrap();
    sign(&mut handoff, A);
    assert!(refused(take(&mut g, handoff)).contains("does not know that kind"), "a later build's acts are refused here, not half applied");
}

#[test]
fn an_act_that_follows_a_charter_the_pool_has_left_behind_is_a_no_op() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let old_prev = g.context();
    put(&mut g, "set_holds", json!({"dispute_d": 9}), &[A]);
    assert_eq!(g.state().chain.latest().n, 2, "a longer dispute is a strengthening: it is in force at once");
    advance(&mut g, HOUR);
    let mut stale = poolhouse_node::gov::act::Act::new(&g.id, old_prev.0, "set_holds", json!({"dispute_d": 30}), wall_ms(), &old_prev.1).unwrap();
    sign(&mut stale, A);
    take(&mut g, stale.clone());
    advance(&mut g, 25 * HOUR);
    assert_eq!(g.state().chain.latest().charter.holds.dispute_d, 9);
    assert!(matches!(g.status(&stale.id()), ActStatus::Stale | ActStatus::Void(_)), "{:?}", g.status(&stale.id()));
}

#[test]
fn a_pool_holds_four_thousand_admits_and_no_more() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let one = ident(20);
    let acts: Vec<_> = (0..4100).map(|_| admit(&g, &one, &[A])).collect();
    g.ingest_many(acts, wall_ms(), false);
    assert_eq!(g.acts().values().filter(|a| a.kind == "admit").count(), poolhouse_node::gov::MAX_ADMITS);
    assert!(is_member(&g, &one));
}

#[test]
fn a_device_name_with_control_characters_is_cleaned_and_cut_to_forty_characters() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let guest = ident(20);
    let name = format!("evil\u{7}\n{}", "x".repeat(70));
    let act = make(&g, "admit", admit_body(&guest, &name, "join-code", ""), &[A]);
    take(&mut g, act);
    let shown = &g.devices()[&guest.fingerprint()].name;
    assert!(shown.chars().count() <= 40 && !shown.chars().any(char::is_control), "{shown:?}");
}

#[test]
fn an_act_dated_in_the_future_is_refused() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let mut act = g.new_act("remove", json!({"fingerprint": "ab".repeat(32)}), wall_ms() + 3_600_000).unwrap();
    sign(&mut act, A);
    assert!(refused(take(&mut g, act)).contains("future"));
}

#[test]
fn a_cancel_during_the_hold_ends_the_act_and_a_cancel_after_it_ends_nothing() {
    let (_d, mut g) = pool(&[strong(A, "ann"), strong(B, "ben")], Policy::Secure);
    let one = add(&g, strong(C, "cy"), &[A, B]);
    take(&mut g, one.clone());
    advance(&mut g, 2 * HOUR);
    let cancel = make(&g, "cancel", json!({"act": one.id()}), &[B]);
    take(&mut g, cancel);
    advance(&mut g, 30 * HOUR);
    assert!(!admins(&g).contains(&public(C)));
    assert!(matches!(g.status(&one.id()), ActStatus::Void(w) if w.contains("cancelled")));
    let (_d2, mut h) = pool(&[strong(A, "ann"), strong(B, "ben")], Policy::Secure);
    let two = add(&h, strong(C, "cy"), &[A, B]);
    take(&mut h, two.clone());
    advance(&mut h, 25 * HOUR);
    assert!(admins(&h).contains(&public(C)));
    put(&mut h, "cancel", json!({"act": two.id()}), &[B]);
    advance(&mut h, 25 * HOUR);
    assert!(admins(&h).contains(&public(C)), "it was in effect before the cancel arrived");
}

#[test]
fn an_admin_remove_waits_its_hold_and_the_removed_keys_veto_cancels_it_and_pauses_the_admin_acts() {
    let (_d, mut g) = pool(&[strong(A, "ann"), strong(B, "ben"), strong(C, "cy")], Policy::Secure);
    let removal = make(&g, "admin_remove", json!({"key": public(C), "cut": []}), &[A, B]);
    take(&mut g, removal.clone());
    advance(&mut g, 10 * HOUR);
    assert!(admins(&g).contains(&public(C)), "still held");
    put(&mut g, "veto", json!({"act": removal.id()}), &[C]);
    advance(&mut g, 20 * HOUR);
    assert!(admins(&g).contains(&public(C)), "the veto ended the removal");
    assert!(g.state().chain.dispute.is_some(), "and opened a dispute that the pool shows");
    let again = make(&g, "set_holds", json!({"dispute_d": 2}), &[A, B]);
    take(&mut g, again);
    advance(&mut g, 3 * 24 * HOUR);
    assert_eq!(g.state().chain.latest().charter.holds.dispute_d, 7, "admin-set acts wait while the dispute lasts");
    advance(&mut g, 5 * 24 * HOUR);
    assert!(g.state().chain.dispute.is_none(), "after seven days it is over");
}

// -- fork rules ------------------------------------------------------------------------------------------------------

#[test]
fn removals_commute_and_apply_together_when_enough_admins_are_left() {
    let (_d, mut g) = pool(&[strong(A, "ann"), strong(B, "ben"), strong(C, "cy"), strong(D, "di")], Policy::Secure);
    put(&mut g, "admin_remove", json!({"key": public(C), "cut": []}), &[A, B]);
    put(&mut g, "admin_remove", json!({"key": public(D), "cut": []}), &[A, B]);
    advance(&mut g, 25 * HOUR);
    assert_eq!(admins(&g).len(), 2, "{:?}", g.state().chain);
    assert_eq!(g.state().chain.latest().n, 2, "one step for both");
}

#[test]
fn the_last_admin_rule_is_checked_after_the_fold_and_the_conflicting_removals_open_a_dispute() {
    let (_d, mut g) = pool(&[strong(A, "ann"), strong(B, "ben"), strong(C, "cy")], Policy::Secure);
    put(&mut g, "admin_remove", json!({"key": public(B), "cut": []}), &[A, C]);
    put(&mut g, "admin_remove", json!({"key": public(C), "cut": []}), &[A, B]);
    advance(&mut g, 25 * HOUR);
    assert_eq!(admins(&g).len(), 3, "together they would leave one admin where two are needed: neither applies");
    assert!(g.state().chain.dispute.is_some());
}

#[test]
fn the_last_admin_cannot_be_removed() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let act = make(&g, "admin_remove", json!({"key": public(A), "cut": []}), &[A]);
    take(&mut g, act.clone());
    advance(&mut g, 25 * HOUR);
    assert_eq!(admins(&g), vec![public(A)]);
    assert!(matches!(g.status(&act.id()), ActStatus::Void(w) if w.contains("too few")));
}

#[test]
fn an_add_whose_signer_is_removed_in_the_same_step_is_void() {
    let (_d, mut g) = pool(&[strong(A, "ann"), strong(B, "ben"), strong(C, "cy")], Policy::Secure);
    let adds = add(&g, strong(D, "di"), &[B, C]);
    take(&mut g, adds.clone());
    put(&mut g, "admin_remove", json!({"key": public(C), "cut": [adds.id()]}), &[A, B]);
    advance(&mut g, 25 * HOUR);
    assert!(!admins(&g).contains(&public(D)));
    assert!(admins(&g).contains(&public(A)) && !admins(&g).contains(&public(C)));
    assert!(matches!(g.status(&adds.id()), ActStatus::Void(w) if w.contains("signers were removed")), "{:?}", g.status(&adds.id()));
}

#[test]
fn an_add_signed_by_a_key_that_is_removed_without_the_add_in_its_cut_is_struck() {
    let (_d, mut g) = pool(&[strong(A, "ann"), strong(B, "ben"), strong(C, "cy")], Policy::Secure);
    let adds = add(&g, strong(D, "di"), &[B, C]);
    take(&mut g, adds.clone());
    put(&mut g, "admin_remove", json!({"key": public(C), "cut": []}), &[A, B]);
    advance(&mut g, 25 * HOUR);
    assert!(!admins(&g).contains(&public(D)), "the removed key's signature on an act the removal did not keep counts for nothing");
    assert_ne!(g.status(&adds.id()), ActStatus::Effective);
}

#[test]
fn two_acts_that_cannot_both_hold_are_settled_by_signers_and_never_by_a_nonce() {
    let (_d, mut g) = pool(&[strong(A, "ann"), strong(B, "ben"), strong(C, "cy")], Policy::Secure);
    let two = make(&g, "set_holds", json!({"dispute_d": 3}), &[A, B]);
    let three = make(&g, "set_holds", json!({"dispute_d": 4}), &[A, B, C]);
    take(&mut g, two);
    take(&mut g, three);
    advance(&mut g, 25 * HOUR);
    assert_eq!(g.state().chain.latest().charter.holds.dispute_d, 4, "more distinct strong signers wins");
    assert!(g.state().chain.dispute.is_none());
}

#[test]
fn equal_signers_apply_neither_and_open_a_dispute_whatever_the_nonce() {
    let (_d, mut g) = pool(&[strong(A, "ann"), strong(B, "ben")], Policy::Secure);
    for n in 0..3 {
        let a = make(&g, "set_holds", json!({"dispute_d": 3 + n}), &[A, B]);
        take(&mut g, a);
    }
    advance(&mut g, 0);
    assert_eq!(g.state().chain.latest().charter.holds.dispute_d, 7);
    assert!(g.state().chain.dispute.is_some(), "the same signers signed three different acts: they sign one of them again");
}

#[test]
fn a_removed_keys_act_that_is_not_in_the_cut_is_void_and_one_in_the_cut_stays() {
    let (_d, mut g) = pool(&[strong(A, "ann"), strong(B, "ben"), strong(C, "cy")], Policy::Secure);
    let (kept, drawer) = (ident(10), ident(11));
    let kept_admit = admit(&g, &kept, &[C]);
    take(&mut g, kept_admit.clone());
    let drawer_admit = admit(&g, &drawer, &[C]);
    take(&mut g, drawer_admit.clone());
    assert!(is_member(&g, &kept) && is_member(&g, &drawer));
    put(&mut g, "admin_remove", json!({"key": public(C), "cut": [kept_admit.id()]}), &[A, B]);
    advance(&mut g, 25 * HOUR);
    assert!(!admins(&g).contains(&public(C)));
    assert!(is_member(&g, &kept), "the act the removing admins saw and listed stays");
    assert!(!is_member(&g, &drawer), "an act of the removed key that was not in the cut is void on every device");
}

#[test]
fn a_flood_of_admin_set_acts_is_capped_and_a_page_fits_the_frame() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let sets: Vec<_> = (0..300u64).map(|n| make(&g, "set_holds", json!({"dispute_d": 2 + (n % 20)}), &[A])).collect();
    g.ingest_many(sets, wall_ms(), false);
    let held = g.acts().values().filter(|a| a.is_chain()).count();
    assert!(held <= poolhouse_node::gov::MAX_ADMIN_ACTS, "{held}");
    let removes: Vec<_> = (0..700u64).map(|n| make(&g, "remove", json!({"fingerprint": format!("{n:064x}")}), &[A])).collect();
    g.ingest_many(removes, wall_ms(), false);
    let have = std::collections::BTreeSet::new();
    let mut total = 0;
    for page in 0..8 {
        let (acts, more) = g.page(&have, page);
        assert!(acts.len() <= 256);
        assert!(serde_json::to_vec(&acts).unwrap().len() < 2 * 1024 * 1024);
        total += acts.len();
        if !more {
            break;
        }
    }
    assert!(total >= 700, "every act is on some page: {total}");
}

#[test]
fn a_key_that_floods_admin_acts_replaces_its_own_oldest_and_never_the_acts_of_another_key() {
    let (_d, mut g) = pool(&[strong(A, "ann"), strong(B, "ben"), strong(C, "cy")], Policy::Secure);
    let legit = make(&g, "admin_remove", json!({"key": public(C), "cut": []}), &[B]);
    take(&mut g, legit.clone());
    let flood: Vec<_> = (0..40u64).map(|n| make(&g, "set_holds", json!({"dispute_d": 2 + n % 20}), &[A])).collect();
    g.ingest_many(flood, wall_ms(), false);
    let ann = g.acts().values().filter(|a| a.is_chain() && a.admin_keys().contains(&public(A).as_str())).count();
    assert!(ann <= poolhouse_node::gov::ADMIN_ACTS_PER_KEYS, "{ann}");
    assert!(g.acts().contains_key(&legit.id()), "the removal ben signed is still held, waiting for a second person");
}

#[test]
fn the_scope_of_an_admin_fingerprint_matches_a_pool_only_where_the_key_is_listed_and_accepted() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let fp = |seed: u8| poolhouse_node::gov::keys::fingerprint_of(&public(seed));
    assert!(g.admin_scope(&fp(A)) && !g.admin_scope(&fp(B)));
    put_add(&mut g, strong(B, "ben"), &[A]);
    assert!(!g.admin_scope(&fp(B)), "a key asked to be listed is not listed until the add takes effect");
    advance(&mut g, 25 * HOUR);
    assert!(g.admin_scope(&fp(B)));
    assert!(!g.admin_scope(&"ab".repeat(32)));
}

#[test]
fn an_act_over_sixteen_kib_or_with_a_field_nobody_defined_is_refused() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let mut big = make(&g, "remove", json!({"fingerprint": "ab".repeat(32)}), &[A]);
    big.body = json!({"fingerprint": "ab".repeat(32), "note": "x".repeat(20_000)});
    assert!(!matches!(take(&mut g, big), Ingest::Accepted));
    let mut extra = make(&g, "remove", json!({"fingerprint": "ab".repeat(32), "also": 1}), &[A]);
    sign(&mut extra, A);
    assert!(!matches!(take(&mut g, extra), Ingest::Accepted));
}

#[test]
fn the_acceptance_of_a_key_is_for_the_pool_and_epoch_it_names_and_cannot_list_a_key_for_another_person() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let mut borrowed = entry(strong(B, "ben"), 2, &g.id);
    borrowed.acceptance = RigSigner::sign_as(C, b"someone else's acceptance");
    let act = make(&g, "admin_add", json!({"entry": borrowed}), &[A]);
    take(&mut g, act.clone());
    advance(&mut g, 25 * HOUR);
    assert!(!admins(&g).contains(&public(B)), "a pool cannot list a public key its holder did not accept");
    assert!(matches!(g.status(&act.id()), ActStatus::Void(w) if w.contains("did not accept")));
    let stale = entry(strong(B, "ben"), 5, &g.id);
    let act = make(&g, "admin_add", json!({"entry": stale}), &[A]);
    take(&mut g, act.clone());
    advance(&mut g, 25 * HOUR);
    assert!(!admins(&g).contains(&public(B)), "an acceptance for another epoch is no acceptance for this one");
}

#[test]
fn an_unknown_role_that_names_the_recovery_key_twice_is_refused() {
    let (_d, mut g) = pool(&[strong(A, "ann"), recovery(D, "ann")], Policy::Secure);
    let act = add(&g, K(C, "ann", Role::Recovery, poolhouse_node::gov::keys::Strength::Strong), &[A]);
    take(&mut g, act.clone());
    advance(&mut g, 25 * HOUR);
    assert!(!admins(&g).contains(&public(C)), "the recovery key is replaced only by its own signature");
}
