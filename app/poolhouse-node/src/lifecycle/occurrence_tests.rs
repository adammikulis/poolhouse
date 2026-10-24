//! Test-only storage protocol fault injection; no native provenance/disk durability claim.
use super::*;
#[derive(Default)]
struct TestStore { committed: Vec<Mutation>, fault: u8 }
impl Store for TestStore {
    fn commit(&mut self, exact: &Mutation) -> Result<Acknowledgement, CommitFailure> {
        if self.fault == 1 { return Err(CommitFailure::NoCommit); }
        self.committed.push(exact.clone());
        if self.fault == 2 { return Err(CommitFailure::Indeterminate); }
        let mut ack = exact.clone();
        match self.fault {
            3 => ack.after += 1, 4 => ack.binding.writer[0] ^= 1,
            5 => ack.source.occurrence[0] ^= 1, 6 => ack.next.state = State::Retired,
            7 => ack.before += 1, 8 => ack.binding.continuity[0] ^= 1,
            _ => (),
        }
        Ok(Acknowledgement { exact: ack })
    }
}
fn source(n: u8) -> Source { Source { installation: [1; 32], continuity: [2; 32], original_launch: [3; 32], occurrence: [n; 32] } }
fn context() -> Context { Context { actor: b"actor".to_vec(), root: b"actor".to_vec(), parent: None, bytes: b"exact\ncontext".to_vec() } }
fn fence() -> Fence<TestStore> { Fence { binding: StoreBinding { store: [4; 32], installation: [1; 32], continuity: [2; 32], writer: [5; 32] }, revision: 0, records: HashMap::new(), store: TestStore::default(), usable: true } }
fn birth(f: &mut Fence<TestStore>) {
    assert!(matches!(f.admit(source(1), Original::Birth { context: context() }), Ok(Admission::Committed(_))));
    f.change(&source(1), Transition::Admit).unwrap();
}
fn spec(n: u8) -> DispatchSpec { DispatchSpec { handler: [n; 32], measured_plan: vec![n], purpose: vec![n], operation: vec![n], owner: [7; 32] } }
fn invocation() -> Original { Original::Invocation { context: context(), admission: source(1), kind: b"invoke".to_vec(), payload: b"actual bytes".to_vec(), catalog: DispatchCatalog { config_generation: [13; 32], entries: vec![spec(1), spec(2)] } } }
fn dispatch_state(f: &Fence<TestStore>, n: usize) -> &State { match &f.records[&source(2)].state { State::Invocation(entries) => &entries[n].state, _ => panic!("invocation required") } }
fn dispatch(f: &mut Fence<TestStore>, event: Transition) -> Result<Committed, Error> { f.change_dispatch(&source(2), &spec(1), event) }
fn custody() -> Custody { Custody { owner: [7; 32], resources: vec![Resource { identity: [8; 32], action: Action::CloseChannel }, Resource { identity: [9; 32], action: Action::Preserve }] } }
fn reserved() -> Fence<TestStore> { let mut f = fence(); birth(&mut f); f.admit(source(2), invocation()).unwrap(); f }
fn transitions() -> Vec<Transition> { vec![Transition::Spawn(custody()), Transition::Bind(custody()), Transition::FenceRelease(custody()), Transition::Released(custody()), Transition::FenceEffect { custody: custody(), operation: spec(1).operation }, Transition::Complete { operation: spec(1).operation, outcome: Outcome::Success(b"result".to_vec()) }, Transition::Cleanup { custody: custody(), result: Cleanup::Closed }] }
#[test]
fn original_namespace_is_shared_and_exact() {
    let mut f = fence(); birth(&mut f);
    assert!(matches!(f.admit(source(1), invocation()), Err(Error::Conflict)));
    f.admit(source(2), invocation()).unwrap();
    let count = f.store.committed.len();
    assert!(matches!(f.admit(source(2), invocation()), Ok(Admission::Existing(State::Invocation(_)))));
    assert_eq!(f.store.committed.len(), count);
    for field in 0..4 {
        let mut altered = invocation();
        if let Original::Invocation { context, payload, catalog, .. } = &mut altered {
            match field { 0 => context.actor.push(0), 1 => payload.push(0), 2 => catalog.entries[0].measured_plan.push(0), _ => catalog.entries[0].purpose.push(0) }
        }
        assert!(matches!(f.admit(source(2), altered), Err(Error::Conflict)));
    }
    // New transport/node generations do not exist in this source key at all.
    assert_eq!(f.records.len(), 2);
}
#[test]
fn failure_at_every_transition_withholds_receipt_and_poisoned_retry() {
    for boundary in 0..7 {
        for fault in 1..=8 {
            let mut f = reserved();
            for (index, step) in transitions().into_iter().enumerate() {
                if index == boundary {
                    f.store.fault = fault; let rev = f.revision; let old = f.records.clone();
                    assert!(matches!(dispatch(&mut f, step), Err(Error::Uncertain)));
                    assert_eq!(f.revision, rev); assert_eq!(f.records, old);
                    assert!(matches!(f.admit(source(2), invocation()), Err(Error::Unavailable)));
                    break;
                }
                dispatch(&mut f, step).unwrap();
            }
        }
    }
}
#[test]
fn first_admission_failure_never_publishes_a_mapping() {
    for fault in 1..=8 {
        let mut f = fence(); f.store.fault = fault;
        assert!(matches!(f.admit(source(1), Original::Birth { context: context() }), Err(Error::Uncertain)));
        assert!(f.records.is_empty()); assert_eq!(f.revision, 0);
    }
}
#[test]
fn exact_receipt_binds_prior_new_revision_writer_and_original() {
    let mut f = fence();
    let receipt = match f.admit(source(1), Original::Birth { context: context() }).unwrap() {
        Admission::Committed(c) => c, _ => panic!("new admission required"),
    };
    assert_eq!(receipt.exact, f.store.committed[0]);
    assert_eq!(receipt.exact.before, 0); assert_eq!(receipt.exact.after, 1);
    assert_eq!(receipt.exact.binding, f.binding); assert_eq!(receipt.exact.source, source(1));
}
#[test]
fn cancellation_and_effect_fence_have_one_winner_and_preserve_obligations() {
    let mut stopped = reserved();
    for step in transitions().into_iter().take(4) { dispatch(&mut stopped, step).unwrap(); }
    dispatch(&mut stopped, Transition::Stop).unwrap();
    assert!(matches!(dispatch(&mut stopped, Transition::FenceEffect { custody: custody(), operation: vec![] }), Err(Error::State)));
    assert_eq!(dispatch_state(&stopped, 0), &State::Stopped { custody: Some(custody()), cleanup: Cleanup::Pending });
    let mut consumed = reserved();
    for step in transitions().into_iter().take(5) { dispatch(&mut consumed, step).unwrap(); }
    assert!(matches!(dispatch(&mut consumed, Transition::Stop), Err(Error::State)));
    dispatch(&mut consumed, Transition::Unknown).unwrap();
    assert!(matches!(consumed.admit(source(2), invocation()), Ok(Admission::Existing(State::Invocation(_)))));
    assert!(matches!(dispatch(&mut consumed, Transition::Spawn(custody())), Err(Error::State)));
}
#[test]
fn hidden_or_changed_custody_cannot_bind_release_complete_or_cleanup() {
    let mut f = reserved();
    let mut duplicate = custody(); duplicate.resources.push(duplicate.resources[0].clone());
    assert!(matches!(dispatch(&mut f, Transition::Spawn(duplicate)), Err(Error::State)));
    dispatch(&mut f, Transition::Spawn(custody())).unwrap();
    let mut missing = custody(); missing.resources.pop();
    assert!(matches!(dispatch(&mut f, Transition::Bind(missing.clone())), Err(Error::State)));
    for step in transitions().into_iter().skip(1).take(4) { dispatch(&mut f, step).unwrap(); }
    assert!(matches!(dispatch(&mut f, Transition::Complete { operation: vec![], outcome: Outcome::Success(vec![]) }), Err(Error::State)));
    dispatch(&mut f, Transition::Unknown).unwrap();
    assert!(matches!(dispatch(&mut f, Transition::Cleanup { custody: missing, result: Cleanup::Closed }), Err(Error::State)));
}
#[test]
fn restored_data_never_remints_old_occurrence_fresh_resume_needs_retired_mapping() {
    let mut f = fence(); birth(&mut f);
    let resume = Original::Resume { context: context(), prior: source(1) };
    assert!(matches!(f.admit(source(3), resume.clone()), Err(Error::State)));
    f.change(&source(1), Transition::Retire).unwrap();
    assert!(matches!(f.admit(source(4), Original::Birth { context: context() }), Err(Error::Conflict)));
    f.admit(source(3), resume.clone()).unwrap();
    // Test-only move across coordinator lifetimes is protocol simulation, NOT trusted recovery.
    let mut restarted = Fence { binding: f.binding, revision: f.revision, records: f.records, store: f.store, usable: true };
    restarted.binding.writer = [10; 32];
    assert!(matches!(restarted.admit(source(1), Original::Birth { context: context() }), Ok(Admission::Existing(State::Retired))));
    assert!(matches!(restarted.admit(source(3), resume.clone()), Ok(Admission::Existing(State::AdmissionFenced))));
    assert!(matches!(restarted.admit(source(4), resume), Err(Error::Conflict)));
    let mut replacement = source(5); replacement.original_launch = [11; 32];
    assert!(matches!(restarted.admit(replacement, Original::Resume { context: context(), prior: source(1) }), Err(Error::Conflict | Error::State)));
}
#[test]
fn overflow_and_wrong_continuity_refuse_without_store_write() {
    let mut f = fence(); f.revision = u64::MAX;
    assert!(matches!(f.admit(source(1), Original::Birth { context: context() }), Err(Error::Exhausted)));
    assert!(f.store.committed.is_empty());
    let mut foreign = source(1); foreign.continuity = [12; 32];
    assert!(matches!(f.admit(foreign, Original::Birth { context: context() }), Err(Error::Scope)));
}

#[test]
fn retirement_fences_each_future_action_but_preserves_completion_and_cleanup() {
    for boundary in 0..5 {
        let mut f = reserved();
        for step in transitions().into_iter().take(boundary) { dispatch(&mut f, step).unwrap(); }
        f.change(&source(1), Transition::Retire).unwrap();
        let step = transitions().into_iter().nth(boundary).unwrap();
        assert!(matches!(dispatch(&mut f, step), Err(Error::State)));
        // Cancellation keeps actual recorded resource responsibility, not a fake close.
        dispatch(&mut f, Transition::Stop).unwrap();
    }
    let mut f = reserved();
    for step in transitions().into_iter().take(5) { dispatch(&mut f, step).unwrap(); }
    f.change(&source(1), Transition::Retire).unwrap();
    for step in transitions().into_iter().skip(5) { dispatch(&mut f, step).unwrap(); }
    assert!(matches!(dispatch_state(&f, 0), State::Completed { cleanup: Cleanup::Closed, .. }));
    let mut opening = fence();
    opening.admit(source(1), Original::Birth { context: context() }).unwrap();
    opening.change(&source(1), Transition::Retire).unwrap();
    assert!(matches!(opening.change(&source(1), Transition::Admit), Err(Error::State)));
}

#[test]
fn one_original_event_can_progress_two_independent_handlers() {
    let mut f = reserved();
    for step in transitions().into_iter().take(5) { dispatch(&mut f, step).unwrap(); }
    // A sibling handler belongs to this original event, not a fabricated new occurrence.
    let mut sibling = custody(); sibling.resources[0].identity = [18; 32]; sibling.resources[1].identity = [19; 32];
    f.change_dispatch(&source(2), &spec(2), Transition::Spawn(sibling.clone())).unwrap();
    assert!(matches!(dispatch_state(&f, 0), State::EffectFenced { .. }));
    assert_eq!(dispatch_state(&f, 1), &State::SpawnFenced(sibling));
    assert!(matches!(dispatch(&mut f, Transition::Spawn(custody())), Err(Error::State)));
    assert!(matches!(f.change(&source(2), Transition::Spawn(custody())), Err(Error::State)));
    assert_eq!(f.records.len(), 2);
}

#[test]
fn frozen_catalog_refuses_reload_duplicate_handlers_and_empty_catalog_has_no_right() {
    let mut f = reserved();
    let count = f.store.committed.len();
    for field in 0..3 {
        let mut changed = invocation();
        if let Original::Invocation { catalog, .. } = &mut changed {
            match field { 0 => catalog.config_generation[0] ^= 1, 1 => catalog.entries.push(spec(3)), _ => catalog.entries.reverse() }
        }
        assert!(matches!(f.admit(source(2), changed), Err(Error::Conflict)));
    }
    assert_eq!(f.store.committed.len(), count);
    let mut duplicate = invocation();
    if let Original::Invocation { catalog, .. } = &mut duplicate { catalog.entries.push(spec(1)); }
    assert!(matches!(f.admit(source(3), duplicate), Err(Error::Conflict)));
    let mut empty = invocation();
    if let Original::Invocation { catalog, .. } = &mut empty { catalog.entries.clear(); }
    f.admit(source(3), empty).unwrap();
    assert!(matches!(f.change_dispatch(&source(3), &spec(1), Transition::Spawn(custody())), Err(Error::Conflict)));
    assert!(matches!(f.records[&source(3)].state, State::Invocation(ref entries) if entries.is_empty()));
}

#[test]
fn dispatch_selector_plan_purpose_owner_operation_and_sibling_custody_cannot_substitute() {
    let mut f = reserved();
    let count = f.store.committed.len();
    for field in 0..5 {
        let mut changed = spec(1);
        match field {
            0 => changed.handler = [99; 32], 1 => changed.measured_plan.push(0),
            2 => changed.purpose.push(0), 3 => changed.operation.push(0), _ => changed.owner[0] ^= 1,
        }
        assert!(matches!(f.change_dispatch(&source(2), &changed, Transition::Spawn(custody())), Err(Error::Conflict)));
    }
    let mut wrong_owner = custody(); wrong_owner.owner[0] ^= 1;
    assert!(matches!(dispatch(&mut f, Transition::Spawn(wrong_owner)), Err(Error::State)));
    assert_eq!(f.store.committed.len(), count);
    for step in transitions().into_iter().take(4) { dispatch(&mut f, step).unwrap(); }
    assert!(matches!(dispatch(&mut f, Transition::FenceEffect { custody: custody(), operation: b"another effect".to_vec() }), Err(Error::State)));
    assert!(matches!(f.change_dispatch(&source(2), &spec(2), Transition::Spawn(custody())), Err(Error::State)));
    assert_eq!(dispatch_state(&f, 1), &State::Reserved);
}

#[test]
fn retry_keeps_both_dispatch_dispositions_and_cleanup_never_releases_sibling() {
    let mut f = reserved();
    for step in transitions().into_iter().take(6) { dispatch(&mut f, step).unwrap(); }
    let mut sibling = custody(); sibling.resources[0].identity = [18; 32]; sibling.resources[1].identity = [19; 32];
    f.change_dispatch(&source(2), &spec(2), Transition::Spawn(sibling.clone())).unwrap();
    let before = f.records[&source(2)].state.clone();
    let count = f.store.committed.len();
    match f.admit(source(2), invocation()).unwrap() {
        Admission::Existing(state) => assert_eq!(state, before), _ => panic!("retry reminted event"),
    }
    assert_eq!(f.store.committed.len(), count);
    dispatch(&mut f, Transition::Cleanup { custody: custody(), result: Cleanup::Closed }).unwrap();
    assert_eq!(dispatch_state(&f, 1), &State::SpawnFenced(sibling));
    assert!(matches!(dispatch(&mut f, Transition::Spawn(custody())), Err(Error::State)));
    f.change(&source(1), Transition::Retire).unwrap();
    assert!(matches!(f.change_dispatch(&source(2), &spec(2), Transition::Bind(dispatch_state(&f, 1).custody().unwrap().clone())), Err(Error::State)));
    f.change_dispatch(&source(2), &spec(2), Transition::Stop).unwrap();
}

#[test]
fn lost_ack_poison_covers_siblings_and_old_source_cannot_gain_new_dispatch_identity() {
    let mut f = reserved();
    for step in transitions().into_iter().take(5) { dispatch(&mut f, step).unwrap(); }
    let snapshot = f.records.clone();
    f.store.fault = 2;
    assert!(matches!(dispatch(&mut f, Transition::Unknown), Err(Error::Uncertain)));
    assert_eq!(f.records, snapshot);
    assert!(matches!(f.change_dispatch(&source(2), &spec(2), Transition::Stop), Err(Error::Unavailable)));
    assert!(matches!(f.admit(source(2), invocation()), Err(Error::Unavailable)));
    // Protocol-only recovery simulation: an acknowledged Unknown does not reset a
    // consumed dispatch, and changing catalog identity still conflicts with the source.
    let mut fresh = reserved();
    for step in transitions().into_iter().take(5) { dispatch(&mut fresh, step).unwrap(); }
    dispatch(&mut fresh, Transition::Unknown).unwrap();
    assert!(matches!(dispatch(&mut fresh, Transition::Spawn(custody())), Err(Error::State)));
    let mut rekeyed = invocation();
    if let Original::Invocation { catalog, .. } = &mut rekeyed { catalog.entries[0].handler = [88; 32]; }
    assert!(matches!(fresh.admit(source(2), rekeyed), Err(Error::Conflict)));
}

#[test]
fn distinct_handlers_cannot_reserve_same_owner_operation_twice() {
    let mut f = fence(); birth(&mut f);
    let mut duplicate = invocation();
    if let Original::Invocation { catalog, .. } = &mut duplicate {
        catalog.entries[1].operation = catalog.entries[0].operation.clone();
        catalog.entries[1].owner = catalog.entries[0].owner;
    }
    let count = f.store.committed.len();
    assert!(matches!(f.admit(source(2), duplicate), Err(Error::Conflict)));
    assert_eq!(f.store.committed.len(), count);
    assert!(!f.records.contains_key(&source(2)));
}
