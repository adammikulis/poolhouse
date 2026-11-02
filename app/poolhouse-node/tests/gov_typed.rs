//! A pool approved by a typed yes, and what every card says of how a pool is approved.
//!
//! A computer that can raise no prompt of its own (no administrator's account, no polkit, a terminal only) is never left unable to found a pool:
//! its pool is approved by a typed yes at every floor, and says so. That is allowed only where no key above a typed yes is listed, it gives the
//! first computer no power to admit without asking, it keeps every wait, and the cards, the founding line and the level line say plainly that a
//! program running as the person could approve it.

mod kit;

use kit::gov::*;
use poolhouse_node::gov::charter::{shown_whole, Charter, Floors, Unit, MOST_NAME};
use poolhouse_node::gov::describe::founding_line;
use poolhouse_node::gov::ops::founding_prompt;
use poolhouse_node::gov::pool::{ActStatus, Ingest};
use poolhouse_node::membership::Policy;
use poolhouse_node::presence::card::{shows_whole, MOST_SENTENCE};
use serde_json::json;

const A: u8 = 1;
const B: u8 = 2;

fn typed(keys: &[K], policy: Policy) -> Charter {
    charter_floored(keys, policy, Floors::typed())
}

#[test]
fn a_typed_yes_floor_is_allowed_only_where_no_key_above_a_typed_yes_is_listed() {
    typed(&[weak(A, "ann")], Policy::Secure).check_founding().expect("a pool of typed keys, approved by a typed yes");
    for (label, keys) in [("a computer key beside", vec![weak(A, "ann"), computer_admin(B, "bob")]), ("a phone key beside", vec![weak(A, "ann"), phone_admin(B, "bob")]), ("a phone key alone", vec![phone_admin(A, "ann")])] {
        let refused = typed(&keys, Policy::Secure).check_founding().expect_err(label);
        assert!(refused.to_string().contains("approved from a computer or a phone"), "{label}: {refused}");
    }
}

#[test]
fn a_pool_approved_by_a_typed_yes_gives_no_device_the_power_to_admit_without_asking() {
    let mut c = typed(&[weak(A, "ann")], Policy::Secure);
    c.first = c.first.delegating();
    let anchor = c.anchor();
    c.admins = vec![entry(weak(A, "ann"), 1, &anchor)];
    let refused = c.check_founding().expect_err("a no-wait delegate with a typed yes");
    assert!(refused.to_string().contains("does not let any device admit others without asking"), "{refused}");
}

#[test]
fn a_typed_yes_pool_counts_its_key_and_keeps_every_wait() {
    let (_d, mut g) = pool_of(&typed(&[weak(A, "ann")], Policy::Secure));
    let guest = ident(30);
    assert!(matches!(put_admit(&mut g, &guest, &[A]), Ingest::Accepted), "a typed key admits");
    assert!(is_member(&g, &guest));
    let held = make(&g, "set_holds", json!({"admin_add_h": 48}), &[A]);
    let id = held.id();
    take(&mut g, held);
    assert!(matches!(g.status(&id), ActStatus::Pending), "a governance act is held: {:?}", g.status(&id));
    advance(&mut g, HELD);
    assert!(matches!(g.status(&id), ActStatus::Effective), "and takes effect when the hold has run: {:?}", g.status(&id));
    assert!(g.state().roster.delegates.is_empty(), "no device is a delegate until a held `delegate` act says so");
}

#[test]
fn the_cards_say_when_a_pool_is_open_and_when_the_computer_may_admit_for_thirty_days() {
    let delegating = |mut c: Charter| {
        c.first = c.first.delegating();
        let anchor = c.anchor();
        c.admins = c.admins.iter().map(|k| entry(K(if k.key == public(A) { A } else { B }, "ann", k.role, poolhouse_node::gov::keys::Level::Computer), 1, &anchor)).collect();
        c
    };
    let open = delegating(charter(&[computer_admin(A, "ann")], Policy::Open, Unit::Person));
    let line = founding_line(&open);
    assert!(line.contains("(open)") && line.contains("Anyone on this network can join it.") && line.contains("This computer may admit devices without asking for 30 days."), "{line}");
    let secure = delegating(charter(&[computer_admin(A, "ann")], Policy::Secure, Unit::Person));
    let line = founding_line(&secure);
    assert!(line.contains("(secure)") && !line.contains("Anyone on this network") && line.contains("30 days"), "{line}");
    let plain = typed(&[weak(A, "ann")], Policy::Secure);
    let line = founding_line(&plain);
    assert!(line.contains("approved by a typed yes; a program running as you could approve it") && !line.contains("30 days"), "{line}");
    for (c, words) in [(&open, ["OPEN pool", "Anyone on this network can join it.", "30 days"]), (&secure, ["pool", "adds a key", "30 days"])] {
        let said = founding_prompt(c);
        assert!(words.iter().all(|w| said.contains(w)), "{said}");
    }
    assert!(founding_prompt(&plain).contains("typed yes") && !founding_prompt(&plain).contains("30 days"));
}

#[test]
fn the_sentence_of_the_longest_pool_always_fits_the_prompt_whole() {
    let name = "A".repeat(MOST_NAME);
    assert!(shown_whole(&name) && !shown_whole(&format!("{name}A")));
    let mut c = charter(&[computer_admin(A, "ann")], Policy::Open, Unit::Person);
    c.name = name;
    c.first = c.first.delegating();
    let said = founding_prompt(&c);
    assert!(said.chars().count() <= MOST_SENTENCE && shows_whole(&said), "{} characters: {said}", said.chars().count());
}

#[test]
fn a_name_the_prompt_could_not_show_whole_is_no_pool_name() {
    for name in ["my_pool", "caf\u{e9}", "a \"b\"", "it's", "", " x", "a\nb", "a/b", &"x".repeat(MOST_NAME + 1)] {
        assert!(!shown_whole(name), "{name:?}");
        let mut c = charter(&[computer_admin(A, "ann")], Policy::Secure, Unit::Person);
        c.name = name.into();
        assert!(c.check_founding().is_err(), "{name:?} founds no pool");
    }
    for name in ["home", "My home pool", "lab-2.net", &"x".repeat(MOST_NAME)] {
        assert!(shown_whole(name), "{name:?}");
    }
}

#[test]
fn a_delegate_that_needs_two_signers_starts_its_wait_when_the_second_signature_arrives() {
    let (_d, mut g) = pool(&[computer_admin(A, "p1"), computer_admin(B, "p2")], Policy::Secure);
    let delegate = ident(20);
    put_admit(&mut g, &delegate, &[A]);
    let mut act = make(&g, "delegate", json!({"fingerprint": delegate.fingerprint()}), &[A]);
    take(&mut g, act.clone());
    advance(&mut g, 5 * DAY);
    sign(&mut act, B);
    take(&mut g, act);
    advance(&mut g, 5 * DAY);
    assert!(!g.state().roster.delegates.contains_key(&delegate.fingerprint()), "ten days after the first signature but five after the act was complete: still held");
    advance(&mut g, 2 * DAY + HOUR);
    assert!(g.state().roster.delegates.contains_key(&delegate.fingerprint()), "seven days after it was complete it is live");
}
