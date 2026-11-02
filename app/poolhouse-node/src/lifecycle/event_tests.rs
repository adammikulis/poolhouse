//! Authenticated-message fixtures exercise state boundaries only, not native provenance.
use super::*;
use sha2::{Digest, Sha256};

#[test]
fn reservation_derives_native_event_payload_and_birth_provenance() {
    let f = Fixture::new(); let source = &f.binding.request.event;
    assert_eq!(source.source.producer, f.producer.body().admission);
    assert_eq!(source.source.installation, f.producer.body().installation);
    assert_eq!(source.source.launch_epoch, f.producer.body().launch_epoch);
    assert_eq!(source.source.actor, "root"); assert_eq!(source.source.stream_event_id, [100; 32]);
    assert_eq!(source.session, f.owner.key); assert_eq!(source.admission.origin, Origin::Birth);
    assert_eq!(source.admission.source.stream_event_id, [1; 32]);
    assert_eq!(source.kind, "completion"); assert_eq!(f.binding.request.purpose, "record-result");
    let digest: [u8; 32] = Sha256::digest(b"native-event-payload").into();
    assert_eq!(f.binding.request.digest, digest);
    assert_eq!(f.binding.request.spawn_plan, b"immutable-plan");
}

#[test]
fn exact_repeated_event_never_issues_new_rights_in_any_disposition() {
    for state in 0..3 {
        let f = Fixture::new();
        if state == 1 { f.ready(); assert!(matches!(f.consume(), Ok(Consume::Execute(_)))); }
        if state == 2 { f.registry.cancel(&f.owner, &f.right).unwrap(); }
        let before = f.registry.lock().unwrap().next;
        assert!(matches!(f.registry.reserve(&f.owner, invocation(&f.registry, &f.owner, 100)), Err(Error::AlreadyReserved)));
        let store = f.registry.lock().unwrap(); assert_eq!(store.next, before); assert_eq!(store.invocations.len(), 1);
    }
}

#[test]
fn same_native_id_with_changed_type_purpose_payload_or_plan_is_conflict() {
    for field in 0..4 {
        let f = Fixture::new(); let mut body = invocation_body(&f.registry, &f.owner, 100);
        match field { 0 => body.kind.push('x'), 1 => body.purpose.push('x'),
            2 => body.payload.push(1), _ => body.spawn_plan.push(1) }
        assert!(matches!(f.registry.reserve(&f.owner, issue::invocation(body)), Err(Error::Mismatch)));
        assert_eq!(f.registry.lock().unwrap().invocations.len(), 1);
    }
}

#[test]
fn invocation_provenance_is_validated_before_duplicate_lookup() {
    for field in 0..5 {
        let f = Fixture::new(); let mut body = invocation_body(&f.registry, &f.owner, 100);
        match field { 0 => body.context.data.push(1), 1 => body.session.generation += 1,
            2 => body.admission.source.stream_event_id[0] ^= 1, 3 => body.context.identity.push('x'),
            _ => body.admission.origin = Origin::Resume(f.owner.key) }
        assert!(matches!(f.registry.reserve(&f.owner, issue::invocation(body)), Err(Error::Mismatch)));
    }
    let f = Fixture::new(); let mut body = invocation_body(&f.registry, &f.owner, 100);
    let (foreign, _) = issue::producer(&f.registry); body.producer = foreign;
    assert!(matches!(f.registry.reserve(&f.owner, issue::invocation(body)), Err(Error::Origin)));
}

#[test]
fn duplicate_events_compete_for_one_atomic_reservation() {
    let f = Fixture::new(); let a = invocation(&f.registry, &f.owner, 101); let b = invocation(&f.registry, &f.owner, 101);
    let gate = std::sync::Barrier::new(3);
    std::thread::scope(|scope| {
        let first = scope.spawn(|| { gate.wait(); f.registry.reserve(&f.owner, a) });
        let second = scope.spawn(|| { gate.wait(); f.registry.reserve(&f.owner, b) });
        gate.wait(); let (a, b) = (first.join().unwrap(), second.join().unwrap());
        assert!(matches!((a, b), (Ok(_), Err(Error::AlreadyReserved)) | (Err(Error::AlreadyReserved), Ok(_))));
    });
    assert_eq!(f.registry.lock().unwrap().invocations.len(), 2);
}

#[test]
fn retired_event_cannot_be_retagged_after_genuine_resume() {
    let f = Fixture::new(); let old_event = invocation(&f.registry, &f.owner, 100);
    f.registry.retire(&f.owner).unwrap();
    let resumed = f.registry.open(resume_event(&f.registry, &f.producer, "root", f.owner.key, Placement::Root, 4)).unwrap();
    f.registry.admit(&resumed).unwrap();
    assert!(matches!(f.registry.reserve(&resumed, old_event), Err(Error::Mismatch)));
    assert!(matches!(f.registry.reserve(&resumed, invocation(&f.registry, &resumed, 100)), Err(Error::Mismatch)));
    let fresh = f.registry.reserve(&resumed, invocation(&f.registry, &resumed, 101)).unwrap();
    let binding = f.registry.binding(&fresh).unwrap();
    assert_eq!(binding.request.event.admission.origin, Origin::Resume(f.owner.key));
    assert_eq!(binding.request.event.admission.source.stream_event_id, [4; 32]);
    assert_eq!(binding.request.event.session, resumed.key);
}

#[test]
fn registry_restart_requires_fresh_admitted_producer_not_a_live_old_proof() {
    let f = Fixture::new(); let restarted = Registry::new().unwrap();
    assert!(matches!(restarted.open(event(&f.producer, "fresh", Origin::Birth, Placement::Root, 7)), Err(Error::Stale)));
    let fresh_proof = issue::readmit(&restarted, &f.producer);
    assert_ne!(fresh_proof.body().admission.boot, f.producer.body().admission.boot);
    assert!(Arc::ptr_eq(&fresh_proof.body().process, &f.producer.body().process));
    session(&restarted, &fresh_proof, "fresh", Placement::Root, 8);
    assert!(matches!(restarted.reserve(&f.owner, invocation(&f.registry, &f.owner, 100)), Err(Error::Stale)));
}

#[test]
fn readmitting_same_source_cannot_reset_its_event_namespace() {
    let f = Fixture::new(); let changed_admission = issue::readmit(&f.registry, &f.producer);
    assert!(matches!(f.registry.open(event(&changed_admission, "another", Origin::Birth, Placement::Root, 7)), Err(Error::Origin)));
    let mut body = invocation_body(&f.registry, &f.owner, 100); body.producer = changed_admission;
    assert!(matches!(f.registry.reserve(&f.owner, issue::invocation(body)), Err(Error::Origin)));
    assert_eq!(f.registry.lock().unwrap().producers.len(), 1);
}

#[test]
fn native_event_binding_changes_refuse_caller_cleanup_and_completion_receipts() {
    let held_fixture = Fixture::new(); let mut wrong_held = held_fixture.binding.clone();
    wrong_held.request.event.source.stream_event_id[0] ^= 1;
    let rejected = held_fixture.registry.bind(&held_fixture.owner, &held_fixture.right,
        held_fixture.held(wrong_held)).err().unwrap();
    assert_eq!(rejected.error, Error::Mismatch);
    assert!(Arc::ptr_eq(&rejected.held.body().process, &held_fixture.process));
    let f = Fixture::new(); f.ready(); let mut changed = f.binding.clone(); changed.request.event.source.stream_event_id[0] ^= 1;
    let caller = issue::caller(changed.clone(), f.process.clone());
    assert!(matches!(f.registry.consume(&f.owner, &f.right, &caller, &changed), Err(Error::Mismatch)));
    let receipt = issue::cleanup(changed.clone(), f.process.clone(), f.holder.clone(), CleanupPhase::BeforeConsume, CleanupOutcome::Closed);
    assert_eq!(f.registry.cleanup(receipt), Err(Error::Mismatch));
    let execution = match f.consume().unwrap() { Consume::Execute(right) => right, _ => panic!() };
    assert_eq!(f.registry.complete(&execution, issue::completion(changed, execution.permit,
        f.completion_owner.clone(), b"wrong-event")), Err(Error::Mismatch));
    assert_eq!(f.registry.complete(&execution, issue::completion(f.binding.clone(), execution.permit,
        f.completion_owner.clone(), b"actual")), Ok(b"actual".to_vec()));
}

#[test]
fn stream_message_id_cannot_be_retagged_to_another_actor_or_event_variant() {
    let f = Fixture::new();
    let child = session(&f.registry, &f.producer, "child", Placement::Child { root: f.owner.key, parent: f.owner.key }, 2);
    assert!(matches!(f.registry.reserve(&child, invocation(&f.registry, &child, 100)), Err(Error::Mismatch)));
    assert!(matches!(f.registry.reserve(&f.owner, invocation(&f.registry, &f.owner, 1)), Err(Error::Mismatch)));
    assert!(matches!(f.registry.open(event(&f.producer, "new", Origin::Birth, Placement::Root, 100)), Err(Error::Origin)));
    let (foreign, _) = issue::producer(&f.registry);
    let other = session(&f.registry, &foreign, "other", Placement::Root, 1);
    assert!(f.registry.reserve(&other, invocation(&f.registry, &other, 100)).is_ok());
}
