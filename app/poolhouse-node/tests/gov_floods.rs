//! What a member can fill a governed pool with, and what it costs this device to take it.

mod kit;

use kit::gov::*;
use poolhouse_node::gov::act::{Act, ActSig, SigKind};
use poolhouse_node::gov::alg::Alg;
use poolhouse_node::gov::pool::{GovPool, Ingest};
use poolhouse_node::gov::room::{MOST_JUNK, MOST_JUNK_PER_DEVICE, MOST_LEAVES_PER_DEVICE};
use poolhouse_node::gov::{MAX_ACTS, MAX_ADMITS};
use poolhouse_node::membership::Policy;
use poolhouse_node::poolops::wall_ms;
use serde_json::json;

const A: u8 = 1;

fn fast_sign(act: &mut Act, seed: u8, fingerprint: &str) {
    use ed25519_dalek::Signer;
    let sig = poolhouse_node::fsutil::hex(&kit::key(seed).sign(&act.sign_bytes()).to_bytes());
    act.add_signature(ActSig { kind: SigKind::Device, key: fingerprint.into(), alg: Alg::Ed25519, sig });
}

/// An open pool with one admin and one member device (made from seed 20), and that device's fingerprint.
fn open_pool() -> (tempfile::TempDir, GovPool, String) {
    let (d, mut g) = pool(&[strong(A, "ann")], Policy::Open);
    let member = ident(20);
    put_admit(&mut g, &member, &[A]);
    (d, g, member.fingerprint())
}

/// ``n`` open admits of one guest signed by the device made from ``seed``, each with a name of its own.
fn open_admits(g: &GovPool, seed: u8, fingerprint: &str, n: usize) -> Vec<Act> {
    let guest = ident(30);
    (0..n).map(|i| {
        let mut act = g.new_act("admit", admit_body(&guest, &format!("guest {i}"), "open", "segment"), wall_ms()).unwrap();
        fast_sign(&mut act, seed, fingerprint);
        act
    }).collect()
}

fn count(g: &GovPool, f: impl Fn(&str, &Act) -> bool) -> usize {
    g.acts().iter().filter(|(id, a)| f(id, a)).count()
}

fn junk(g: &GovPool) -> usize {
    count(g, |id, a| a.kind == "admit" && !a.device_keys().is_empty() && !g.state().roster.standing.contains(id))
}

fn cancels(g: &GovPool, from: u64, to: u64) -> Vec<Act> {
    (from..to).map(|n| make(g, "cancel", json!({"act": format!("{n:064x}")}), &[A])).collect()
}

#[test]
fn a_member_cannot_fill_a_pool_with_leaves_that_name_other_devices() {
    let (_d, mut g, fp) = open_pool();
    let before = g.acts().len();
    let leaves: Vec<Act> = (0..300u64).map(|n| {
        let mut act = g.new_act("leave", json!({"fingerprint": format!("{n:064x}")}), wall_ms()).unwrap();
        fast_sign(&mut act, 20, &fp);
        act
    }).collect();
    let out = g.ingest_many(leaves, wall_ms(), false);
    assert!(out.iter().all(|(_, i)| matches!(i, Ingest::Refused(_))), "a device leaves only as itself");
    assert_eq!(g.acts().len(), before);
}

#[test]
fn a_device_that_signs_many_leaves_of_its_own_keeps_eight_and_signs_no_leave_once_it_has_left() {
    let (_d, mut g, fp) = open_pool();
    let leaves: Vec<Act> = (0..50).map(|_| {
        let mut act = g.new_act("leave", json!({"fingerprint": fp}), wall_ms()).unwrap();
        fast_sign(&mut act, 20, &fp);
        act
    }).collect();
    g.ingest_many(leaves, wall_ms(), false);
    assert!(count(&g, |_, a| a.kind == "leave") <= MOST_LEAVES_PER_DEVICE);
    assert_eq!(g.devices()[&fp].status, poolhouse_node::membership::Status::Left);
    let mut again = g.new_act("leave", json!({"fingerprint": fp}), wall_ms()).unwrap();
    fast_sign(&mut again, 20, &fp);
    assert!(matches!(take(&mut g, again), Ingest::Refused(_)), "a device that is not a member leaves nothing");
}

#[test]
fn a_member_that_floods_open_admits_holds_thirty_two_that_stand_nowhere_and_costs_a_few_folds() {
    let (_d, mut g, fp) = open_pool();
    let folds = g.folds();
    let acts = open_admits(&g, 20, &fp, 256);
    g.ingest_many(acts, wall_ms(), false);
    assert!(g.folds() - folds <= 3, "a push of 256 acts signed by a device this pool knows is folded {} times", g.folds() - folds);
    assert!(count(&g, |_, a| a.kind == "admit" && a.device_keys().contains(&fp.as_str())) <= MOST_JUNK_PER_DEVICE, "its own oldest go first");
    assert_eq!(put_admit(&mut g, &ident(31), &[A]), Ingest::Accepted, "an admin's admit is still taken");
}

#[test]
fn many_devices_flooding_admits_leave_the_pool_at_most_a_thousand_that_stand_nowhere() {
    let (_d, mut g, _) = open_pool();
    let seeds: Vec<u8> = (100..142).collect();
    let members: Vec<(u8, String)> = seeds.iter().map(|s| {
        let device = ident(*s);
        put_admit(&mut g, &device, &[A]);
        (*s, device.fingerprint())
    }).collect();
    for (seed, fp) in &members {
        let acts = open_admits(&g, *seed, fp, MOST_JUNK_PER_DEVICE + 8);
        g.ingest_many(acts, wall_ms(), false);
    }
    assert!(junk(&g) <= MOST_JUNK, "{}", junk(&g));
    assert!(junk(&g) > MOST_JUNK_PER_DEVICE, "the flood was held up to the cap: {}", junk(&g));
}

#[test]
fn a_pool_that_holds_as_many_acts_as_it_will_gives_up_a_device_signed_admit_for_an_admins_act_and_then_no_more() {
    let (_d, mut g, fp) = open_pool();
    let fill = MAX_ACTS - g.acts().len() - 20;
    g.ingest_many(cancels(&g, 0, fill as u64), wall_ms(), false);
    g.ingest_many(open_admits(&g, 20, &fp, 20), wall_ms(), false);
    assert_eq!(g.acts().len(), MAX_ACTS);
    let spare = junk(&g);
    assert!(spare > 0 && spare < 20, "the ones past the rate stand nowhere: {spare}");
    let out = g.ingest_many(cancels(&g, 90_000, 90_000 + spare as u64 + 1), wall_ms(), false);
    let taken = out.iter().filter(|(_, i)| *i == Ingest::Accepted).count();
    assert_eq!(taken, spare, "each admin act took the place of one admit that stood nowhere");
    assert!(out.iter().any(|(_, i)| matches!(i, Ingest::Refused(why) if why.contains("as many acts as it will"))), "and then there was none to give up");
    assert_eq!(g.acts().len(), MAX_ACTS);
}

#[test]
fn admits_that_stand_nowhere_do_not_count_toward_the_four_thousand() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let removed = ident(40);
    put_admit(&mut g, &removed, &[A]);
    put(&mut g, "remove", json!({"fingerprint": removed.fingerprint()}), &[A]);
    for _ in 0..MAX_ADMITS / 256 + 2 {
        let batch: Vec<Act> = (0..256).map(|_| admit(&g, &removed, &[A])).collect();
        g.ingest_many(batch, wall_ms(), false);
    }
    assert!(count(&g, |_, a| a.kind == "admit") > MAX_ADMITS, "admits of a certificate that was removed are void and are not counted");
    assert_eq!(put_admit(&mut g, &ident(41), &[A]), Ingest::Accepted);
}

#[test]
fn a_pool_is_folded_again_when_a_hold_or_a_delegate_ends_and_not_for_the_time_that_passes() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let folds = g.folds();
    g.clock.advance_ms(10_000);
    assert!(!g.tick());
    assert_eq!(g.folds(), folds, "nothing is pending, so the time that passes needs no fold");
    let anchor = g.id.clone();
    put(&mut g, "admin_add", json!({"entry": entry(strong(2, "ben"), 2, &anchor)}), &[A]);
    let folds = g.folds();
    g.clock.advance_ms(10_000);
    assert!(!g.tick());
    assert_eq!(g.folds(), folds, "the hold has not run");
    g.clock.advance_ms(25 * HOUR);
    assert!(g.tick(), "the hold ran out");
    assert!(admins(&g).contains(&public(2)));
}

#[test]
fn a_pool_writes_a_file_only_when_what_it_holds_changed() {
    let (_d, mut g, _) = open_pool();
    g.save().unwrap();
    let base = g.dir().to_path_buf();
    let stamp = |name: &str| std::fs::metadata(base.join(name)).unwrap().modified().unwrap();
    let (acts, refused) = (stamp("acts.json"), stamp("refused.json"));
    std::thread::sleep(std::time::Duration::from_millis(30));
    g.save().unwrap();
    g.page(&Default::default(), 0);
    g.save().unwrap();
    assert_eq!((stamp("acts.json"), stamp("refused.json")), (acts, refused), "a request that changes nothing writes nothing");
    put_admit(&mut g, &ident(41), &[A]);
    g.save().unwrap();
    assert!(stamp("acts.json") > acts, "an act taken is written");
    assert_eq!(stamp("refused.json"), refused);
}
