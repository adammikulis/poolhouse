//! Claims, levels and floors: a key states how it is held, every member computes the level it counts at, a key counts for a class of act
//! only at or above the floor the charter names for it, and what only `computer` keys sign waits.

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

fn computer_floors() -> Floors {
    Floors { routine: Level::Computer, sensitive: Level::Computer, governance: Level::Computer }
}

/// A pool of a phone's key and a computer's key.
fn mixed() -> (tempfile::TempDir, GovPool) {
    pool(&[phone_admin(A, "ann"), computer_admin(B, "bob")], Policy::Secure)
}

#[test]
fn a_pool_of_one_computer_key_founds_admits_and_removes() {
    let a = device();
    found_at(&a, person(ANN, "ann"), "secure", Level::Computer);
    let gov = a.status()["governance"].clone();
    assert_eq!(gov["floors"], json!({"routine": "computer", "sensitive": "computer", "governance": "computer"}));
    assert_eq!((gov["admins"][0]["claim"]["method"].clone(), gov["admins"][0]["counted"].clone()), (json!("keystore-prompt"), json!("computer")), "{gov}");
    let laptop = ident(30);
    let admitted = a.node.lock().unwrap().gov_admit(&laptop.der, "laptop", "join-code").unwrap();
    assert_eq!(admitted.status, Status::Active, "a computer key admits a device");
    let removed = a.node.lock().unwrap().gov_remove(&laptop.fingerprint()).unwrap();
    assert_eq!(removed.status, Status::Revoked, "a computer key removes a member");
}

#[test]
fn a_phone_claim_counts_as_computer() {
    let (_d, mut g) = pool(&[phone_admin(A, "ann"), computer_admin(B, "ann")], Policy::Secure);
    assert_eq!(floors(&g), computer_floors());
    let keys = &g.state().chain.latest().charter.admins;
    assert!(keys.iter().all(|k| k.level() == Level::Computer), "a phone's claim and a computer's both count at computer");
    let laptop = ident(10);
    put_admit(&mut g, &laptop, &[B]);
    assert!(is_member(&g, &laptop), "a computer key admits a device in a pool that lists a phone key");
    put(&mut g, "remove", json!({"fingerprint": laptop.fingerprint()}), &[B]);
    assert!(is_removed(&g, &laptop), "and removes it: the phone key is one more counting key, not a higher one");
}

#[test]
fn a_floor_above_computer_is_refused_because_no_key_holds_it() {
    for keys in [vec![computer_admin(A, "ann")], vec![phone_admin(A, "ann")], vec![K(A, "ann", Role::Admin, Level::Native)]] {
        let (_d, mut g) = pool(&keys, Policy::Secure);
        for level in ["phone", "native"] {
            let act = make(&g, "set_floors", json!({"sensitive": level}), &[A]);
            take(&mut g, act.clone());
            assert!(matches!(g.status(&act.id()), ActStatus::Void(w) if w.contains("no listed key holds that level")), "{level}: {:?}", g.status(&act.id()));
        }
        advance(&mut g, 8 * DAY);
        assert_eq!(floors(&g), computer_floors(), "nothing moved, so the pool still counts");
    }
}

#[test]
fn a_charter_with_other_floors_is_another_pool() {
    let keys = [phone_admin(A, "ann"), computer_admin(B, "bob")];
    let one = charter_floored(&keys, Policy::Secure, computer_floors());
    let mut other = one.clone();
    other.floors.sensitive = Level::Phone;
    assert_ne!(one.pool_id(), other.pool_id(), "the floors are part of what the pool id hashes");
    assert_eq!(one.pool_id(), one.clone().pool_id());
}

#[test]
fn a_charter_with_another_claim_is_another_pool() {
    let one = charter(&[computer_admin(A, "ann")], Policy::Secure, poolhouse_node::gov::charter::Unit::Person);
    let mut other = one.clone();
    other.admins[0].claim = Some(claim_at(Level::Phone));
    assert_ne!(one.pool_id(), other.pool_id(), "the claim is part of what the pool id hashes");
    other.admins[0].claim = None;
    assert!(other.check_keys(1).is_err(), "an admin key that states no claim is no key");
}

#[test]
fn a_key_added_below_the_floor_counts_nothing() {
    let (_d, mut g) = pool(&[computer_admin(A, "ann")], Policy::Secure);
    assert_eq!(floors(&g), computer_floors());
    let listing = add(&g, weak(B, "bob"), &[A]);
    take(&mut g, listing);
    advance(&mut g, 8 * DAY);
    assert!(admins(&g).contains(&public(B)), "the key is listed");
    assert_eq!(floors(&g), computer_floors(), "adding a key moves no floor");
    let laptop = ident(10);
    assert!(refused(put_admit(&mut g, &laptop, &[B])).contains("below the pool's floor"));
    let removal = make(&g, "remove", json!({"fingerprint": laptop.fingerprint()}), &[B]);
    assert!(refused(take(&mut g, removal)).contains("below the pool's floor"));
}

#[test]
fn a_floor_is_a_typed_yes_only_where_no_key_above_one_is_listed_and_is_never_lowered_to_one() {
    let weak_floor = charter_floored(&[computer_admin(A, "ann")], Policy::Secure, Floors { routine: Level::Weak, sensitive: Level::Computer, governance: Level::Computer });
    let why = poolhouse_node::gov::pool::founding(&weak_floor.found_act()).unwrap_err().to_string();
    assert!(why.contains("is approved from one, not by a typed yes"), "a computer key beside a typed-yes floor: {why}");
    let (_d, mut g) = pool(&[computer_admin(A, "ann")], Policy::Secure);
    let act = make(&g, "set_floors", json!({"routine": "weak"}), &[A]);
    assert!(refused(take(&mut g, act)).contains("never lowered to a typed yes"));
    assert_eq!(floors(&g), computer_floors());
}

#[test]
fn a_claim_whose_prompt_does_not_fit_its_method_is_no_claim() {
    use poolhouse_node::gov::keys::{Claim, Method, Os, Prompt};
    let mut one = charter(&[computer_admin(A, "ann")], Policy::Secure, poolhouse_node::gov::charter::Unit::Person);
    for (method, prompt) in [(Method::KeystorePrompt, Prompt::TypedYes), (Method::KeystoreTyped, Prompt::OsDialog), (Method::PhoneKeystore, Prompt::OsDialog), (Method::File, Prompt::Biometric)] {
        one.admins[0].claim = Some(Claim { method, os: Os::Macos, prompt });
        assert!(one.check_keys(1).is_err(), "{method:?} with {prompt:?}");
    }
    let word = serde_json::from_value::<Claim>(json!({"method": "enclave", "os": "macos", "prompt": "os-dialog"}));
    assert!(word.is_err(), "a word outside the vocabulary is not a claim");
}

#[test]
fn computer_only_governance_is_held_seven_days() {
    for keys in [vec![computer_admin(A, "ann")], vec![phone_admin(A, "ann")]] {
        let (_d, mut g) = pool(&keys, Policy::Secure);
        let added = add(&g, computer_admin(B, "bob"), &[A]);
        assert_eq!(take(&mut g, added.clone()), Ingest::Accepted);
        advance(&mut g, 25 * HOUR);
        assert!(!admins(&g).contains(&public(B)), "the pool's own 24 hours are not enough: every counting key is a computer key");
        advance(&mut g, 5 * DAY);
        assert!(!admins(&g).contains(&public(B)), "six days and an hour are less than seven");
        advance(&mut g, 2 * DAY);
        assert!(admins(&g).contains(&public(B)));
    }

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
    assert_eq!(data_act_wait_ms("file_delete"), DAY, "an irreversible act waits a day for a key to cancel it");
    assert_eq!(data_act_wait_ms("shard_consent"), 0, "consent to sharding is turned off again: it is sensitive, not irreversible");
    assert_eq!(data_act_wait_ms("sync_now"), 0, "a reversible act never waits");
}

#[test]
fn member_remove_needs_the_sensitive_floor() {
    let (_d, mut g) = pool(&[phone_admin(A, "ann"), weak(B, "bob")], Policy::Secure);
    let laptop = ident(10);
    put_admit(&mut g, &laptop, &[A]);
    let by_typed_yes = make(&g, "remove", json!({"fingerprint": laptop.fingerprint()}), &[B]);
    assert!(refused(take(&mut g, by_typed_yes)).contains("below the pool's floor"));
    assert!(is_member(&g, &laptop));
    put(&mut g, "remove", json!({"fingerprint": laptop.fingerprint()}), &[A]);
    assert!(is_removed(&g, &laptop), "the key at the floor removes it");

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
    assert!(!admins(&g).contains(&public(D)), "24 hours do not list the first recovery key");
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
    let (_d, mut g) = pool(&[phone_admin(A, "ann"), weak(B, "bob"), recovery(D, "ann")], Policy::Secure);
    let swap = replace(&g, recovery(E, "ann"), &[A]);
    take(&mut g, swap.clone());
    let by_typed_yes = make(&g, "cancel", json!({"act": swap.id()}), &[B]);
    assert!(refused(take(&mut g, by_typed_yes)).contains("below the pool's floor"), "a typed yes cancels nothing");
    assert_eq!(put(&mut g, "cancel", json!({"act": swap.id()}), &[A]), Ingest::Accepted);
    advance(&mut g, 8 * DAY);
    assert!(admins(&g).contains(&public(D)));
}

#[test]
fn the_level_line_survives_the_300_character_cut() {
    use poolhouse_node::gov::describe::compose;
    let level = "LEVEL: This change is approved from this computer; a program running as you could approve it without asking.";
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
    assert!(said.chars().count() <= 300 && line.starts_with("LEVEL: This change is approved from this computer; a program running as you could approve it without asking."), "{text}");
    assert!(line.contains("waits 7 days"), "a governance act says it waits: {line}");
}
