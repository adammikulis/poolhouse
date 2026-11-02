//! What a `window` act lets the device it names do, at the governance level: the person's one yes for one window admits ONE device, for the length the
//! person was shown, counted from the moment the yes was signed. (The first two tests are the independent reviewer's repros.)

mod kit;

use kit::gov::*;
use poolhouse_node::cert::Identity;
use poolhouse_node::gov::pool::{GovPool, Ingest};
use poolhouse_node::membership::Policy;
use poolhouse_node::poolops::wall_ms;
use serde_json::json;

const A: u8 = 1;
const MINUTE: i64 = 60_000;

fn device_admit(g: &mut GovPool, signer: u8, guest: &Identity) -> Ingest {
    let mut act = g.new_act("admit", admit_body(guest, "guest", "join-code", ""), wall_ms()).unwrap();
    device_sign(&mut act, signer);
    take(g, act)
}

/// A pool of one admin whose device 20 is a member and holds a window of ``body``.
fn pool_with_window(body: serde_json::Value) -> (tempfile::TempDir, GovPool, Identity) {
    let (dir, mut g) = pool(&[phone_admin(A, "ann")], Policy::Secure);
    let holder = ident(20);
    put_admit(&mut g, &holder, &[A]);
    let mut body = body;
    body["fingerprint"] = json!(holder.fingerprint());
    assert_eq!(put(&mut g, "window", body, &[A]), Ingest::Accepted);
    (dir, g, holder)
}

#[test]
fn one_window_act_lets_the_device_key_admit_three_devices_with_no_code() {
    let (_d, mut g) = pool(&[phone_admin(A, "ann")], Policy::Secure);
    let holder = ident(20);
    put_admit(&mut g, &holder, &[A]);
    put(&mut g, "window", json!({"fingerprint": holder.fingerprint()}), &[A]);
    let guests: Vec<Identity> = (30..34).map(ident).collect();
    for guest in &guests {
        device_admit(&mut g, 20, guest);
    }
    let admitted = guests.iter().filter(|d| is_member(&g, d)).count();
    assert_eq!(admitted, 1, "the person's yes was for ONE device; the device key (a file under file custody) admitted {admitted}");
}

#[test]
fn a_window_act_signed_long_ago_opens_ten_fresh_minutes_when_first_seen() {
    let (_d, mut g) = pool(&[phone_admin(A, "ann")], Policy::Secure);
    let holder = ident(20);
    put_admit(&mut g, &holder, &[A]);
    let mut act = make(&g, "window", json!({"fingerprint": holder.fingerprint()}), &[]);
    act.at_ms = wall_ms() - 2 * 24 * 60 * MINUTE as u64; // signed two days ago, delivered now (a peer that was offline, or held back)
    kit::gov::sign(&mut act, A);
    let kept = take(&mut g, act);
    assert!(matches!(kept, Ingest::Refused(_)), "a copy of a window kept back for two days is not taken in: {kept:?}");
    advance(&mut g, MINUTE);
    let guest = ident(40);
    device_admit(&mut g, 20, &guest);
    assert!(!is_member(&g, &guest), "a window act two days old still opened a window at first sight");
}

#[test]
fn a_window_just_inside_its_length_is_taken_in_and_one_just_outside_it_is_not() {
    let (_d, mut g) = pool(&[phone_admin(A, "ann")], Policy::Secure);
    let holder = ident(20);
    put_admit(&mut g, &holder, &[A]);
    // a window is signed for 60 seconds; the clock of the device that reads it may differ by the skew the pool allows (five minutes)
    for (late_ms, taken) in [(30_000i64, true), (60_000 + 5 * MINUTE - 10_000, true), (60_000 + 5 * MINUTE + 10_000, false)] {
        let mut act = make(&g, "window", json!({"fingerprint": holder.fingerprint(), "secs": 60}), &[]);
        act.at_ms = wall_ms() - late_ms as u64;
        kit::gov::sign(&mut act, A);
        let kept = take(&mut g, act);
        assert_eq!(matches!(kept, Ingest::Accepted | Ingest::Known), taken, "signed {late_ms} ms ago: {kept:?}");
    }
}

#[test]
fn a_window_lasts_the_length_the_person_was_shown() {
    let (_d, mut g, _) = pool_with_window(json!({"secs": 60}));
    let early = ident(40);
    device_admit(&mut g, 20, &early);
    assert!(is_member(&g, &early), "inside its minute the window admits the device that proves the code");
    let (_d, mut g, _) = pool_with_window(json!({"secs": 60}));
    advance(&mut g, 2 * MINUTE);
    let late = ident(41);
    device_admit(&mut g, 20, &late);
    assert!(!is_member(&g, &late), "a window the person was shown for 60 seconds is not ten minutes long");
}

#[test]
fn a_window_without_a_length_is_ten_minutes_and_none_is_longer() {
    let (_d, mut g, _) = pool_with_window(json!({}));
    advance(&mut g, 9 * MINUTE);
    let inside = ident(40);
    device_admit(&mut g, 20, &inside);
    assert!(is_member(&g, &inside), "ten minutes is the most there is");
    let (_d, mut g, _) = pool_with_window(json!({}));
    advance(&mut g, 11 * MINUTE);
    let outside = ident(41);
    device_admit(&mut g, 20, &outside);
    assert!(!is_member(&g, &outside));
    let (_d, mut g) = pool(&[phone_admin(A, "ann")], Policy::Secure);
    let holder = ident(20);
    put_admit(&mut g, &holder, &[A]);
    for secs in [0u64, 601, 86_400] {
        let kept = put(&mut g, "window", json!({"fingerprint": holder.fingerprint(), "secs": secs}), &[A]);
        assert!(matches!(kept, Ingest::Refused(_)), "a window of {secs} seconds is refused: {kept:?}");
    }
}

#[test]
fn the_second_window_of_a_device_admits_one_more_and_not_two() {
    let (_d, mut g, holder) = pool_with_window(json!({}));
    let first = ident(40);
    device_admit(&mut g, 20, &first);
    advance(&mut g, 1_000); // the clock of the test is frozen: two admits seen in the same instant are ordered by their ids, and this one is meant to come first
    let again = ident(41);
    device_admit(&mut g, 20, &again);
    assert!(is_member(&g, &first) && !is_member(&g, &again), "a window is spent by the first device it admits");
    // the person says yes again: a new window, a new device
    assert_eq!(put(&mut g, "window", json!({"fingerprint": holder.fingerprint(), "secs": 120}), &[A]), Ingest::Accepted);
    device_admit(&mut g, 20, &again);
    assert!(is_member(&g, &again), "the second yes opens a second window");
}
