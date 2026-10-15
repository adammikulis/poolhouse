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
    assert!(verified.card().contains("Current pool terms"));
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

fn test_clock(count: u64, time: &std::sync::Arc<std::sync::atomic::AtomicU64>) -> clock::LocalClock {
    let time = time.clone();
    clock::LocalClock::test(count, move || Ok(time.load(std::sync::atomic::Ordering::SeqCst)))
}

#[test]
fn same_boot_reopen_earns_awake_once_and_never_credits_an_unseen_hold() {
    use std::sync::{Arc, atomic::{AtomicU64, Ordering}};
    let (root, charter, binding) = setup();
    let time = Arc::new(AtomicU64::new(1000));
    let mut engine = Engine::open_clock(root.path(), &binding, Some(test_clock(5, &time))).unwrap();
    bootstrap(&mut engine, &charter);
    let saved: serde_json::Value = serde_json::from_slice(&std::fs::read(engine.pool.as_ref().unwrap().dir().join("seen.json")).unwrap()).unwrap();
    let saved = saved["awake_ms"].as_i64().unwrap();
    drop(engine);
    time.store(24 * 3_600_000 + 1000, Ordering::SeqCst);
    let mut engine = Engine::open_clock(root.path(), &binding, Some(test_clock(5, &time))).unwrap();
    let earned = engine.pool.as_ref().unwrap().clock.now_ms();
    assert!(earned >= saved + 24 * 3_600_000);
    let unseen = sign(engine.pool.as_ref().unwrap().new_act("set_holds", json!({"admin_add_h": 23}), NOW).unwrap());
    let current = request(&engine);
    engine.review(Proof { found: charter.found_act(), acts: vec![unseen] }, current, NOW).unwrap();
    assert_eq!(engine.pool.as_ref().unwrap().context().0, 1);
    let before = engine.pool.as_ref().unwrap().clock.now_ms();
    drop(engine);
    for _ in 0..3 {
        let mut reopened = Engine::open_clock(root.path(), &binding, Some(test_clock(5, &time))).unwrap();
        let now = reopened.pool.as_ref().unwrap().clock.now_ms();
        assert!(now >= saved + 24 * 3_600_000 && now < before + 24 * 3_600_000);
        let current = request(&reopened);
        let verified = reopened.review(Proof { found: charter.found_act(), acts: vec![] }, current, NOW).unwrap();
        reopened.recheck(&verified, NOW).unwrap();
    }
}

#[test]
fn reboot_earns_no_gap_and_clock_rollback_failure_and_overflow_refuse() {
    use std::sync::{Arc, atomic::{AtomicU64, Ordering}};
    let (root, charter, binding) = setup();
    let time = Arc::new(AtomicU64::new(1000));
    let mut engine = Engine::open_clock(root.path(), &binding, Some(test_clock(5, &time))).unwrap();
    bootstrap(&mut engine, &charter);
    let before = engine.pool.as_ref().unwrap().clock.now_ms();
    drop(engine);
    time.store(100, Ordering::SeqCst);
    assert!(Engine::open_clock(root.path(), &binding, Some(test_clock(5, &time))).is_err());
    assert!(Engine::open_clock(root.path(), &binding, Some(test_clock(4, &time))).is_err());
    let after_boot = Engine::open_clock(root.path(), &binding, Some(test_clock(6, &time))).unwrap();
    assert!(after_boot.pool.as_ref().unwrap().clock.now_ms() < before + 1000);
    assert!(Engine::open_clock(root.path(), &binding, Some(clock::LocalClock::test(5, || bad("clock failed")))).is_err());
    time.store(u64::MAX, Ordering::SeqCst);
    assert!(Engine::open_clock(root.path(), &binding, Some(test_clock(5, &time))).is_err());
}

#[test]
fn semantic_cards_show_every_changed_field_without_transport_prose_or_json() {
    let (root, charter, binding) = setup();
    let mut engine = Engine::open(root.path(), &binding).unwrap();
    bootstrap(&mut engine, &charter);
    let changes = [
        ("admit", json!({"cert": charter.first.cert, "name": "new device", "how": "tap", "via": ""}), vec!["new device", "Joining method: tap", &binding.node]),
        ("remove", json!({"fingerprint": "d".repeat(64), "cut": [{"board":"project", "origin":"a".repeat(32), "seq":3,"hash":"b".repeat(64)}]}), vec!["through entry 3", "\"project\"", "bbbbbbbb"]),
        ("set_threshold", json!({"routine":1,"sensitive":0,"unit":"key"}), vec!["Routine approvals: 1", "Sensitive approvals: automatic", "by: key"]),
        ("set_holds", json!({"admin_add_h":25,"admin_remove_h":26,"handoff_min":15,"dispute_d":7}), vec!["Add admin: 25 hours","Remove admin: 26 hours","Handoff: 15 minutes","Dispute: 7 days"]),
        ("set_mode", json!({"mode":"admins"}), vec!["Governed by: admins"]),
        ("delegate", json!({"fingerprint":"d".repeat(64)}), vec!["30 days", "dddddddd"]),
        ("delegate_end", json!({"fingerprint":"d".repeat(64)}), vec!["End permission", "dddddddd"]),
        ("admin_remove", json!({"key":binding.public,"cut":["e".repeat(64)]}), vec!["Retain these signed changes", "eeeeeeee"]),
    ];
    for (kind, body, expected) in changes {
        let act = engine.pool.as_ref().unwrap().new_act(kind, body, NOW).unwrap();
        let bytes = act.sign_bytes();
        let card = card::render(&act, engine.pool.as_ref().unwrap()).unwrap();
        assert!(!card.contains('{') && !card.contains("\"body\""));
        assert!(card.contains("Recovery delay: 7 days") && card.contains("Count approvals by: person"));
        for text in expected { assert!(card.contains(text), "missing {text} in {card}"); }
        assert_eq!(bytes, act.sign_bytes());
    }
    let mut added = charter.admins[0].clone();
    added.key = RigSigner::public(2);
    added.since = 2;
    added.label = "second admin\u{202e}\u{2066}\\u202e".into();
    added.person = "person-b".into();
    added.acceptance = RigSigner::sign_as(2, &added.acceptance_message(&charter.pool_id(), &charter.hash()));
    let pool = engine.pool.as_ref().unwrap();
    let add = pool.new_act("admin_add", json!({"entry":added}), NOW).unwrap();
    let text = card::render(&add, pool).unwrap();
    assert!(text.contains("second admin") && text.contains("person-b") && text.contains("listed since epoch 2"));
    assert!(text.contains(&crate::gov::keys::fingerprint_of(&RigSigner::public(2))));
    assert!(!text.contains('\u{202e}') && !text.contains('\u{2066}'));
}

#[test]
fn cancellation_cards_require_and_show_the_verified_target() {
    let (root, charter, binding) = setup();
    let mut engine = Engine::open(root.path(), &binding).unwrap();
    bootstrap(&mut engine, &charter);
    let pool = engine.pool.as_mut().unwrap();
    let target = sign(pool.new_act("remove", json!({"fingerprint":"d".repeat(64)}), NOW).unwrap());
    assert_eq!(pool.ingest(target.clone(), NOW), Ingest::Accepted);
    for kind in ["cancel", "veto"] {
        let act = pool.new_act(kind, json!({"act":target.id()}), NOW).unwrap();
        let card = card::render(&act, pool).unwrap();
        assert!(card.contains(&target.id()) && card.contains("Remove") && card.contains("dddddddd"));
        let missing = pool.new_act(kind, json!({"act":"e".repeat(64)}), NOW).unwrap();
        assert!(card::render(&missing, pool).is_err());
    }
}

#[test]
fn proposals_reuse_current_policy_before_returning_signing_bytes() {
    let (root, charter, binding) = setup();
    let mut engine = Engine::open(root.path(), &binding).unwrap();
    bootstrap(&mut engine, &charter);
    for (kind, body) in [("set_mode", json!({"mode":"members"})), ("set_threshold", json!({"routine":9})),
        ("admin_remove", json!({"key":binding.public}))] {
        let mut current = request(&engine);
        current.act = engine.pool.as_ref().unwrap().new_act(kind, body, NOW).unwrap();
        assert!(engine.review(Proof { found:charter.found_act(), acts:vec![] }, current, NOW).is_err());
    }
    let mut added = charter.admins[0].clone();
    added.key = RigSigner::public(2);
    added.person = "person-b".into();
    added.since = 2;
    added.acceptance = RigSigner::sign_as(2, &added.acceptance_message(&charter.pool_id(), &charter.hash()));
    let mut current = request(&engine);
    current.act = engine.pool.as_ref().unwrap().new_act("admin_add", json!({"entry":added}), NOW).unwrap();
    let refusal = engine.review(Proof { found:charter.found_act(), acts:vec![] }, current, NOW).err().unwrap().to_string();
    assert!(refusal.contains("needs verified proof"));
}

#[test]
fn a_verified_dispute_allows_only_cancelling_its_actual_contested_changes() {
    let (root, charter, binding) = setup();
    let mut engine = Engine::open(root.path(), &binding).unwrap();
    bootstrap(&mut engine, &charter);
    let pool = engine.pool.as_ref().unwrap();
    let first = sign(pool.new_act("set_holds", json!({"admin_add_h":25}), NOW).unwrap());
    let second = sign(pool.new_act("set_holds", json!({"admin_remove_h":25}), NOW).unwrap());
    let unrelated = request(&engine);
    assert!(engine.review(Proof { found:charter.found_act(), acts:vec![first.clone(), second] }, unrelated, NOW).is_err());
    assert!(engine.pool.as_ref().unwrap().state().chain.dispute.is_some());
    let mut cancel = request(&engine);
    cancel.act = engine.pool.as_ref().unwrap().new_act("cancel", json!({"act":first.id()}), NOW).unwrap();
    let verified = engine.review(Proof { found:charter.found_act(), acts:vec![] }, cancel, NOW).unwrap();
    engine.recheck(&verified, NOW).unwrap();
    let mut unrelated = request(&engine);
    unrelated.act = engine.pool.as_ref().unwrap().new_act("cancel", json!({"act":"e".repeat(64)}), NOW).unwrap();
    assert!(engine.review(Proof { found:charter.found_act(), acts:vec![] }, unrelated, NOW).is_err());
    let unrelated = request(&engine);
    assert!(engine.review(Proof { found:charter.found_act(), acts:vec![] }, unrelated, NOW).is_err());
}

#[test]
fn only_the_exact_removed_key_can_veto_its_pending_removal() {
    let (_initial, mut charter, base) = setup();
    let mut second = charter.admins[0].clone();
    second.key = RigSigner::public(2);
    second.person = "person-b".into();
    charter.admins.push(second);
    charter.admins.sort_by_key(|entry| entry.key.clone());
    charter.thresholds.sensitive = 1;
    let anchor = charter.anchor();
    for entry in &mut charter.admins {
        let seed = if entry.key == RigSigner::public(1) { 1 } else { 2 };
        entry.acceptance = RigSigner::sign_as(seed, &entry.acceptance_message(&anchor, ""));
    }
    for seed in [1, 2] {
        let root = tempfile::tempdir().unwrap();
        let binding = Binding { anchor:anchor.clone(), public:RigSigner::public(seed), ..base.clone() };
        Engine::pin(root.path(), binding.clone()).unwrap();
        let mut engine = Engine::open(root.path(), &binding).unwrap();
        bootstrap(&mut engine, &charter);
        let target = sign(engine.pool.as_ref().unwrap().new_act("admin_remove", json!({"key":RigSigner::public(2)}), NOW).unwrap());
        let current = request(&engine);
        engine.review(Proof { found:charter.found_act(), acts:vec![target.clone()] }, current, NOW).unwrap();
        let mut veto = request(&engine);
        veto.act = engine.pool.as_ref().unwrap().new_act("veto", json!({"act":target.id()}), NOW).unwrap();
        let result = engine.review(Proof { found:charter.found_act(), acts:vec![] }, veto, NOW);
        if seed == 1 { assert!(result.err().unwrap().to_string().contains("Only the strong key named")); }
        else { assert!(result.is_ok()); }
    }
}
