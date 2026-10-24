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
fn invocation() -> Original { Original::Invocation { context: context(), admission: source(1), kind: b"invoke".to_vec(), purpose: b"purpose".to_vec(), payload: b"actual bytes".to_vec(), measured_plan: b"plan".to_vec() } }
fn custody() -> Custody { Custody { owner: [7; 32], resources: vec![Resource { identity: [8; 32], action: Action::CloseChannel }, Resource { identity: [9; 32], action: Action::Preserve }] } }
fn reserved() -> Fence<TestStore> { let mut f = fence(); birth(&mut f); f.admit(source(2), invocation()).unwrap(); f }
fn transitions() -> Vec<Transition> { vec![Transition::Spawn(custody()), Transition::Bind(custody()), Transition::FenceRelease(custody()), Transition::Released(custody()), Transition::FenceEffect { custody: custody(), operation: b"exact operation".to_vec() }, Transition::Complete { operation: b"exact operation".to_vec(), outcome: Outcome::Success(b"result".to_vec()) }, Transition::Cleanup { custody: custody(), result: Cleanup::Closed }] }
#[test]
fn original_namespace_is_shared_and_exact() {
    let mut f = fence(); birth(&mut f);
    assert!(matches!(f.admit(source(1), invocation()), Err(Error::Conflict)));
    f.admit(source(2), invocation()).unwrap();
    let count = f.store.committed.len();
    assert!(matches!(f.admit(source(2), invocation()), Ok(Admission::Existing(State::Reserved))));
    assert_eq!(f.store.committed.len(), count);
    for field in 0..4 {
        let mut altered = invocation();
        if let Original::Invocation { context, payload, measured_plan, purpose, .. } = &mut altered {
            match field { 0 => context.actor.push(0), 1 => payload.push(0), 2 => measured_plan.push(0), _ => purpose.push(0) }
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
                    assert!(matches!(f.change(&source(2), step), Err(Error::Uncertain)));
                    assert_eq!(f.revision, rev); assert_eq!(f.records, old);
                    assert!(matches!(f.admit(source(2), invocation()), Err(Error::Unavailable)));
                    break;
                }
                f.change(&source(2), step).unwrap();
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
    for step in transitions().into_iter().take(4) { stopped.change(&source(2), step).unwrap(); }
    stopped.change(&source(2), Transition::Stop).unwrap();
    assert!(matches!(stopped.change(&source(2), Transition::FenceEffect { custody: custody(), operation: vec![] }), Err(Error::State)));
    assert_eq!(stopped.records[&source(2)].state, State::Stopped { custody: Some(custody()), cleanup: Cleanup::Pending });
    let mut consumed = reserved();
    for step in transitions().into_iter().take(5) { consumed.change(&source(2), step).unwrap(); }
    assert!(matches!(consumed.change(&source(2), Transition::Stop), Err(Error::State)));
    consumed.change(&source(2), Transition::Unknown).unwrap();
    assert!(matches!(consumed.admit(source(2), invocation()), Ok(Admission::Existing(State::Unknown { .. }))));
    assert!(matches!(consumed.change(&source(2), Transition::Spawn(custody())), Err(Error::State)));
}
#[test]
fn hidden_or_changed_custody_cannot_bind_release_complete_or_cleanup() {
    let mut f = reserved();
    let mut duplicate = custody(); duplicate.resources.push(duplicate.resources[0].clone());
    assert!(matches!(f.change(&source(2), Transition::Spawn(duplicate)), Err(Error::State)));
    f.change(&source(2), Transition::Spawn(custody())).unwrap();
    let mut missing = custody(); missing.resources.pop();
    assert!(matches!(f.change(&source(2), Transition::Bind(missing.clone())), Err(Error::State)));
    for step in transitions().into_iter().skip(1).take(4) { f.change(&source(2), step).unwrap(); }
    assert!(matches!(f.change(&source(2), Transition::Complete { operation: vec![], outcome: Outcome::Success(vec![]) }), Err(Error::State)));
    f.change(&source(2), Transition::Unknown).unwrap();
    assert!(matches!(f.change(&source(2), Transition::Cleanup { custody: missing, result: Cleanup::Closed }), Err(Error::State)));
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
