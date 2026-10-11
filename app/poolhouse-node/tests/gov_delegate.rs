//! The delegate and the open pool's member: the device-signed admits, their rates, and what ends them.

mod kit;

use kit::gov::*;
use poolhouse_node::cert::Identity;
use poolhouse_node::gov::act::Act;
use poolhouse_node::gov::keys::Strength;
use poolhouse_node::gov::pool::{GovPool, Ingest};
use poolhouse_node::membership::Policy;
use poolhouse_node::poolops::wall_ms;
use serde_json::json;

const A: u8 = 1;
const B: u8 = 2;
const MINUTE: i64 = 60_000;

/// A device-signed admit of ``guest`` signed by the device made from ``signer``, with ``via``.
fn device_admit(g: &mut GovPool, signer: u8, guest: &Identity, via: &str) -> Ingest {
    let mut act = g.new_act("admit", admit_body(guest, "guest", "join-code", via), wall_ms()).unwrap();
    device_sign(&mut act, signer);
    take(g, act)
}

fn delegate_pool() -> (tempfile::TempDir, GovPool, Identity) {
    let (d, mut g) = pool(&[strong(A, "ann"), kit::gov::weak(B, "ann")], Policy::Secure);
    let delegate = ident(20);
    put_admit(&mut g, &delegate, &[A]);
    put(&mut g, "delegate", json!({"fingerprint": delegate.fingerprint()}), &[A]);
    (d, g, delegate)
}

fn count(g: &GovPool, guests: &[Identity]) -> usize {
    guests.iter().filter(|d| is_member(g, d)).count()
}

#[test]
fn a_delegate_admits_three_an_hour_and_ten_a_day_by_the_order_this_device_first_saw_them() {
    let (_d, mut g, delegate) = delegate_pool();
    assert!(g.state().roster.delegates.contains_key(&delegate.fingerprint()));
    let guests: Vec<Identity> = (30..46).map(ident).collect();
    for guest in &guests[..4] {
        device_admit(&mut g, 20, guest, "");
    }
    assert_eq!(count(&g, &guests[..4]), 3, "the fourth in the hour is refused");
    for round in 1..4 {
        advance(&mut g, 61 * MINUTE);
        for guest in &guests[3 * round + 1..3 * round + 4] {
            device_admit(&mut g, 20, guest, "");
        }
    }
    assert_eq!(count(&g, &guests[..13]), 10, "ten in a day, and the eleventh is refused");
    advance(&mut g, 25 * HOUR);
    device_admit(&mut g, 20, &guests[14], "");
    assert!(is_member(&g, &guests[14]), "a day on, the delegate admits again");
}

#[test]
fn a_device_that_is_no_delegate_signs_no_admit_and_a_delegate_signs_no_other_act() {
    let (_d, mut g, _) = delegate_pool();
    let plain = ident(21);
    put_admit(&mut g, &plain, &[A]);
    let guest = ident(31);
    device_admit(&mut g, 21, &guest, "");
    assert!(!is_member(&g, &guest), "a member that is not a delegate admits nobody in a secure pool");
    let mut removal = g.new_act("remove", json!({"fingerprint": plain.fingerprint()}), wall_ms()).unwrap();
    device_sign(&mut removal, 20);
    assert!(matches!(take(&mut g, removal), Ingest::Refused(_)), "a delegate signs `admit` and nothing else");
    assert!(is_member(&g, &plain));
}

#[test]
fn a_delegate_lasts_thirty_days_of_awake_time_and_what_it_admitted_stays() {
    let (_d, mut g, _) = delegate_pool();
    let (early, late) = (ident(32), ident(33));
    device_admit(&mut g, 20, &early, "");
    advance(&mut g, 29 * 24 * HOUR);
    assert!(g.state().roster.delegates.len() == 1, "29 days in, it is still a delegate");
    advance(&mut g, 2 * 24 * HOUR);
    assert!(g.state().roster.delegates.is_empty(), "31 days in, it is not");
    device_admit(&mut g, 20, &late, "");
    assert!(is_member(&g, &early), "it admitted this one while it was a delegate");
    assert!(!is_member(&g, &late));
}

#[test]
fn a_weak_key_ends_a_delegate_at_once_and_what_it_admitted_before_stays() {
    let (_d, mut g, delegate) = delegate_pool();
    let (before, after) = (ident(34), ident(35));
    device_admit(&mut g, 20, &before, "");
    advance(&mut g, 10);
    put(&mut g, "delegate_end", json!({"fingerprint": delegate.fingerprint()}), &[B]);
    advance(&mut g, 10);
    device_admit(&mut g, 20, &after, "");
    assert!(is_member(&g, &before) && !is_member(&g, &after));
    assert!(g.state().roster.delegates.is_empty());
}

#[test]
fn an_open_pools_member_admits_five_an_hour_and_only_with_via_segment() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Open);
    let member = ident(20);
    put_admit(&mut g, &member, &[A]);
    let guests: Vec<Identity> = (30..38).map(ident).collect();
    device_admit(&mut g, 20, &guests[0], "");
    assert!(!is_member(&g, &guests[0]), "an open admit without `via: segment` is refused");
    for guest in &guests[1..7] {
        device_admit(&mut g, 20, guest, "segment");
    }
    assert_eq!(count(&g, &guests[1..7]), 5, "the sixth in an hour is refused by every member");
    advance(&mut g, 61 * MINUTE);
    device_admit(&mut g, 20, &guests[7], "segment");
    assert!(is_member(&g, &guests[7]));
}

#[test]
fn a_secure_pool_takes_no_open_admit() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let member = ident(20);
    put_admit(&mut g, &member, &[A]);
    let guest = ident(30);
    device_admit(&mut g, 20, &guest, "segment");
    assert!(!is_member(&g, &guest));
}

#[test]
fn a_device_that_left_is_not_a_member_and_an_admit_brings_it_back_and_a_removed_one_never_returns() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let (stays, goes, gone) = (ident(20), ident(21), ident(22));
    for d in [&stays, &goes, &gone] {
        put_admit(&mut g, d, &[A]);
    }
    let mut leave = g.new_act("leave", json!({"fingerprint": goes.fingerprint()}), wall_ms()).unwrap();
    device_sign(&mut leave, 21);
    take(&mut g, leave);
    assert!(!is_member(&g, &goes) && is_member(&g, &stays));
    assert_eq!(g.devices()[&goes.fingerprint()].status, poolhouse_node::membership::Status::Left);
    std::thread::sleep(std::time::Duration::from_millis(5));
    put_admit(&mut g, &goes, &[A]);
    assert!(is_member(&g, &goes), "a later admit undoes a leave");
    put(&mut g, "remove", json!({"fingerprint": gone.fingerprint()}), &[A]);
    assert!(is_removed(&g, &gone));
    std::thread::sleep(std::time::Duration::from_millis(5));
    put_admit(&mut g, &gone, &[A]);
    assert!(is_removed(&g, &gone), "a removed certificate never comes back");
    let _ = Strength::Strong;
}

#[test]
fn a_removed_member_signs_no_open_admit() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Open);
    let member = ident(20);
    put_admit(&mut g, &member, &[A]);
    advance(&mut g, 10);
    put(&mut g, "remove", json!({"fingerprint": member.fingerprint()}), &[A]);
    let guest = ident(30);
    device_admit(&mut g, 20, &guest, "segment");
    assert!(is_removed(&g, &member) && !is_member(&g, &guest), "a device put out of the pool admits nobody");
}

#[test]
fn a_device_that_left_signs_no_admit_after_it_left() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Open);
    let member = ident(20);
    put_admit(&mut g, &member, &[A]);
    let (before, after) = (ident(30), ident(31));
    device_admit(&mut g, 20, &before, "segment");
    std::thread::sleep(std::time::Duration::from_millis(5));
    let mut leave = g.new_act("leave", json!({"fingerprint": member.fingerprint()}), wall_ms()).unwrap();
    device_sign(&mut leave, 20);
    take(&mut g, leave);
    std::thread::sleep(std::time::Duration::from_millis(5));
    device_admit(&mut g, 20, &after, "segment");
    assert!(is_member(&g, &before), "it admitted this one while it was a member");
    assert!(!is_member(&g, &after), "a device that has left admits nobody");
}

#[test]
fn a_delegate_made_by_a_key_that_is_removed_without_its_act_in_the_cut_admits_nobody() {
    let (_d, mut g) = pool(&[strong(A, "ann"), strong(B, "ben"), strong(3, "cy")], Policy::Secure);
    let delegate = ident(20);
    put_admit(&mut g, &delegate, &[A]);
    put(&mut g, "delegate", json!({"fingerprint": delegate.fingerprint()}), &[B, 3]);
    let guest = ident(30);
    device_admit(&mut g, 20, &guest, "");
    assert!(is_member(&g, &guest), "while the delegate act stands");
    put(&mut g, "admin_remove", json!({"key": public(3), "cut": []}), &[A, B]);
    advance(&mut g, 25 * HOUR);
    assert!(!admins(&g).contains(&public(3)));
    assert!(!is_member(&g, &guest), "the delegate act lost the signature of the key that was removed, and one person is not enough to make a delegate");
    assert!(g.state().roster.delegates.is_empty());
}

/// The seed of a device whose fingerprint sorts below ``fingerprint``.
fn seed_below(fingerprint: &str) -> u8 {
    (100u8..200).find(|s| ident(*s).fingerprint().as_str() < fingerprint).expect("a lower fingerprint")
}

fn device_leave(g: &mut GovPool, who: &Identity, signers: &[u8]) -> Ingest {
    let mut leave = g.new_act("leave", json!({"fingerprint": who.fingerprint()}), wall_ms()).unwrap();
    signers.iter().for_each(|s| device_sign(&mut leave, *s));
    take(g, leave)
}

#[test]
fn a_member_that_relays_a_delegates_admit_cannot_undo_it_by_adding_its_own_signature() {
    let (_d, mut g, delegate) = delegate_pool();
    let plain_seed = seed_below(&delegate.fingerprint());
    put_admit(&mut g, &ident(plain_seed), &[A]);
    let guest = ident(31);
    let mut act = g.new_act("admit", admit_body(&guest, "guest", "join-code", ""), wall_ms()).unwrap();
    device_sign(&mut act, 20);
    assert_eq!(take(&mut g, act.clone()), Ingest::Accepted);
    let mut relayed = act.clone();
    device_sign(&mut relayed, plain_seed);
    assert!(matches!(take(&mut g, relayed), Ingest::Refused(_)), "a device that may not admit adds no signature to an admit");
    assert!(is_member(&g, &guest), "the delegate's admit stands");
    assert_eq!(g.acts()[&act.id()].signatures.len(), 1);
}

#[test]
fn an_admit_carrying_the_signature_of_a_device_that_may_not_admit_is_refused() {
    let (_d, mut g, delegate) = delegate_pool();
    let plain_seed = seed_below(&delegate.fingerprint());
    put_admit(&mut g, &ident(plain_seed), &[A]);
    let guest = ident(31);
    let mut act = g.new_act("admit", admit_body(&guest, "guest", "join-code", ""), wall_ms()).unwrap();
    device_sign(&mut act, 20);
    device_sign(&mut act, plain_seed);
    assert!(matches!(take(&mut g, act), Ingest::Refused(_)));
    assert!(!is_member(&g, &guest));
}

#[test]
fn a_member_cannot_add_its_signature_to_the_leave_of_another_device() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let (goes, other) = (ident(21), ident(22));
    put_admit(&mut g, &goes, &[A]);
    put_admit(&mut g, &other, &[A]);
    let mut leave = g.new_act("leave", json!({"fingerprint": goes.fingerprint()}), wall_ms()).unwrap();
    device_sign(&mut leave, 21);
    assert_eq!(take(&mut g, leave.clone()), Ingest::Accepted);
    let mut relayed = leave.clone();
    device_sign(&mut relayed, 22);
    assert!(matches!(take(&mut g, relayed), Ingest::Refused(_)));
    assert_eq!(g.devices()[&goes.fingerprint()].status, poolhouse_node::membership::Status::Left, "the leave stands");
}

#[test]
fn a_delegate_end_ends_the_delegate_whatever_time_its_signer_put_on_either_act() {
    let (_d, mut g) = pool(&[strong(A, "ann"), kit::gov::weak(B, "ann")], Policy::Secure);
    let delegate = ident(20);
    put_admit(&mut g, &delegate, &[A]);
    let (epoch, prev) = g.context();
    let mut ahead = Act::new(&g.id, epoch, "delegate", json!({"fingerprint": delegate.fingerprint()}), wall_ms() + 4 * MINUTE as u64, &prev).unwrap();
    sign(&mut ahead, A);
    take(&mut g, ahead);
    assert!(g.state().roster.delegates.contains_key(&delegate.fingerprint()));
    advance(&mut g, 10);
    put(&mut g, "delegate_end", json!({"fingerprint": delegate.fingerprint()}), &[B]);
    assert!(g.state().roster.delegates.is_empty(), "the end came after the delegate act on this device");
}

#[test]
fn a_device_that_left_admits_nobody_whatever_time_it_puts_on_the_admit() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Open);
    let member = ident(20);
    put_admit(&mut g, &member, &[A]);
    assert_eq!(device_leave(&mut g, &member, &[20]), Ingest::Accepted);
    advance(&mut g, 10);
    let guest = ident(30);
    let mut act = g.new_act("admit", admit_body(&guest, "guest", "open", "segment"), wall_ms() - MINUTE as u64).unwrap();
    device_sign(&mut act, 20);
    take(&mut g, act);
    assert!(!is_member(&g, &guest), "it was seen after the leave, so it was signed by a device that was not a member");
}

#[test]
fn a_leave_is_signed_by_the_device_it_names_alone() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let (goes, other) = (ident(21), ident(22));
    put_admit(&mut g, &goes, &[A]);
    put_admit(&mut g, &other, &[A]);
    assert!(matches!(device_leave(&mut g, &goes, &[22]), Ingest::Refused(_)), "another device signs no leave of this one");
    assert!(matches!(device_leave(&mut g, &goes, &[21, 22]), Ingest::Refused(_)), "nor does one add its signature");
    assert!(is_member(&g, &goes));
    assert_eq!(device_leave(&mut g, &goes, &[21]), Ingest::Accepted);
}
