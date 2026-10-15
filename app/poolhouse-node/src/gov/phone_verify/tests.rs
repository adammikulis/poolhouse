use super::*;
use crate::gov::act::{ActSig, SigKind};
use crate::gov::charter::{Charter, First};
use crate::gov::keys::{AdminKey, Alg, Role, Strength};
use crate::gov::signer::RigSigner;
use crate::membership::Policy;
use serde_json::json;

const NOW: u64 = 1_700_000_000_000;

fn setup() -> (tempfile::TempDir, Charter, Binding) {
    let root = tempfile::tempdir().unwrap();
    let certificate = tempfile::tempdir().unwrap();
    let identity = crate::cert::load_or_create(certificate.path(), &ed25519_dalek::SigningKey::from_bytes(&[91; 32])).unwrap();
    let mut admin = AdminKey { key: RigSigner::public(1), alg: Alg::Ed25519, role: Role::Admin, strength: Strength::Strong,
        person: "person-a".into(), since: 1, acceptance: String::new(), label: "admin key".into() };
    let mut charter = Charter::new("test pool", Policy::Secure, "", vec![admin.clone()], First::of(&identity.der, "first computer"), NOW);
    admin.acceptance = RigSigner::sign_as(1, &admin.acceptance_message(&charter.anchor(), ""));
    charter.admins = vec![admin];
    let binding = Binding { anchor: charter.anchor(), node: identity.fingerprint(), tls: "b".repeat(64), public: RigSigner::public(1) };
    Engine::pin(root.path(), binding.clone()).unwrap();
    (root, charter, binding)
}

fn request(engine: &Engine) -> Request {
    let pool = engine.pool.as_ref().unwrap();
    Request { id: "a".repeat(32), nonce: "b".repeat(32), created_ms: NOW, expires_ms: NOW + 60_000,
        act: pool.new_act("delegate_end", json!({"fingerprint": "d".repeat(64)}), NOW).unwrap() }
}

fn bootstrap(engine: &mut Engine, charter: &Charter) {
    let act = Act::new(&charter.pool_id(), 1, "delegate_end", json!({"fingerprint": "d".repeat(64)}), NOW, &charter.hash()).unwrap();
    let request = Request { id: "a".repeat(32), nonce: "b".repeat(32), created_ms: NOW, expires_ms: NOW + 60_000, act };
    let verified = engine.review(Proof { found: charter.found_act(), acts: vec![] }, request, NOW).unwrap();
    assert!(verified.card().contains("Phone-verified pool terms"));
}

fn sign(mut act: Act) -> Act {
    act.add_signature(ActSig { kind: SigKind::Admin, key: RigSigner::public(1), sig: RigSigner::sign_as(1, &act.sign_bytes()) });
    act
}

#[test]
fn checkpoint_refuses_unpinned_corrupt_missing_and_changed_bindings() {
    let (root, charter, binding) = setup();
    let mut engine = Engine::open(root.path(), &binding).unwrap();
    bootstrap(&mut engine, &charter);
    let mut wrong = binding.clone();
    wrong.tls = "c".repeat(64);
    assert!(Engine::open(root.path(), &wrong).is_err());
    let acts = root.path().join("pools").join(charter.pool_id()).join("acts.json");
    std::fs::remove_file(acts).unwrap();
    assert!(Engine::open(root.path(), &binding).is_err());
    assert!(engine.recheck(&request::verify(engine.pool.as_ref().unwrap(), &binding, request(&engine), NOW).unwrap(), NOW).is_err());
}

#[test]
fn local_request_rejects_old_context_expiry_and_repeated_local_signature() {
    let (root, charter, binding) = setup();
    let mut engine = Engine::open(root.path(), &binding).unwrap();
    bootstrap(&mut engine, &charter);
    let mut stale = request(&engine);
    stale.act.prev = "e".repeat(64);
    assert!(engine.review(Proof { found: charter.found_act(), acts: vec![] }, stale, NOW).is_err());
    let expired = request(&engine);
    assert!(engine.review(Proof { found: charter.found_act(), acts: vec![] }, expired, NOW + 60_000).is_err());
    let mut repeated = request(&engine);
    repeated.act = sign(repeated.act);
    assert!(engine.review(Proof { found: charter.found_act(), acts: vec![] }, repeated, NOW).is_err());
}

#[test]
fn a_valid_late_competing_act_survives_omission_and_reopen() {
    let (root, charter, binding) = setup();
    let mut engine = Engine::open(root.path(), &binding).unwrap();
    bootstrap(&mut engine, &charter);
    let pool = engine.pool.as_ref().unwrap();
    let first = sign(pool.new_act("set_holds", json!({"admin_add_h": 25}), NOW).unwrap());
    let competing = sign(pool.new_act("set_holds", json!({"admin_add_h": 26}), NOW).unwrap());
    let stale = request(&engine);
    assert!(engine.review(Proof { found: charter.found_act(), acts: vec![first] }, stale, NOW).is_err());
    assert_eq!(engine.pool.as_ref().unwrap().context().0, 2);
    engine.pool.as_ref().unwrap().clock.advance_ms(1);
    let current = request(&engine);
    engine.review(Proof { found: charter.found_act(), acts: vec![competing.clone()] }, current, NOW).unwrap();
    assert!(engine.pool.as_ref().unwrap().acts().contains_key(&competing.id()));
    let mut reopened = Engine::open(root.path(), &binding).unwrap();
    let current = request(&reopened);
    reopened.review(Proof { found: charter.found_act(), acts: vec![] }, current, NOW).unwrap();
    let pool = reopened.pool.as_ref().unwrap();
    assert!(pool.acts().contains_key(&competing.id()));
    assert!(pool.state().chain.void[&competing.id()].contains("arrived after"));
}

#[test]
fn invalid_late_junk_does_not_poison_the_next_valid_request() {
    let (root, charter, binding) = setup();
    let mut engine = Engine::open(root.path(), &binding).unwrap();
    bootstrap(&mut engine, &charter);
    let pool = engine.pool.as_ref().unwrap();
    let first = sign(pool.new_act("set_holds", json!({"admin_add_h": 25}), NOW).unwrap());
    let mut junk = sign(pool.new_act("set_holds", json!({"admin_add_h": 26}), NOW).unwrap());
    junk.signatures[0].sig = "00".repeat(64);
    let stale = request(&engine);
    assert!(engine.review(Proof { found: charter.found_act(), acts: vec![first] }, stale, NOW).is_err());
    let current = request(&engine);
    assert!(engine.review(Proof { found: charter.found_act(), acts: vec![junk.clone()] }, current, NOW).is_err());
    assert!(!engine.pool.as_ref().unwrap().acts().contains_key(&junk.id()));
    let current = request(&engine);
    engine.review(Proof { found: charter.found_act(), acts: vec![] }, current, NOW).unwrap();
}

#[test]
fn wall_time_and_reopen_do_not_finish_an_unobserved_hold() {
    let (root, charter, binding) = setup();
    let mut engine = Engine::open(root.path(), &binding).unwrap();
    bootstrap(&mut engine, &charter);
    let weaken = sign(engine.pool.as_ref().unwrap().new_act("set_holds", json!({"admin_add_h": 23}), NOW).unwrap());
    let current = request(&engine);
    engine.review(Proof { found: charter.found_act(), acts: vec![weaken] }, current, NOW).unwrap();
    assert_eq!(engine.pool.as_ref().unwrap().context().0, 1);
    drop(engine);
    let reopened = Engine::open(root.path(), &binding).unwrap();
    assert_eq!(reopened.pool.as_ref().unwrap().context().0, 1);
    assert!(reopened.pool.as_ref().unwrap().state().chain.pending.is_some());
}
