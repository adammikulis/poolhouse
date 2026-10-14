//! The charter format: an admin key says which algorithm it is, a phone's P-256 key founds a pool and signs in it, a pool is its
//! anchor however its keys sign it, a signature has one canonical form, and a charter of another version is refused.

mod kit;

use kit::gov::*;
use kit::netkit::*;
use kit::p256::PhoneKey;
use poolhouse_node::fsutil::{hex, unhex};
use poolhouse_node::gov::act::{Act, ActSig, SigKind};
use poolhouse_node::gov::charter::{Charter, Unit};
use poolhouse_node::gov::keys::{AdminKey, Alg, Role, Strength};
use poolhouse_node::gov::pool::{founding, ActStatus, GovPool, Ingest};
use poolhouse_node::gov::signer::RigSigner;
use poolhouse_node::grants::person::{fingerprint_of, verifies_p256, verifies_with};
use poolhouse_node::membership::{Policy, Pool};
use poolhouse_node::poolops::wall_ms;
use serde_json::{json, Value};

const A: u8 = 1;
const B: u8 = 2;
const C: u8 = 3;

fn phone_entry(phone: &PhoneKey, person: &str, since: u64) -> AdminKey {
    AdminKey { key: phone.public.clone(), alg: Alg::P256, role: Role::Admin, strength: Strength::Strong, person: person.into(), since, acceptance: String::new(), label: format!("{person}'s phone") }
}

/// A charter of one phone key, accepted by the phone with the signature ``sign`` makes.
fn phone_charter(phone: &PhoneKey, sign: impl Fn(&PhoneKey, &[u8]) -> String) -> Charter {
    let mut c = Charter::new("home", Policy::Secure, "", vec![phone_entry(phone, "ann", 1)], first_of(&ident(FIRST).der), wall_ms());
    let anchor = c.anchor();
    c.admins[0].acceptance = sign(phone, &c.admins[0].acceptance_message(&anchor, ""));
    c
}

fn canonical(phone: &PhoneKey, message: &[u8]) -> String {
    phone.sign(message)
}

fn high(phone: &PhoneKey, message: &[u8]) -> String {
    phone.sign_high(message)
}

fn sign_phone(act: &mut Act, phone: &PhoneKey) {
    act.add_signature(ActSig { kind: SigKind::Admin, key: phone.public.clone(), sig: phone.sign(&act.sign_bytes()) });
}

fn phone_pool(phone: &PhoneKey) -> (tempfile::TempDir, GovPool) {
    pool_of(&phone_charter(phone, canonical))
}

fn refused(i: Ingest) -> String {
    match i {
        Ingest::Refused(why) => why,
        other => panic!("expected a refusal, got {other:?}"),
    }
}

// -- a phone's key founds a pool and signs in it --------------------------------------------------------------------------------

#[test]
fn a_phone_key_founds_a_pool_and_its_signature_admits_a_device() {
    let phone = PhoneKey::new();
    let (_d, mut g) = phone_pool(&phone);
    assert!(g.state().chain.latest().charter.is_strong(&phone.public), "the phone key counts toward the thresholds");
    let laptop = ident(10);
    let mut act = admit(&g, &laptop, &[]);
    sign_phone(&mut act, &phone);
    assert_eq!(take(&mut g, act), Ingest::Accepted);
    assert!(is_member(&g, &laptop), "an admit the phone signed puts the device in the pool");
    assert!(g.admin_scope(&fingerprint_of("p256", &phone.public).unwrap()), "a grant scoped to the pools this key administers matches");
}

#[test]
fn a_phone_key_and_a_typed_key_of_two_people_count_as_two_for_a_change_of_admins() {
    let phone = PhoneKey::new();
    let mut c = phone_charter(&phone, canonical);
    c.admins.push(entry(strong(B, "ben"), 1, ""));
    c.admins.sort_by(|a, b| a.key.cmp(&b.key));
    let anchor = c.anchor();
    for k in c.admins.iter_mut() {
        let message = k.acceptance_message(&anchor, "");
        k.acceptance = if k.alg == Alg::P256 { phone.sign(&message) } else { RigSigner::sign_as(B, &message) };
    }
    let (_d, mut g) = pool_of(&c);
    let added = entry_after(strong(C, "cy"), 2, &g);
    let mut one = make(&g, "admin_add", json!({"entry": added}), &[]);
    sign_phone(&mut one, &phone);
    take(&mut g, one.clone());
    assert_eq!(g.status(&one.id()), ActStatus::Short(1, 2), "a change of admins needs two people, and the phone is one");
    let mut both = one.clone();
    sign(&mut both, B);
    assert_eq!(take(&mut g, both.clone()), Ingest::Merged);
    advance(&mut g, 25 * HOUR);
    assert!(admins(&g).contains(&public(C)), "the phone's signature and a typed key's make two people");
}

// -- F5: one set of terms is one pool -------------------------------------------------------------------------------------------

#[test]
fn a_charter_minted_twice_from_one_set_of_terms_is_one_pool_whatever_its_key_signs() {
    let phone = PhoneKey::new();
    let one = phone_charter(&phone, canonical);
    let mut two = one.clone();
    let anchor = two.anchor();
    two.admins[0].acceptance = phone.sign(&two.admins[0].acceptance_message(&anchor, ""));
    assert_ne!(one.admins[0].acceptance, two.admins[0].acceptance, "an ECDSA signature differs each time it is made");
    assert_eq!(one.pool_id(), two.pool_id(), "the pool is its anchor, not the signatures");
    assert_eq!(one.hash(), two.hash(), "the charter the acts name does not depend on how its founding keys signed");
    assert_ne!(one.found_act().id(), two.found_act().id());
    let (_a, mut a) = pool_of(&one);
    let (_b, mut b) = pool_of(&two);
    assert_eq!(a.id, b.id);
    assert_eq!(take(&mut a, two.found_act()), Ingest::Known, "the other copy of the founding is the founding it holds");
    let laptop = ident(10);
    let mut act = admit(&a, &laptop, &[]);
    sign_phone(&mut act, &phone);
    assert_eq!(take(&mut a, act.clone()), Ingest::Accepted);
    assert_eq!(take(&mut b, act), Ingest::Accepted, "an act made on one copy follows the charter of the other");
    assert!(is_member(&b, &laptop));
}

#[test]
fn a_changed_term_is_another_pool() {
    let phone = PhoneKey::new();
    let one = phone_charter(&phone, canonical);
    let mut renamed = one.clone();
    renamed.name = "work".into();
    assert_ne!(one.pool_id(), renamed.pool_id(), "the name is a term");
    let mut later = one.clone();
    later.at_ms += 1;
    assert_ne!(one.pool_id(), later.pool_id(), "so is the time it was made");
    assert_eq!(one.pool_id(), one.anchor()[..32], "the id is the first half of the anchor");
    assert!(founding(&renamed.found_act()).is_err(), "and the key's acceptance was made for the terms it saw");
}

#[test]
fn a_founding_act_is_made_of_its_charter_and_nothing_else() {
    let phone = PhoneKey::new();
    let c = phone_charter(&phone, canonical);
    assert!(founding(&c.found_act()).is_ok());
    let mut other_nonce = c.found_act();
    other_nonce.nonce = "00".repeat(16);
    assert!(founding(&other_nonce).is_err(), "a founding act carries the nonce its charter makes");
    let mut signed = c.found_act();
    signed.signatures.push(ActSig { kind: SigKind::Admin, key: phone.public.clone(), sig: phone.sign(b"x") });
    assert!(founding(&signed).is_err(), "and no signature of its own");
}

// -- F5: a signature has one canonical form -------------------------------------------------------------------------------------

#[test]
fn the_high_s_twin_of_a_valid_signature_is_valid_to_the_curve_and_refused_by_the_pool() {
    let phone = PhoneKey::new();
    let message = b"the same bytes";
    assert!(verifies_p256(&phone.public, message, &phone.sign(message)));
    let twin = phone.sign_high(message);
    assert!(verifies_p256(&phone.public, message, &twin), "the premise: the curve accepts both forms");
    assert_ne!(twin, phone.sign(message));
    let (_d, mut g) = phone_pool(&phone);
    let laptop = ident(10);
    let mut act = admit(&g, &laptop, &[]);
    act.add_signature(ActSig { kind: SigKind::Admin, key: phone.public.clone(), sig: phone.sign_high(&act.sign_bytes()) });
    assert!(refused(take(&mut g, act)).contains("signature"), "the pool takes the low-s form only");
    assert!(!is_member(&g, &laptop));
}

#[test]
fn a_founding_key_that_accepts_with_a_high_s_signature_founds_nothing() {
    let phone = PhoneKey::new();
    let c = phone_charter(&phone, high);
    let err = founding(&c.found_act()).unwrap_err().to_string();
    assert!(err.contains("accept"), "{err}");
    assert!(founding(&phone_charter(&phone, canonical).found_act()).is_ok());
}

#[test]
fn a_later_keys_high_s_acceptance_lists_nothing() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let phone = PhoneKey::new();
    let mut listed = phone_entry(&phone, "ben", 2);
    let (anchor, prev) = (g.id.clone(), g.context().1);
    listed.acceptance = phone.sign_high(&listed.acceptance_message(&anchor, &prev));
    let act = make(&g, "admin_add", json!({"entry": listed}), &[A]);
    assert!(refused(take(&mut g, act)).contains("acceptance"), "an entry whose acceptance is not canonical is not a well formed entry");
}

#[test]
fn a_signature_of_the_wrong_form_for_its_key_is_refused_before_it_is_checked() {
    let phone = PhoneKey::new();
    let (_d, mut g) = phone_pool(&phone);
    let laptop = ident(10);
    let good = {
        let mut act = admit(&g, &laptop, &[]);
        sign_phone(&mut act, &phone);
        act
    };
    let der = good.signatures[0].sig.clone();
    let forms = [
        ("an Ed25519 length for a P-256 key", "ab".repeat(64)),
        ("trailing bytes after the DER", format!("{der}00")),
        ("a DER cut short", der[..der.len() - 2].to_string()),
        ("not hex", "zz".repeat(35)),
        ("empty", String::new()),
        ("a sequence of nothing", "3000".into()),
        ("a long form length", format!("3081{}", &der[2..])),
        ("an integer padded with a zero it does not need", format!("3045022100{}0220{}", "01".repeat(32), "02".repeat(32))),
        ("a zero s", format!("3025022001{}020100", "01".repeat(31))),
    ];
    for (what, sig) in forms {
        let mut act = good.clone();
        act.signatures[0].sig = sig;
        let why = refused(take(&mut g, act));
        assert!(why.contains("canonical signature"), "{what}: the form is refused before the curve is asked: {why}");
    }
    let mut ed = good.clone();
    ed.signatures[0].key = public(A);
    assert!(take(&mut g, ed).ne(&Ingest::Accepted), "a P-256 signature under an Ed25519 key is no signature");
    assert!(!is_member(&g, &laptop));
    assert_eq!(take(&mut g, good), Ingest::Accepted, "and the form that is right is taken");
}

#[test]
fn an_ed25519_key_is_never_asked_for_a_der_signature_and_a_short_one_is_refused() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let laptop = ident(10);
    let mut act = admit(&g, &laptop, &[A]);
    act.signatures[0].sig = act.signatures[0].sig[..126].to_string();
    assert!(refused(take(&mut g, act)).contains("64 bytes"));
    let mut der_form = admit(&g, &laptop, &[A]);
    der_form.signatures[0].sig = PhoneKey::new().sign(b"x");
    assert!(refused(take(&mut g, der_form)).contains("64 bytes"));
}

#[test]
fn an_algorithm_this_build_does_not_know_verifies_nothing_and_has_no_fingerprint() {
    let message = b"bytes";
    let sig = RigSigner::sign_as(A, message);
    assert!(verifies_with("ed25519", &public(A), message, &sig));
    assert!(!verifies_with("rsa", &public(A), message, &sig), "an unknown algorithm is not Ed25519 by default");
    assert!(!verifies_with("", &public(A), message, &sig));
    assert!(fingerprint_of("rsa", &public(A)).is_err());
    assert!(fingerprint_of("ed25519", &public(A)).is_ok());
}

#[test]
fn a_key_that_names_another_algorithm_than_its_own_founds_nothing() {
    let phone = PhoneKey::new();
    let mut c = phone_charter(&phone, canonical);
    c.admins[0].alg = Alg::Ed25519;
    let anchor = c.anchor();
    c.admins[0].acceptance = phone.sign(&c.admins[0].acceptance_message(&anchor, ""));
    let err = founding(&c.found_act()).unwrap_err().to_string();
    assert!(err.contains("and says which"), "a 130 digit key is not an Ed25519 key, and the charter says so before any signature is read: {err}");
    let mut body = phone_charter(&phone, canonical).body();
    body["admins"][0]["alg"] = json!("rsa");
    assert!(serde_json::from_value::<Charter>(body.clone()).is_err(), "an algorithm this build does not know is not read");
    body["admins"][0].as_object_mut().unwrap().remove("alg");
    assert!(serde_json::from_value::<Charter>(body).is_err(), "an entry that does not say is not read");
}

#[test]
fn a_public_key_is_an_ed25519_key_or_a_p256_point_and_nothing_else() {
    let phone = PhoneKey::new();
    assert_eq!(Alg::of_public(&phone.public), Some(Alg::P256));
    assert_eq!(Alg::of_public(&public(A)), Some(Alg::Ed25519));
    let upper = phone.public.to_uppercase();
    for bad in [&upper, &phone.public[2..].to_string(), &format!("05{}", &phone.public[2..]), &"ab".repeat(33), &String::new()] {
        assert_eq!(Alg::of_public(bad), None, "{bad}");
    }
    assert_eq!(unhex(&phone.public).unwrap().len(), 65);
    assert_eq!(hex(&unhex(&phone.public).unwrap()), phone.public);
}

// -- F9: a later key's acceptance is for the charter it follows and the algorithm it names -----------------------------------------

#[test]
fn a_later_keys_acceptance_names_the_charter_it_follows() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let prev = g.context().1;
    let without = entry_on(strong(B, "ben"), 2, &g.id, "");
    let act = make(&g, "admin_add", json!({"entry": without}), &[A]);
    take(&mut g, act.clone());
    advance(&mut g, 25 * HOUR);
    assert!(!admins(&g).contains(&public(B)), "an acceptance that names no charter lists nothing");
    assert!(matches!(g.status(&act.id()), ActStatus::Void(w) if w.contains("did not accept")));
    let elsewhere = entry_on(strong(B, "ben"), 2, &g.id, &"cd".repeat(32));
    let act = make(&g, "admin_add", json!({"entry": elsewhere}), &[A]);
    take(&mut g, act.clone());
    advance(&mut g, 25 * HOUR);
    assert!(!admins(&g).contains(&public(B)), "nor does one made for another charter's follower");
    let right = entry_on(strong(B, "ben"), 2, &g.id, &prev);
    put(&mut g, "admin_add", json!({"entry": right}), &[A]);
    advance(&mut g, 25 * HOUR);
    assert!(admins(&g).contains(&public(B)), "the acceptance of the charter the add follows lists the key");
}

#[test]
fn an_acceptance_signed_for_another_algorithm_than_the_entry_names_lists_nothing() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let phone = PhoneKey::new();
    let (anchor, prev) = (g.id.clone(), g.context().1);
    let mut as_listed = phone_entry(&phone, "ben", 2);
    let mut signed_as_other = as_listed.clone();
    signed_as_other.alg = Alg::Ed25519;
    as_listed.acceptance = phone.sign(&signed_as_other.acceptance_message(&anchor, &prev));
    let act = make(&g, "admin_add", json!({"entry": as_listed}), &[A]);
    take(&mut g, act.clone());
    advance(&mut g, 25 * HOUR);
    assert!(!admins(&g).contains(&phone.public), "the algorithm is part of what the key accepted");
    let mut honest = phone_entry(&phone, "ben", 2);
    honest.acceptance = phone.sign(&honest.acceptance_message(&anchor, &prev));
    put(&mut g, "admin_add", json!({"entry": honest}), &[A]);
    advance(&mut g, 25 * HOUR);
    assert!(admins(&g).contains(&phone.public), "a phone key listed by an Ed25519 admin counts once it has accepted");
}

#[test]
fn an_acceptance_holds_only_for_the_pool_and_the_charter_it_names() {
    let (id, other_id) = ("ab".repeat(16), "cd".repeat(16));
    let (prev, other_prev) = ("12".repeat(32), "34".repeat(32));
    let key = entry_on(strong(B, "ben"), 3, &id, &prev);
    assert!(key.accepted(&id, &prev));
    assert!(!key.accepted(&id, &other_prev), "the same pool and epoch after another charter is not the charter it accepted");
    assert!(!key.accepted(&id, ""), "an acceptance that names a charter is not one that names none");
    assert!(!key.accepted(&other_id, &prev), "nor does it carry to another pool");
    let mut moved = key.clone();
    moved.since = 4;
    assert!(!moved.accepted(&id, &prev), "nor to another epoch");
}

// -- the version ---------------------------------------------------------------------------------------------------------------

#[test]
fn a_charter_of_another_version_founds_nothing_and_says_so_in_one_sentence() {
    let mut c = charter(&[strong(A, "ann")], Policy::Secure, Unit::Person);
    c.v = 1;
    let err = c.check_founding().unwrap_err().to_string();
    assert!(err.contains("another version of Poolhouse") && err.contains("make a new pool"), "{err}");
    let a = device();
    let before = a.pool();
    let reply = a.call("pool_found", "", &a.token, json!({"charter": c.body()}));
    assert_eq!(reply["ok"], false, "{reply}");
    assert!(reply["error"]["say"].as_str().unwrap().contains("another version of Poolhouse"), "{reply}");
    assert_eq!(a.pool(), before, "it is still in no pool");
}

#[test]
fn a_pool_of_another_charter_version_on_disk_is_set_aside_and_the_device_starts_unfounded() {
    let dir = tempfile::tempdir().unwrap();
    let c = charter(&[strong(A, "ann")], Policy::Secure, Unit::Person);
    let id = c.pool_id();
    GovPool::create(dir.path(), found_act(&c)).unwrap();
    let held = dir.path().join("pools").join(&id);
    assert!(Pool::load(dir.path()).unwrap().gov().is_some(), "a pool of this version loads");
    let mut old: Value = serde_json::from_slice(&std::fs::read(held.join("charter.json")).unwrap()).unwrap();
    old["body"]["v"] = json!(1);
    std::fs::write(held.join("charter.json"), serde_json::to_vec(&old).unwrap()).unwrap();
    let pool = Pool::load(dir.path()).unwrap();
    assert!(pool.is_unfounded(), "the device starts as it would fresh");
    assert_eq!(pool.retired, format!("pools-old-format/{id}"));
    assert!(!held.exists(), "the old pool is out of the way");
    assert!(dir.path().join("pools-old-format").join(&id).join("charter.json").is_file(), "and kept, never deleted");
    assert!(Pool::load(dir.path()).unwrap().is_unfounded(), "a second start finds nothing to read");
}

// -- the founding path of a node -------------------------------------------------------------------------------------------------

/// Enrol the P-256 key of ``phone`` on the node of ``dev`` the way a phone's own enrolment does, which is what makes a key strong there.
fn enrol_phone(dev: &Dev, phone: &PhoneKey) {
    dev.node.lock().unwrap().persons.add_phone_key("p256", &phone.public, "ann's phone", wall_ms()).unwrap();
}

#[test]
fn a_node_drafts_a_charter_for_a_phone_key_and_founds_the_pool_of_what_the_phone_signed() {
    let a = device();
    let phone = PhoneKey::new();
    enrol_phone(&a, &phone);
    let draft = a.ok("pool_draft_found", "", &a.token, json!({"title": "home", "policy": "secure", "project": "", "keys": [{"key": phone.public, "person": "ann", "label": "ann's phone"}]}));
    assert_eq!(draft["charter"]["admins"][0]["alg"], "p256", "{draft}");
    assert_eq!(draft["charter"]["admins"][0]["strength"], "strong", "the node derives it from the key the phone enrolled: {draft}");
    assert_eq!(draft["accept"][0]["alg"], "p256", "{draft}");
    let mut charter = draft["charter"].clone();
    let bytes = unhex(draft["accept"][0]["sign_bytes"].as_str().unwrap()).unwrap();
    charter["admins"][0]["acceptance"] = json!(phone.sign(&bytes));
    let founded = a.ok("pool_found", "", &a.token, json!({"charter": charter}));
    assert_eq!(founded["pool"], draft["anchor"].as_str().unwrap()[..32], "the pool is the first half of the anchor the phone was shown");
    assert_eq!(a.status()["governance"]["admins"][0]["fingerprint"], fingerprint_of("p256", &phone.public).unwrap());
}

#[test]
fn a_draft_for_a_key_that_is_no_key_is_refused_and_a_high_s_acceptance_founds_nothing() {
    let a = device();
    let bad = a.call("pool_draft_found", "", &a.token, json!({"title": "home", "policy": "secure", "project": "", "keys": [{"key": "ab".repeat(33), "person": "ann", "label": ""}]}));
    assert_eq!(bad["ok"], false, "{bad}");
    assert!(bad["error"]["message"].as_str().unwrap().contains("is not a public key"), "{bad}");
    let phone = PhoneKey::new();
    enrol_phone(&a, &phone);
    let draft = a.ok("pool_draft_found", "", &a.token, json!({"title": "home", "policy": "secure", "project": "", "keys": [{"key": phone.public, "person": "ann", "label": "ann's phone"}]}));
    let mut charter = draft["charter"].clone();
    charter["admins"][0]["acceptance"] = json!(phone.sign_high(&unhex(draft["accept"][0]["sign_bytes"].as_str().unwrap()).unwrap()));
    let before = a.pool();
    let refused = a.call("pool_found", "", &a.token, json!({"charter": charter}));
    assert_eq!(refused["ok"], false, "{refused}");
    assert!(refused["error"]["say"].as_str().unwrap().contains("accept"), "it is the acceptance that is refused: {refused}");
    assert_eq!(a.pool(), before, "it is still in no pool");
}
