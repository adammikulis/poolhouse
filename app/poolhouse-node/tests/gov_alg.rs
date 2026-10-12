//! The algorithm of an admin key (Ed25519, or P-256 ECDSA for a phone): a pool governed by a P-256 key, a pool of both, and what a forged,
//! wrong-algorithm, replayed or malformed signature, key or acceptance does.

mod kit;

use kit::gov::*;
use kit::govnode::*;
use kit::netkit::device;
use poolhouse_node::fsutil::hex;
use poolhouse_node::gov::act::{Act, ActSig, SigKind};
use poolhouse_node::gov::alg::Alg;
use poolhouse_node::gov::charter::Unit;
use poolhouse_node::gov::pool::{GovPool, Ingest};
use poolhouse_node::gov::signer::RigSigner;
use poolhouse_node::membership::Policy;
use serde_json::json;

const ANN: u8 = 1;
const PHONE: u8 = 200;
const OTHER_PHONE: u8 = 201;

fn refused(i: Ingest) -> String {
    match i {
        Ingest::Refused(why) => why,
        other => panic!("expected a refusal, got {other:?}"),
    }
}

fn unsigned_admit(g: &GovPool, device: &poolhouse_node::cert::Identity) -> Act {
    admit(g, device, &[])
}

fn with_sig(mut act: Act, key: u8, alg: Alg, signed_by: u8, bytes: &[u8]) -> Act {
    act.add_signature(ActSig { kind: SigKind::Admin, key: public(key), alg, sig: RigSigner::sign_as(signed_by, bytes) });
    act
}

#[test]
fn a_pool_governed_by_a_p256_key_admits_a_device_that_key_signs() {
    let (_d, mut g) = pool(&[strong(PHONE, "owner")], Policy::Secure);
    assert_eq!(RigSigner::alg(PHONE), Alg::P256Ecdsa);
    assert_eq!(public(PHONE).len(), 130, "a P-256 key is an uncompressed point");
    let device = ident(7);
    assert!(matches!(put_admit(&mut g, &device, &[PHONE]), Ingest::Accepted));
    assert!(is_member(&g, &device), "the P-256 key counted as a strong admin");
}

#[test]
fn a_pool_of_an_ed25519_key_and_a_p256_key_takes_acts_signed_by_either_and_counts_two_people() {
    let (_d, mut g) = pool(&[strong(ANN, "ann"), strong(PHONE, "owner")], Policy::Secure);
    assert_eq!(g.state().chain.latest().charter.people(), 2);
    let (one, two) = (ident(7), ident(8));
    assert!(matches!(put_admit(&mut g, &one, &[ANN]), Ingest::Accepted));
    assert!(matches!(put_admit(&mut g, &two, &[PHONE]), Ingest::Accepted));
    let removal = put(&mut g, "remove", json!({"fingerprint": one.fingerprint()}), &[PHONE]);
    assert!(matches!(removal, Ingest::Accepted));
    assert!(is_removed(&g, &one), "a P-256 admin removes a member");
    assert!(is_member(&g, &two));
}

#[test]
fn a_signature_by_another_p256_key_under_the_listed_keys_name_is_refused() {
    let (_d, mut g) = pool(&[strong(PHONE, "owner")], Policy::Secure);
    let device = ident(7);
    let act = unsigned_admit(&g, &device);
    let forged = with_sig(act.clone(), PHONE, Alg::P256Ecdsa, OTHER_PHONE, &act.sign_bytes());
    assert_eq!(refused(take(&mut g, forged)), "a signature does not verify");
    assert!(!is_member(&g, &device));
}

#[test]
fn a_p256_signature_over_another_act_is_refused_so_it_cannot_be_replayed() {
    let (_d, mut g) = pool(&[strong(PHONE, "owner")], Policy::Secure);
    let (one, two) = (ident(7), ident(8));
    let (first, second) = (admit(&g, &one, &[PHONE]), unsigned_admit(&g, &two));
    let replay = with_sig(second.clone(), PHONE, Alg::P256Ecdsa, PHONE, &first.sign_bytes());
    assert_eq!(refused(take(&mut g, replay)), "a signature does not verify");
    assert!(matches!(take(&mut g, first), Ingest::Accepted), "the act it was made for is still taken");
}

#[test]
fn a_signature_that_names_the_other_algorithm_is_refused_in_both_directions() {
    let (_d, mut g) = pool(&[strong(ANN, "ann"), strong(PHONE, "owner")], Policy::Secure);
    let device = ident(7);
    let act = unsigned_admit(&g, &device);
    let as_ed = with_sig(act.clone(), PHONE, Alg::Ed25519, PHONE, &act.sign_bytes());
    assert!(refused(take(&mut g, as_ed)).contains("algorithm"), "a P-256 key is never read as Ed25519");
    let as_p256 = with_sig(act.clone(), ANN, Alg::P256Ecdsa, ANN, &act.sign_bytes());
    assert!(refused(take(&mut g, as_p256)).contains("algorithm"), "an Ed25519 key is never read as P-256");
    let well_formed_as_ed = with_sig(act.clone(), PHONE, Alg::Ed25519, ANN, &act.sign_bytes());
    assert!(refused(take(&mut g, well_formed_as_ed)).contains("algorithm"), "a signature of the right shape for the algorithm named is still not for a key of the other");
    let well_formed_as_p256 = with_sig(act.clone(), ANN, Alg::P256Ecdsa, PHONE, &act.sign_bytes());
    assert!(refused(take(&mut g, well_formed_as_p256)).contains("algorithm"));
    assert!(!is_member(&g, &device));
}

#[test]
fn a_device_signature_is_ed25519_only() {
    let (_d, mut g) = pool(&[strong(ANN, "ann")], Policy::Open);
    let mut act = unsigned_admit(&g, &ident(7));
    act.add_signature(ActSig { kind: SigKind::Device, key: ident(5).fingerprint(), alg: Alg::P256Ecdsa, sig: RigSigner::sign_as(PHONE, &act.sign_bytes()) });
    assert!(refused(take(&mut g, act)).contains("algorithm"), "a device's key is its certificate's, which is Ed25519");
}

#[test]
fn a_charter_whose_key_names_the_other_algorithm_founds_nothing() {
    for (seed, wrong) in [(PHONE, Alg::Ed25519), (ANN, Alg::P256Ecdsa)] {
        let mut c = charter(&[strong(seed, "owner")], Policy::Secure, Unit::Person);
        c.admins[0].alg = wrong;
        let dir = tempfile::tempdir().unwrap();
        assert!(GovPool::create(dir.path(), found_act(&c)).is_err(), "a key is listed under the algorithm its encoding says");
    }
}

#[test]
fn an_acceptance_made_by_another_key_or_shaped_for_the_other_algorithm_founds_nothing() {
    let mut other = charter(&[strong(PHONE, "owner")], Policy::Secure, Unit::Person);
    other.admins[0].acceptance = RigSigner::sign_as(OTHER_PHONE, &other.admins[0].acceptance_message(&other.anchor()));
    let mut shaped = charter(&[strong(PHONE, "owner")], Policy::Secure, Unit::Person);
    shaped.admins[0].acceptance = "ab".repeat(64);
    for c in [other, shaped] {
        let dir = tempfile::tempdir().unwrap();
        assert!(GovPool::create(dir.path(), found_act(&c)).is_err());
    }
}

#[test]
fn the_algorithm_is_inside_what_a_key_accepts() {
    let c = charter(&[strong(PHONE, "owner")], Policy::Secure, Unit::Person);
    let mut renamed = c.admins[0].clone();
    renamed.alg = Alg::Ed25519;
    assert_ne!(c.admins[0].acceptance_message(&c.anchor()), renamed.acceptance_message(&c.anchor()));
    assert!(c.admins[0].acceptance_message(&c.anchor()).windows(10).any(|w| w == b"p256-ecdsa"), "the acceptance names the algorithm");
}

#[test]
fn a_key_off_the_curve_or_in_capitals_or_of_another_length_is_no_key() {
    let good = public(PHONE);
    assert_eq!(Alg::of_key(&good), Some(Alg::P256Ecdsa));
    let mut off = good.clone();
    off.replace_range(128..130, if good.ends_with("00") { "01" } else { "00" });
    assert_eq!(Alg::of_key(&off), None, "a point whose y is changed is not on the curve");
    assert_eq!(Alg::of_key(&good.to_uppercase()), None);
    assert_eq!(Alg::of_key(&good[..128]), None);
    assert_eq!(Alg::of_key(&format!("02{}", &good[2..66])), None, "only the uncompressed form is a key");
    assert_eq!(Alg::of_key(&public(ANN)), Some(Alg::Ed25519));
}

#[test]
fn a_p256_signature_is_a_der_sequence_and_an_ed25519_one_is_64_bytes() {
    let message = b"poolhouse";
    let der = RigSigner::sign_as(PHONE, message);
    assert!(Alg::P256Ecdsa.sig_shape(&der) && Alg::P256Ecdsa.verifies(&public(PHONE), message, &der));
    assert!(!Alg::Ed25519.sig_shape(&der), "a DER signature is not 64 bytes");
    let raw = &der[der.len() - 128..];
    assert!(!Alg::P256Ecdsa.sig_shape(raw), "raw r and s is not the DER form");
    assert!(!Alg::P256Ecdsa.verifies(&public(PHONE), b"another message", &der));
    assert!(!Alg::P256Ecdsa.verifies(&public(OTHER_PHONE), message, &der));
    assert!(!Alg::P256Ecdsa.verifies(&public(ANN), message, &der), "a key of the other algorithm verifies nothing");
    let ed = RigSigner::sign_as(ANN, message);
    assert!(Alg::Ed25519.verifies(&public(ANN), message, &ed) && !Alg::P256Ecdsa.sig_shape(&ed));
    assert!(!Alg::Ed25519.verifies(&public(ANN), message, &ed.to_uppercase()));
}

#[test]
fn a_node_founds_a_pool_for_a_person_whose_strong_key_is_p256_and_the_pool_is_its_admin() {
    let dev = device();
    let signer = std::sync::Arc::new(RigSigner::new().with_key(PHONE, "owner", poolhouse_node::gov::keys::Strength::Strong));
    let id = found(&dev, signer, "secure");
    let status = dev.ok("pool_status", "", &dev.token, json!({}));
    assert_eq!(status["pool"], id);
    let admins = status["governance"]["admins"].as_array().unwrap().clone();
    assert_eq!(admins.len(), 1);
    assert_eq!(admins[0]["strength"], "strong");
    let reply = dev.ok("pool_draft_found", "", &dev.token, json!({"title": "second", "policy": "secure", "project": "", "keys": [
        {"key": public(PHONE), "alg": "p256-ecdsa", "person": "owner", "strength": "strong", "label": "phone"}]}));
    assert_eq!(reply["accept"][0]["alg"], "p256-ecdsa");
    let bad = dev.call("pool_draft_found", "", &dev.token, json!({"title": "second", "policy": "secure", "project": "", "keys": [
        {"key": public(PHONE), "alg": "ed25519", "person": "owner", "strength": "strong", "label": "phone"}]}));
    assert_eq!(bad["ok"], false, "a key that names the other algorithm is not drafted");
}

fn der_of(sig: &str) -> Vec<u8> {
    poolhouse_node::fsutil::unhex(sig).unwrap()
}

#[test]
fn the_high_s_twin_of_a_p256_signature_verifies_for_ring_and_is_refused_here() {
    let message = b"poolhouse";
    let sig = RigSigner::sign_as(PHONE, message);
    let twin = Alg::mirrored_p256(&sig).unwrap();
    assert_ne!(sig, twin);
    assert!(poolhouse_node::grants::person::verifies_p256(&public(PHONE), message, &twin), "the twin is a valid ECDSA signature");
    let (low, high) = if Alg::P256Ecdsa.sig_shape(&sig) { (sig.clone(), twin) } else { (twin, sig) };
    assert!(Alg::P256Ecdsa.verifies(&public(PHONE), message, &low));
    assert!(!Alg::P256Ecdsa.sig_shape(&high) && !Alg::P256Ecdsa.verifies(&public(PHONE), message, &high), "a signature has one spelling");
    assert_eq!(Alg::canonical_p256(&high).unwrap(), low);
    assert_eq!(Alg::mirrored_p256(&high).unwrap(), low);
}

#[test]
fn a_der_signature_with_a_needless_leading_zero_or_trailing_bytes_is_refused() {
    let sig = der_of(&RigSigner::sign_as(PHONE, b"poolhouse"));
    let (rlen, mut padded) = (usize::from(sig[3]), sig.clone());
    padded.splice(4..4, [0u8]);
    padded[3] += 1;
    padded[1] += 1;
    if sig[4] & 0x80 == 0 {
        assert!(!Alg::P256Ecdsa.sig_shape(&hex(&padded)), "a zero that the high bit does not need ({rlen} bytes of r)");
    }
    let mut trailing = sig.clone();
    trailing.push(0);
    trailing[1] += 1;
    assert!(!Alg::P256Ecdsa.sig_shape(&hex(&trailing)));
    assert!(!Alg::P256Ecdsa.sig_shape(&hex(&sig[..sig.len() - 1])));
}

#[test]
fn a_charter_made_of_the_same_keys_has_the_same_pool_id_whatever_the_acceptances_are() {
    let c = charter(&[strong(PHONE, "owner")], Policy::Secure, Unit::Person);
    let mut other = c.clone();
    other.admins[0].acceptance = Alg::mirrored_p256(&c.admins[0].acceptance).unwrap();
    assert_ne!(c.hash(), other.hash(), "the acceptance is part of the charter");
    assert_eq!(c.pool_id(), other.pool_id(), "and of no pool id");
    let dir = tempfile::tempdir().unwrap();
    let twin_is_high_or_low = GovPool::create(dir.path(), found_act(&other)).is_ok();
    assert_eq!(twin_is_high_or_low, Alg::P256Ecdsa.sig_shape(&other.admins[0].acceptance), "only the one spelling founds a pool");
}
