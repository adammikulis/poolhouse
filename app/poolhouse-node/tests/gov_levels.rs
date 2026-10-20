//! Levels and floors: a key counts for a class of act only at or above the floor the charter names for it, and what only `computer`
//! keys sign waits.

mod kit;

use kit::gov::*;
use kit::govnode::*;
use kit::netkit::*;
use poolhouse_node::gov::act::Act;
use poolhouse_node::gov::charter::Floors;
use poolhouse_node::gov::keys::{Level, Role};
use poolhouse_node::gov::pool::{ActStatus, GovPool, Ingest};
use poolhouse_node::membership::{Policy, Status};
use serde_json::{json, Value};

const A: u8 = 1;
const B: u8 = 2;
const D: u8 = 4;
const E: u8 = 5;

fn refused(i: Ingest) -> String {
    match i {
        Ingest::Refused(why) => why,
        other => panic!("expected a refusal, got {other:?}"),
    }
}

fn add(g: &GovPool, k: K, seeds: &[u8]) -> Act {
    let since = g.state().chain.latest().n + 1;
    make(g, "admin_add", json!({"entry": entry_after(k, since, g)}), seeds)
}

fn replace(g: &GovPool, k: K, seeds: &[u8]) -> Act {
    let since = g.state().chain.latest().n + 1;
    make(g, "recovery_replace", json!({"entry": entry_after(k, since, g)}), seeds)
}

fn floors(g: &GovPool) -> Floors {
    g.state().chain.latest().charter.floors
}

fn all(level: Level) -> Floors {
    Floors { routine: level, sensitive: level, governance: level }
}

fn mixed() -> (tempfile::TempDir, GovPool) {
    pool(&[phone_admin(A, "ann"), computer_admin(B, "bob")], Policy::Secure)
}

#[test]
fn a_pool_of_one_computer_key_founds_admits_and_removes() {
    let a = device();
    found_at(&a, person(ANN, "ann"), "secure", Level::Computer);
    assert_eq!(a.status()["governance"]["floors"], json!({"routine": "computer", "sensitive": "computer", "governance": "computer"}));
    assert_eq!(a.status()["governance"]["levels"], json!(["computer"]));
    let laptop = ident(30);
    let admitted = a.node.lock().unwrap().gov_admit(&laptop.der, "laptop", "join-code").unwrap();
    assert_eq!(admitted.status, Status::Active, "a computer key admits a device");
    let removed = a.node.lock().unwrap().gov_remove(&laptop.fingerprint()).unwrap();
    assert_eq!(removed.status, Status::Revoked, "a computer key removes a member");
}

#[test]
fn a_floor_of_phone_makes_a_computer_key_count_for_nothing() {
    let (_d, mut g) = mixed();
    assert_eq!(floors(&g), Floors { routine: Level::Computer, sensitive: Level::Phone, governance: Level::Phone });
    let laptop = ident(10);
    assert!(refused(put_admit(&mut g, &laptop, &[B])).contains("below the pool's floor"), "a computer key admits no device under a phone floor");
    put_admit(&mut g, &laptop, &[A]);
    assert!(is_member(&g, &laptop));
    for kind in ["remove", "admin_add", "set_holds"] {
        let body = match kind {
            "remove" => json!({"fingerprint": laptop.fingerprint()}),
            "admin_add" => json!({"entry": entry_after(computer_admin(D, "dee"), 2, &g)}),
            _ => json!({"dispute_d": 3}),
        };
        let act = make(&g, kind, body, &[B]);
        assert!(refused(take(&mut g, act)).contains("below the pool's floor"), "{kind}");
    }
    assert!(is_member(&g, &laptop));
}

#[test]
fn lowering_a_floor_is_held_and_needs_keys_at_the_old_floor() {
    let (_d, mut g) = mixed();
    let thief = make(&g, "set_floors", json!({"sensitive": "computer"}), &[B]);
    assert!(refused(take(&mut g, thief)).contains("below the pool's floor"), "a computer key cannot lower a phone floor");
    let lowering = make(&g, "set_floors", json!({"sensitive": "computer"}), &[A]);
    assert_eq!(take(&mut g, lowering.clone()), Ingest::Accepted);
    assert_eq!(g.status(&lowering.id()), ActStatus::Pending, "lowering is a weakening: held");
    advance(&mut g, 23 * HOUR);
    assert_eq!(floors(&g).sensitive, Level::Phone);
    advance(&mut g, 2 * HOUR);
    assert_eq!(floors(&g).sensitive, Level::Computer);

    let loose = charter_floored(&[phone_admin(A, "ann"), computer_admin(B, "ann")], Policy::Secure, Floors { routine: Level::Computer, sensitive: Level::Phone, governance: Level::Computer });
    let (_d2, mut g) = pool_of(&loose);
    let act = make(&g, "set_floors", json!({"sensitive": "computer"}), &[B]);
    assert_eq!(take(&mut g, act.clone()), Ingest::Accepted, "B counts for governance here");
    advance(&mut g, 8 * DAY);
    assert_eq!(floors(&g).sensitive, Level::Phone, "but the floor it lowers is the phone's, and B holds no phone key");
    assert!(matches!(g.status(&act.id()), ActStatus::Short(0, 1)), "{:?}", g.status(&act.id()));
}

#[test]
fn raising_a_floor_takes_effect_at_once() {
    let low = charter_floored(&[phone_admin(A, "ann"), computer_admin(B, "ann")], Policy::Secure, all(Level::Computer));
    let (_d, mut g) = pool_of(&low);
    let by_computer = make(&g, "set_floors", json!({"sensitive": "phone"}), &[B]);
    take(&mut g, by_computer.clone());
    assert_eq!(g.status(&by_computer.id()), ActStatus::Pending, "a computer key raising a floor waits");
    let raise = make(&g, "set_floors", json!({"sensitive": "phone"}), &[A]);
    assert_eq!(take(&mut g, raise.clone()), Ingest::Accepted);
    assert_eq!(floors(&g).sensitive, Level::Phone, "a phone key's raise has no hold");
    assert_eq!(g.state().chain.latest().n, 2);
}

#[test]
fn a_charter_with_other_floors_is_another_pool() {
    let keys = [phone_admin(A, "ann"), computer_admin(B, "bob")];
    let one = charter_floored(&keys, Policy::Secure, Floors { routine: Level::Computer, sensitive: Level::Phone, governance: Level::Phone });
    let mut other = one.clone();
    other.floors.sensitive = Level::Computer;
    assert_ne!(one.pool_id(), other.pool_id(), "the floors are part of what the pool id hashes");
    assert_eq!(one.pool_id(), one.clone().pool_id());
}

#[test]
fn a_key_added_below_the_floor_counts_nothing() {
    let (_d, mut g) = pool(&[phone_admin(A, "ann")], Policy::Secure);
    assert_eq!(floors(&g), all(Level::Phone));
    let listing = add(&g, computer_admin(B, "bob"), &[A]);
    take(&mut g, listing);
    advance(&mut g, 25 * HOUR);
    assert!(admins(&g).contains(&public(B)), "the key is listed");
    assert_eq!(floors(&g), all(Level::Phone), "adding a key moves no floor");
    let laptop = ident(10);
    assert!(refused(put_admit(&mut g, &laptop, &[B])).contains("below the pool's floor"));
    let removal = make(&g, "remove", json!({"fingerprint": laptop.fingerprint()}), &[B]);
    assert!(refused(take(&mut g, removal)).contains("below the pool's floor"));
}

#[test]
fn a_key_is_listed_at_no_level_above_the_keys_that_listed_it() {
    let (_d, mut g) = pool(&[computer_admin(A, "ann")], Policy::Secure);
    let claim = add(&g, phone_admin(B, "bob"), &[A]);
    assert_eq!(take(&mut g, claim.clone()), Ingest::Accepted);
    assert!(matches!(g.status(&claim.id()), ActStatus::Void(w) if w.contains("no level above")), "{:?}", g.status(&claim.id()));
    advance(&mut g, 8 * DAY);
    assert!(!admins(&g).contains(&public(B)), "a computer key cannot list a phone key");
    let same = add(&g, computer_admin(B, "bob"), &[A]);
    take(&mut g, same);
    advance(&mut g, 8 * DAY);
    assert!(admins(&g).contains(&public(B)), "it can list a computer key");

    let (_d2, mut by_phone) = pool(&[phone_admin(A, "ann")], Policy::Secure);
    let listing = add(&by_phone, phone_admin(B, "bob"), &[A]);
    take(&mut by_phone, listing);
    advance(&mut by_phone, 25 * HOUR);
    assert!(admins(&by_phone).contains(&public(B)), "a phone key lists a phone key");
}

#[test]
fn a_floor_is_never_weak() {
    let weak_floor = charter_floored(&[phone_admin(A, "ann")], Policy::Secure, Floors { routine: Level::Weak, sensitive: Level::Phone, governance: Level::Phone });
    let why = poolhouse_node::gov::pool::founding(&weak_floor.found_act()).unwrap_err().to_string();
    assert!(why.contains("never a typed yes"), "{why}");
    let (_d, mut g) = pool(&[phone_admin(A, "ann")], Policy::Secure);
    let act = make(&g, "set_floors", json!({"routine": "weak"}), &[A]);
    assert!(refused(take(&mut g, act)).contains("never a typed yes"));
    assert_eq!(floors(&g), all(Level::Phone));
}

#[test]
fn raising_a_floor_to_a_level_nobody_holds_is_refused() {
    let (_d, mut g) = pool(&[computer_admin(A, "ann")], Policy::Secure);
    let act = make(&g, "set_floors", json!({"sensitive": "phone"}), &[A]);
    take(&mut g, act.clone());
    assert!(matches!(g.status(&act.id()), ActStatus::Void(w) if w.contains("no listed key holds that level")), "{:?}", g.status(&act.id()));
    advance(&mut g, 8 * DAY);
    assert_eq!(floors(&g), all(Level::Computer), "nothing moved, so the pool still counts");
}

#[test]
fn computer_only_governance_is_held_seven_days() {
    let (_d, mut g) = pool(&[computer_admin(A, "ann")], Policy::Secure);
    let added = add(&g, computer_admin(B, "bob"), &[A]);
    assert_eq!(take(&mut g, added.clone()), Ingest::Accepted);
    advance(&mut g, 25 * HOUR);
    assert!(!admins(&g).contains(&public(B)), "the pool's own 24 hours are not enough when only a computer signed");
    advance(&mut g, 5 * DAY);
    assert!(!admins(&g).contains(&public(B)), "six days and an hour are less than seven");
    advance(&mut g, 2 * DAY);
    assert!(admins(&g).contains(&public(B)));

    let (_d2, mut phone) = pool(&[phone_admin(A, "ann")], Policy::Secure);
    let listing = add(&phone, phone_admin(B, "bob"), &[A]);
    take(&mut phone, listing);
    advance(&mut phone, 25 * HOUR);
    assert!(admins(&phone).contains(&public(B)), "a phone key's governance act keeps the pool's own hold");

    let (_d3, mut g) = pool(&[computer_admin(A, "ann")], Policy::Secure);
    let laptop = ident(11);
    put_admit(&mut g, &laptop, &[A]);
    put(&mut g, "delegate", json!({"fingerprint": laptop.fingerprint()}), &[A]);
    assert!(!g.state().roster.delegates.contains_key(&laptop.fingerprint()), "a delegate a computer alone granted is held too");
    advance(&mut g, 7 * DAY + HOUR);
    assert!(g.state().roster.delegates.contains_key(&laptop.fingerprint()));
}

#[test]
fn computer_only_file_delete_waits_a_cancel_window() {
    use poolhouse_node::gov::charter::data_act_wait_ms;
    assert_eq!(data_act_wait_ms("file_delete", &[Level::Computer]), DAY, "an irreversible act that only a computer approved waits a day");
    assert_eq!(data_act_wait_ms("shard_consent", &[Level::Computer, Level::Computer]), DAY);
    assert_eq!(data_act_wait_ms("file_delete", &[Level::Computer, Level::Phone]), 0);
    assert_eq!(data_act_wait_ms("file_delete", &[Level::Native]), 0);
    assert_eq!(data_act_wait_ms("sync_now", &[Level::Computer]), 0, "a reversible act never waits");
    assert_eq!(data_act_wait_ms("file_delete", &[Level::Weak]), 0, "a typed yes is the developer's path and is not held");
    assert_eq!(data_act_wait_ms("file_delete", &[Level::Weak, Level::Computer]), DAY);
    assert_eq!(data_act_wait_ms("file_delete", &[]), 0);
}

#[test]
fn member_remove_needs_the_sensitive_floor() {
    let (_d, mut g) = mixed();
    let laptop = ident(10);
    put_admit(&mut g, &laptop, &[A]);
    let by_computer = make(&g, "remove", json!({"fingerprint": laptop.fingerprint()}), &[B]);
    assert!(refused(take(&mut g, by_computer)).contains("below the pool's floor"));
    assert!(is_member(&g, &laptop));
    put(&mut g, "remove", json!({"fingerprint": laptop.fingerprint()}), &[A]);
    assert!(is_removed(&g, &laptop), "the phone key removes it");

    let (_d2, mut two) = pool(&[phone_admin(A, "ann"), phone_admin(B, "ben")], Policy::Secure);
    let guest = ident(12);
    put_admit(&mut two, &guest, &[A]);
    let alone = make(&two, "remove", json!({"fingerprint": guest.fingerprint()}), &[A]);
    take(&mut two, alone.clone());
    assert!(matches!(two.status(&alone.id()), ActStatus::Short(1, 2)), "a removal is sensitive: two people hold keys, so it takes both");
    assert!(!is_removed(&two, &guest));
}

#[test]
fn the_first_recovery_key_is_held_seven_days() {
    let (_d, mut g) = pool(&[phone_admin(A, "ann")], Policy::Secure);
    let act = add(&g, recovery(D, "ann"), &[A]);
    assert_eq!(take(&mut g, act), Ingest::Accepted);
    advance(&mut g, 25 * HOUR);
    assert!(!admins(&g).contains(&public(D)), "a phone's 24 hours do not list the first recovery key");
    advance(&mut g, 6 * DAY);
    assert!(admins(&g).contains(&public(D)));
    assert!(g.state().chain.latest().charter.admins.iter().any(|k| k.role == Role::Recovery));
}

#[test]
fn recovery_replace_is_held_and_the_recovery_key_cancels_it() {
    let (_d, mut g) = pool(&[phone_admin(A, "ann"), recovery(D, "ann")], Policy::Secure);
    let swap = replace(&g, recovery(E, "ann"), &[A]);
    assert_eq!(take(&mut g, swap.clone()), Ingest::Accepted);
    advance(&mut g, 25 * HOUR);
    assert!(admins(&g).contains(&public(D)) && !admins(&g).contains(&public(E)), "held 7 days, not 24 hours");
    advance(&mut g, 7 * DAY);
    assert!(!admins(&g).contains(&public(D)) && admins(&g).contains(&public(E)), "the new recovery key replaces the old one");

    let (_d2, mut g) = pool(&[phone_admin(A, "ann"), recovery(D, "ann")], Policy::Secure);
    let swap = replace(&g, recovery(E, "ann"), &[A]);
    take(&mut g, swap.clone());
    advance(&mut g, 3 * DAY);
    assert_eq!(put(&mut g, "cancel", json!({"act": swap.id()}), &[D]), Ingest::Accepted);
    advance(&mut g, 8 * DAY);
    assert!(admins(&g).contains(&public(D)) && !admins(&g).contains(&public(E)), "the recovery key cancelled the swap");
    let list = add(&g, recovery(E, "ann"), &[A]);
    take(&mut g, list.clone());
    assert!(matches!(g.status(&list.id()), ActStatus::Void(w) if w.contains("recovery_replace")), "a second recovery key is listed only by a replace");
}

#[test]
fn only_the_recovery_key_or_a_key_at_the_floor_cancels_a_replace() {
    let (_d, mut g) = pool(&[phone_admin(A, "ann"), computer_admin(B, "bob"), recovery(D, "ann")], Policy::Secure);
    let swap = replace(&g, recovery(E, "ann"), &[A]);
    take(&mut g, swap.clone());
    let by_computer = make(&g, "cancel", json!({"act": swap.id()}), &[B]);
    assert!(refused(take(&mut g, by_computer)).contains("below the pool's floor"), "a computer key under a phone floor cancels nothing");
    assert_eq!(put(&mut g, "cancel", json!({"act": swap.id()}), &[A]), Ingest::Accepted);
    advance(&mut g, 8 * DAY);
    assert!(admins(&g).contains(&public(D)));
}

#[test]
fn the_level_line_survives_the_300_character_cut() {
    use poolhouse_node::gov::describe::compose;
    let level = "LEVEL: This change is approved from this computer. A program running as you could approve it without asking; a phone would stop that.";
    let long = "x".repeat(450);
    let shown = compose(&long, level);
    let (said, line) = shown.split_once('\n').unwrap();
    assert_eq!((said.chars().count(), line), (300, level), "the sentence is cut and the line is whole");

    let a = device();
    found_at(&a, person(ANN, "ann"), "secure", Level::Computer);
    let drafted = a.ok("pool_draft", "", &a.token, json!({"kind": "set_holds", "body": {"admin_add_h": 2}}));
    let summary: Value = drafted["summary"].clone();
    let text = summary.as_str().unwrap();
    let (said, line) = text.split_once('\n').unwrap();
    assert!(said.chars().count() <= 300 && line.starts_with("LEVEL: This change is approved from this computer."), "{text}");
    assert!(line.contains("waits 7 days"), "a governance act under a computer floor says it waits: {line}");
}
