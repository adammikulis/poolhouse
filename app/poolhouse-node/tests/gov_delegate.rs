//! The delegate and the open pool's member: the device-signed admits, their rates, and what ends them.

mod kit;

use kit::gov::*;
use poolhouse_node::cert::Identity;
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
