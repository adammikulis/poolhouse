//! These internal issuers are state-test fixtures, never native authentication evidence.
use super::*;
use super::invocation::Consume;
use proof::{testing as issue, CleanupOutcome, HeldBody, Origin, SessionEventBody};
use std::sync::atomic::{AtomicBool, Ordering};

fn event(producer: &Arc<Producer>, identity: &str, origin: Origin, placement: Placement, nonce: u8) -> proof::SessionEvent {
    issue::event(SessionEventBody { producer: producer.clone(), identity: identity.into(), context: vec![nonce],
        origin, placement, stream_event_id: [nonce; 32] })
}
fn resume_event(registry: &Registry, producer: &Arc<Producer>, identity: &str, previous: Key,
                placement: Placement, nonce: u8) -> proof::SessionEvent {
    let context = registry.lock().unwrap().sessions[&previous].context.data.clone();
    issue::event(SessionEventBody { producer: producer.clone(), identity: identity.into(), context,
        origin: Origin::Resume(previous), placement, stream_event_id: [nonce; 32] })
}
fn session(registry: &Registry, producer: &Arc<Producer>, name: &str, placement: Placement, nonce: u8) -> SessionRight {
    let right = registry.open(event(producer, name, Origin::Birth, placement, nonce)).unwrap();
    registry.admit(&right).unwrap(); right
}
fn invocation_body(registry: &Registry, owner: &SessionRight, nonce: u8) -> proof::InvocationEventBody {
    let store = registry.lock().unwrap(); let session = &store.sessions[&owner.key];
    proof::InvocationEventBody { producer: session.producer.clone(), session: owner.key,
        admission: session.admission.clone(), context: session.context.clone(), stream_event_id: [nonce; 32],
        kind: "completion".into(), purpose: "record-result".into(), payload: b"native-event-payload".to_vec(),
        spawn_plan: b"immutable-plan".to_vec() }
}
fn invocation(registry: &Registry, owner: &SessionRight, nonce: u8) -> proof::InvocationEvent {
    issue::invocation(invocation_body(registry, owner, nonce))
}

struct Fixture {
    registry: Registry, producer: Arc<Producer>, producer_live: Arc<AtomicBool>, owner: SessionRight,
    right: Reservation, binding: Binding, process: Arc<proof::Process>, process_live: Arc<AtomicBool>,
    holder: Arc<proof::Process>, holder_live: Arc<AtomicBool>, completion_owner: Arc<proof::Process>,
}
impl Fixture {
    fn new() -> Self {
        let registry = Registry::new().unwrap();
        let (producer, producer_live) = issue::producer(&registry);
        let owner = session(&registry, &producer, "root", Placement::Root, 1);
        let right = registry.reserve(&owner, invocation(&registry, &owner, 100)).unwrap();
        let binding = registry.binding(&right).unwrap();
        let (process, process_live) = issue::process();
        let (holder, holder_live) = issue::process();
        let (completion_owner, _) = issue::process();
        Self { registry, producer, producer_live, owner, right, binding, process, process_live, holder, holder_live, completion_owner }
    }
    fn held(&self, binding: Binding) -> proof::Held {
        issue::held(HeldBody { binding, producer: self.producer.clone(), process: self.process.clone(),
            holder: self.holder.clone(), completion_owner: self.completion_owner.clone() })
    }
    fn bind(&self) { self.registry.bind(&self.owner, &self.right, self.held(self.binding.clone())).unwrap_or_else(|failure| panic!("{:?}", failure.error)); }
    fn release(&self) { self.registry.release(&self.owner, &self.right, &self.binding).unwrap(); }
    fn clean(&self, phase: CleanupPhase, outcome: CleanupOutcome) -> Result<CleanupState, Error> {
        self.registry.cleanup(issue::cleanup(self.binding.clone(), self.process.clone(), self.holder.clone(), phase, outcome))
    }
    fn caller(&self) -> proof::Caller { issue::caller(self.binding.clone(), self.process.clone()) }
    fn consume(&self) -> Result<Consume, Error> {
        self.registry.consume(&self.owner, &self.right, &self.caller(), &self.binding)
    }
    fn ready(&self) { self.bind(); self.release(); self.clean(CleanupPhase::BeforeConsume, CleanupOutcome::Closed).unwrap(); }
    fn state(&self) -> InvocationState { self.registry.lock().unwrap().invocations[&self.right.key].state }
}

#[test]
fn authenticated_opening_is_not_live_and_birth_is_not_resume() {
    let registry = Registry::new().unwrap(); let (producer, _) = issue::producer(&registry);
    let root = registry.open(event(&producer, "root", Origin::Birth, Placement::Root, 1)).unwrap();
    assert!(matches!(registry.reserve(&root, invocation(&registry, &root, 100)), Err(Error::Stale)));
    registry.admit(&root).unwrap();
    assert!(matches!(registry.open(event(&producer, "root", Origin::Birth, Placement::Root, 2)), Err(Error::Origin)));
    assert!(matches!(registry.open(event(&producer, "root", Origin::Resume(root.key), Placement::Root, 3)), Err(Error::Origin)));
    registry.retire(&root).unwrap();
    let resumed = registry.open(resume_event(&registry, &producer, "root", root.key, Placement::Root, 4)).unwrap();
    registry.admit(&resumed).unwrap(); assert_ne!(root.key, resumed.key);
    assert!(matches!(registry.reserve(&root, invocation(&registry, &root, 100)), Err(Error::Stale)));
}

#[test]
fn exact_root_and_direct_parent_graph_and_event_replay_are_checked() {
    let registry = Registry::new().unwrap(); let (producer, _) = issue::producer(&registry);
    let root = session(&registry, &producer, "root", Placement::Root, 1);
    let other = session(&registry, &producer, "other", Placement::Root, 2);
    let child = session(&registry, &producer, "child", Placement::Child { root: root.key, parent: root.key }, 3);
    let wrong = Placement::Child { root: other.key, parent: child.key };
    assert!(matches!(registry.open(event(&producer, "nested", Origin::Birth, wrong, 4)), Err(Error::Graph)));
    let good = Placement::Child { root: root.key, parent: child.key };
    session(&registry, &producer, "nested", good.clone(), 5);
    assert!(matches!(registry.open(event(&producer, "replayed", Origin::Birth, good, 5)), Err(Error::Origin)));
}

#[test]
fn bind_release_and_consume_require_order_exact_context_and_single_use() {
    let f = Fixture::new();
    assert!(matches!(f.consume(), Err(Error::State)));
    f.bind(); assert_eq!(f.registry.bind(&f.owner, &f.right, f.held(f.binding.clone())).map_err(|failure| failure.error), Err(Error::State));
    assert!(matches!(f.consume(), Err(Error::State)));
    let mut altered = f.binding.clone(); altered.request.spawn_plan.push(1);
    assert_eq!(f.registry.release(&f.owner, &f.right, &altered), Err(Error::Mismatch));
    f.release(); assert_eq!(f.registry.release(&f.owner, &f.right, &f.binding), Err(Error::State));
    assert!(matches!(f.consume(), Err(Error::Cleanup)));
    f.clean(CleanupPhase::BeforeConsume, CleanupOutcome::Closed).unwrap();
    assert!(matches!(f.consume(), Ok(Consume::Execute(_))));
    assert!(matches!(f.consume(), Ok(Consume::Pending)));
}

#[test]
fn every_immutable_binding_field_refuses_change() {
    for field in 0..8 {
        let f = Fixture::new(); f.ready(); let mut changed = f.binding.clone();
        match field { 0 => changed.request.event.kind.push('x'), 1 => changed.request.purpose.push('x'),
            2 => changed.request.digest[0] ^= 1, 3 => changed.request.spawn_plan.push(1),
            4 => changed.context.data.push(1), 5 => changed.installation[0] ^= 1,
            6 => changed.launch_epoch[0] ^= 1, _ => changed.session.generation += 1 }
        let caller = issue::caller(changed.clone(), f.process.clone());
        assert!(matches!(f.registry.consume(&f.owner, &f.right, &caller, &changed), Err(Error::Mismatch)));
    }
}

#[test]
fn cleanup_requires_responsible_holder_and_exact_process_not_a_boolean() {
    let f = Fixture::new(); f.bind(); f.release(); let (wrong, _) = issue::process();
    let receipt = issue::cleanup(f.binding.clone(), f.process.clone(), wrong, CleanupPhase::BeforeConsume, CleanupOutcome::Closed);
    assert_eq!(f.registry.cleanup(receipt), Err(Error::Mismatch));
    assert_eq!(f.clean(CleanupPhase::BeforeConsume, CleanupOutcome::Failed), Ok(CleanupState::Failed));
    assert!(matches!(f.consume(), Err(Error::Cleanup)));
    let (wrong, _) = issue::process(); let caller = issue::caller(f.binding.clone(), wrong);
    assert!(matches!(f.registry.consume(&f.owner, &f.right, &caller, &f.binding), Err(Error::Mismatch)));
}

#[test]
fn foreign_producer_or_held_binding_invalidates_without_reassignment() {
    let f = Fixture::new(); let (foreign, _) = issue::producer(&f.registry);
    let held = issue::held(HeldBody { binding: f.binding.clone(), producer: foreign, process: f.process.clone(),
        holder: f.holder.clone(), completion_owner: f.completion_owner.clone() });
    assert_eq!(f.registry.bind(&f.owner, &f.right, held).map_err(|failure| failure.error), Err(Error::Mismatch));
    assert_eq!(f.state(), InvocationState::Invalidated);
    assert_eq!(f.registry.bind(&f.owner, &f.right, f.held(f.binding.clone())).map_err(|failure| failure.error), Err(Error::State));
}

#[test]
fn cleanup_does_not_attest_domain_result_and_completion_replays_exactly() {
    let f = Fixture::new(); f.ready();
    let execution = match f.consume().unwrap() { Consume::Execute(right) => right, _ => panic!() };
    let receipt = |owner, bytes: &[u8]| issue::completion(f.binding.clone(), execution.permit, owner, bytes);
    assert_eq!(f.registry.complete(&execution, receipt(f.holder.clone(), b"success")), Err(Error::Mismatch));
    assert!(matches!(f.consume(), Ok(Consume::Pending)));
    assert_eq!(f.registry.complete(&execution, receipt(f.completion_owner.clone(), b"actual-result")), Ok(b"actual-result".to_vec()));
    assert_eq!(f.registry.complete(&execution, receipt(f.completion_owner.clone(), b"actual-result")), Ok(b"actual-result".to_vec()));
    assert_eq!(f.registry.complete(&execution, receipt(f.completion_owner.clone(), b"different")), Err(Error::Mismatch));
    assert!(matches!(f.consume(), Ok(Consume::Recorded(bytes)) if bytes == b"actual-result"));
    assert_eq!(f.registry.cancel(&f.owner, &f.right).err(), Some(Error::State));
}

#[test]
fn cancellation_revokes_before_request_and_old_cleanup_cannot_resolve_it() {
    let f = Fixture::new(); f.ready();
    let request = f.registry.cancel(&f.owner, &f.right).unwrap().unwrap();
    assert_eq!(request.invocation, f.right.key); assert_eq!(request.binding, f.binding);
    assert!(Arc::ptr_eq(&request.held.body().process, &f.process));
    assert_eq!(f.state(), InvocationState::Cancelled);
    assert!(f.consume().is_err());
    assert_eq!(f.clean(CleanupPhase::BeforeConsume, CleanupOutcome::Closed), Err(Error::Mismatch));
    assert_eq!(f.clean(CleanupPhase::Terminal, CleanupOutcome::Failed), Ok(CleanupState::Failed));
    assert_eq!(f.registry.lock().unwrap().invocations[&f.right.key].cleanup, CleanupState::Failed);
    f.clean(CleanupPhase::Terminal, CleanupOutcome::Closed).unwrap();
}

#[test]
fn consume_and_cancel_compete_at_one_atomic_terminal_transition() {
    let f = Fixture::new(); f.ready(); let gate = std::sync::Barrier::new(3);
    std::thread::scope(|scope| {
        let consume = scope.spawn(|| { gate.wait(); f.consume() });
        let cancel = scope.spawn(|| { gate.wait(); f.registry.cancel(&f.owner, &f.right) });
        gate.wait(); let (a, b) = (consume.join().unwrap(), cancel.join().unwrap());
        assert!(matches!((&a, &b), (Ok(Consume::Execute(_)), Err(Error::State)) | (Err(Error::State), Ok(Some(_)))));
    });
}

#[test]
fn retire_invalidates_descendants_and_resume_cannot_reuse_them() {
    let f = Fixture::new();
    let child = session(&f.registry, &f.producer, "child", Placement::Child { root: f.owner.key, parent: f.owner.key }, 2);
    let nested = session(&f.registry, &f.producer, "nested", Placement::Child { root: f.owner.key, parent: child.key }, 3);
    let right = f.registry.reserve(&nested, invocation(&f.registry, &nested, 101)).unwrap();
    f.bind(); let obligations = f.registry.retire(&f.owner).unwrap(); assert_eq!(obligations.len(), 1);
    let store = f.registry.lock().unwrap();
    assert_eq!(store.sessions[&child.key].state, SessionState::Retired);
    assert_eq!(store.invocations[&right.key].state, InvocationState::Invalidated); drop(store);
    let resumed = f.registry.open(resume_event(&f.registry, &f.producer, "root", f.owner.key, Placement::Root, 4)).unwrap();
    f.registry.admit(&resumed).unwrap();
    assert!(f.registry.reserve(&child, invocation(&f.registry, &child, 100)).is_err());
    assert!(f.registry.bind(&f.owner, &f.right, f.held(f.binding.clone())).map_err(|failure| failure.error).is_err());
}

#[test]
fn losing_lifetimes_invalidates_only_outstanding_work() {
    for loss in 0..3 {
        let f = Fixture::new(); f.ready();
        [&f.producer_live, &f.process_live, &f.holder_live][loss].store(false, Ordering::SeqCst);
        assert_eq!(f.registry.reconcile().unwrap().len(), 1);
        assert_eq!(f.state(), InvocationState::Invalidated); assert!(f.consume().is_err());
    }
    let f = Fixture::new(); f.ready();
    let permit = match f.consume().unwrap() { Consume::Execute(permit) => permit, _ => panic!() };
    drop(permit); f.holder_live.store(false, Ordering::SeqCst);
    assert!(f.registry.reconcile().unwrap().is_empty()); assert_eq!(f.state(), InvocationState::Consumed);
    assert!(matches!(f.consume(), Err(Error::Stale)));
    assert!(f.registry.retire(&f.owner).unwrap().is_empty()); assert!(matches!(f.consume(), Err(Error::Stale)));
}

#[test]
fn fresh_boot_epoch_rejects_all_previous_rights_and_receipts() {
    let f = Fixture::new(); f.ready(); let restarted = Registry::new().unwrap();
    assert_ne!(f.right.key.boot, restarted.lock().unwrap().boot);
    assert_ne!(f.right.key.boot, f.binding.launch_epoch);
    assert!(matches!(restarted.reserve(&f.owner, invocation(&f.registry, &f.owner, 100)), Err(Error::Stale)));
    assert!(matches!(restarted.consume(&f.owner, &f.right, &f.caller(), &f.binding), Err(Error::Stale)));
    assert!(matches!(restarted.cleanup(issue::cleanup(f.binding.clone(), f.process.clone(), f.holder.clone(),
        CleanupPhase::BeforeConsume, CleanupOutcome::Closed)), Err(Error::Stale)));
}

#[test]
fn foreign_owner_cannot_invalidate_a_reservation_and_rejected_child_is_returned() {
    let f = Fixture::new();
    let stranger = session(&f.registry, &f.producer, "stranger", Placement::Root, 2);
    let failure = f.registry.bind(&stranger, &f.right, f.held(f.binding.clone())).err().unwrap();
    assert_eq!(failure.error, Error::Mismatch);
    assert!(Arc::ptr_eq(&failure.held.body().process, &f.process));
    assert_eq!(f.state(), InvocationState::Reserved);
    f.registry.bind(&f.owner, &f.right, failure.held).unwrap_or_else(|failure| panic!("{:?}", failure.error));
    assert_eq!(f.state(), InvocationState::Bound);
}

#[test]
fn child_and_resume_require_same_admitted_producer_and_installation_scope() {
    for installation in [[1; 32], [9; 32]] {
        let f = Fixture::new(); let (foreign, _) = issue::producer_for(&f.registry, installation);
        let placement = Placement::Child { root: f.owner.key, parent: f.owner.key };
        assert!(matches!(f.registry.open(event(&foreign, "child", Origin::Birth, placement, 2)), Err(Error::Graph)));
        f.registry.retire(&f.owner).unwrap();
        assert!(matches!(f.registry.open(event(&foreign, "root", Origin::Resume(f.owner.key), Placement::Root, 3)), Err(Error::Origin)));
    }
    let f = Fixture::new(); let (other_installation, _) = issue::producer_for(&f.registry, [9; 32]);
    session(&f.registry, &other_installation, "root", Placement::Root, 2);
    assert_eq!(f.registry.lock().unwrap().identities.len(), 2);
}

#[test]
fn terminal_results_remain_recorded_but_old_authorization_cannot_replay_them() {
    let f = Fixture::new(); f.ready();
    let permit = match f.consume().unwrap() { Consume::Execute(permit) => permit, _ => panic!() };
    f.registry.complete(&permit, issue::completion(f.binding.clone(), permit.permit,
        f.completion_owner.clone(), b"recorded")).unwrap();
    f.registry.retire(&f.owner).unwrap();
    assert!(matches!(f.consume(), Err(Error::Stale)));
    assert_eq!(f.registry.lock().unwrap().invocations[&f.right.key].result.as_deref(), Some(b"recorded".as_slice()));
}

#[test]
fn resume_refuses_context_mutation_and_root_child_or_parent_substitution() {
    let f = Fixture::new();
    let child = session(&f.registry, &f.producer, "child", Placement::Child { root: f.owner.key, parent: f.owner.key }, 2);
    let stranger = session(&f.registry, &f.producer, "stranger", Placement::Root, 3);
    let stranger_child = session(&f.registry, &f.producer, "stranger-child", Placement::Child { root: stranger.key, parent: stranger.key }, 4);
    f.registry.retire(&f.owner).unwrap();
    let mutated = event(&f.producer, "root", Origin::Resume(f.owner.key), Placement::Root, 5);
    assert!(matches!(f.registry.open(mutated), Err(Error::Origin)));
    let root_as_child = Placement::Child { root: stranger.key, parent: stranger.key };
    assert!(matches!(f.registry.open(resume_event(&f.registry, &f.producer, "root", f.owner.key, root_as_child, 6)), Err(Error::Graph)));
    assert!(matches!(f.registry.open(resume_event(&f.registry, &f.producer, "child", child.key, Placement::Root, 7)), Err(Error::Graph)));
    let swapped_root = Placement::Child { root: stranger.key, parent: stranger_child.key };
    assert!(matches!(f.registry.open(resume_event(&f.registry, &f.producer, "child", child.key, swapped_root, 8)), Err(Error::Graph)));
    let root = f.registry.open(resume_event(&f.registry, &f.producer, "root", f.owner.key, Placement::Root, 9)).unwrap();
    f.registry.admit(&root).unwrap();
    let sibling = session(&f.registry, &f.producer, "sibling", Placement::Child { root: root.key, parent: root.key }, 10);
    let swapped_parent = Placement::Child { root: root.key, parent: sibling.key };
    assert!(matches!(f.registry.open(resume_event(&f.registry, &f.producer, "child", child.key, swapped_parent, 11)), Err(Error::Graph)));
}

#[test]
fn genuine_nested_resume_uses_new_generations_of_same_logical_ancestors() {
    let f = Fixture::new();
    let child = session(&f.registry, &f.producer, "child", Placement::Child { root: f.owner.key, parent: f.owner.key }, 2);
    let nested = session(&f.registry, &f.producer, "nested", Placement::Child { root: f.owner.key, parent: child.key }, 3);
    f.registry.retire(&f.owner).unwrap();
    let root = f.registry.open(resume_event(&f.registry, &f.producer, "root", f.owner.key, Placement::Root, 4)).unwrap();
    f.registry.admit(&root).unwrap();
    let child2 = f.registry.open(resume_event(&f.registry, &f.producer, "child", child.key,
        Placement::Child { root: root.key, parent: root.key }, 5)).unwrap();
    f.registry.admit(&child2).unwrap();
    let nested2 = f.registry.open(resume_event(&f.registry, &f.producer, "nested", nested.key,
        Placement::Child { root: root.key, parent: child2.key }, 6)).unwrap();
    f.registry.admit(&nested2).unwrap();
    assert_ne!(child.key, child2.key); assert_ne!(nested.key, nested2.key);
    assert!(f.registry.reserve(&nested, invocation(&f.registry, &nested, 101)).is_err());
    assert!(f.registry.reserve(&nested2, invocation(&f.registry, &nested2, 101)).is_ok());
}

#[path = "event_tests.rs"]
mod event_tests;
