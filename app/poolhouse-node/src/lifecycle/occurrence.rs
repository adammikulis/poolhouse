//! Dormant original-occurrence transition engine, NOT a durable store or proof issuer.
//! No production constructor/acknowledgement/recovery issuer exists. A future reviewed
//! boundary must validate native provenance, protected store ownership, exclusive writer,
//! complete history and independent rollback observations before constructing anything here.
//! Same-domain hashes/revisions and implementations of Store are NOT those proofs.
use std::collections::HashMap;

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
struct Source {
    installation: [u8; 32], continuity: [u8; 32], original_launch: [u8; 32], occurrence: [u8; 32],
}
// ONE namespace across all variants. No node boot/admission, actor or variant in Source.
#[derive(Clone, Debug, PartialEq, Eq)]
struct Context { actor: Vec<u8>, root: Vec<u8>, parent: Option<Vec<u8>>, bytes: Vec<u8> }
#[derive(Clone, Debug, PartialEq, Eq)]
enum Original {
    Birth { context: Context },
    Resume { context: Context, prior: Source },
    Invocation { context: Context, admission: Source, kind: Vec<u8>, purpose: Vec<u8>,
        payload: Vec<u8>, measured_plan: Vec<u8> },
}
// These are diagnostic historical references only, never restored OS handles/proofs.
#[derive(Clone, Debug, PartialEq, Eq)]
struct Custody { owner: [u8; 32], resources: Vec<Resource> }
#[derive(Clone, Debug, PartialEq, Eq)]
struct Resource { identity: [u8; 32], action: Action }
#[derive(Clone, Debug, PartialEq, Eq)]
enum Action { CloseChannel, TerminateAndReap, ReleaseMeasurement, Preserve }
#[derive(Clone, Debug, PartialEq, Eq)]
enum Cleanup { Pending, Failed, Closed }
#[derive(Clone, Debug, PartialEq, Eq)]
enum Outcome { Success(Vec<u8>), Failure(Vec<u8>) }
// Closed progression x custody combinations: no free-floating mutable phase fields.
#[derive(Clone, Debug, PartialEq, Eq)]
enum State {
    AdmissionFenced, Admitted, Retired,
    Reserved, SpawnFenced(Custody), Bound(Custody), ReleaseFenced(Custody), Released(Custody),
    EffectFenced { custody: Custody, operation: Vec<u8> },
    Completed { custody: Custody, operation: Vec<u8>, outcome: Outcome, cleanup: Cleanup },
    Unknown { custody: Custody, operation: Vec<u8>, cleanup: Cleanup },
    Stopped { custody: Option<Custody>, cleanup: Cleanup },
}
#[derive(Clone, Debug, PartialEq, Eq)]
struct Record { original: Original, state: State }
#[derive(Clone, Debug, PartialEq, Eq)]
struct StoreBinding { store: [u8; 32], installation: [u8; 32], continuity: [u8; 32], writer: [u8; 32] }
#[derive(Clone, Debug, PartialEq, Eq)]
struct Mutation { binding: StoreBinding, source: Source, before: u64, after: u64,
    previous: Option<Record>, next: Record }
/// Store must commit the EXACT mutation atomically (including mapping/custody history),
/// with exclusive-writer CAS and all required independent witness acknowledgements.
/// Disk commit before witness failure/lost acknowledgement is Indeterminate, never NoCommit.
/// No production implementation or acknowledgement constructor is supplied.
trait Store { fn commit(&mut self, mutation: &Mutation) -> Result<Acknowledgement, CommitFailure>; }
struct Acknowledgement { exact: Mutation }
enum CommitFailure { NoCommit, Indeterminate }
#[derive(Debug, PartialEq, Eq)]
enum Error { Unavailable, Scope, Conflict, State, Exhausted, Uncertain }
// Not an Execution/permit: a non-Clone historical transition receipt, never OS authority.
struct Committed { exact: Mutation }
enum Admission { Committed(Committed), Existing(State) }
struct Fence<S> { binding: StoreBinding, revision: u64, records: HashMap<Source, Record>, store: S, usable: bool }
impl<S: Store> Fence<S> {
    fn check(&self, source: &Source) -> Result<(), Error> {
        if !self.usable { return Err(Error::Unavailable); }
        if source.installation != self.binding.installation || source.continuity != self.binding.continuity {
            return Err(Error::Scope);
        }
        Ok(())
    }
    /// Inputs come only from the future authenticated boundary. This method is NOT authentication.
    /// Duplicate dispositions must not be disclosed before that boundary's current authorization.
    fn admit(&mut self, source: Source, original: Original) -> Result<Admission, Error> {
        self.check(&source)?;
        if let Some(old) = self.records.get(&source) {
            return if old.original == original { Ok(Admission::Existing(old.state.clone())) }
                else { Err(Error::Conflict) };
        }
        match &original {
            Original::Birth { context } => {
                if self.records.iter().any(|(key, record)| key.installation == source.installation
                    && key.continuity == source.continuity && match &record.original {
                        Original::Birth { context: old } | Original::Resume { context: old, .. } => old.actor == context.actor,
                        _ => false,
                    }) { return Err(Error::Conflict); }
            },
            Original::Resume { context, prior } => {
                self.check(prior)?;
                let old = self.records.get(prior).ok_or(Error::State)?;
                let prior_context = match &old.original {
                    Original::Birth { context } | Original::Resume { context, .. } => context,
                    _ => return Err(Error::State),
                };
                if self.records.values().any(|record| matches!(&record.original,
                    Original::Resume { prior: used, .. } if used == prior)) { return Err(Error::Conflict); }
                if old.state != State::Retired || prior_context != context
                    || prior.original_launch != source.original_launch { return Err(Error::State); }
            },
            Original::Invocation { context, admission, .. } => {
                self.check(admission)?;
                let old = self.records.get(admission).ok_or(Error::State)?;
                let admitted = match &old.original {
                    Original::Birth { context } | Original::Resume { context, .. } => context,
                    _ => return Err(Error::State),
                };
                if old.state != State::Admitted || admitted != context
                    || admission.original_launch != source.original_launch { return Err(Error::State); }
            },
        }
        let state = match original { Original::Invocation { .. } => State::Reserved, _ => State::AdmissionFenced };
        self.commit(source, Record { original, state }).map(Admission::Committed)
    }
    fn commit(&mut self, source: Source, next: Record) -> Result<Committed, Error> {
        self.check(&source)?;
        let after = self.revision.checked_add(1).ok_or(Error::Exhausted)?;
        let exact = Mutation { binding: self.binding.clone(), previous: self.records.get(&source).cloned(),
            source: source.clone(), before: self.revision, after, next: next.clone() };
        // Any failed/mismatched ack invalidates the loaded snapshot. Even explicit NoCommit
        // needs fresh complete recovery before another attempt; do not replay an external action.
        self.usable = false;
        let ack = self.store.commit(&exact).map_err(|_| Error::Uncertain)?;
        if ack.exact != exact { return Err(Error::Uncertain); }
        self.records.insert(source, next); self.revision = after; self.usable = true;
        Ok(Committed { exact })
    }
    fn change(&mut self, source: &Source, event: Transition) -> Result<Committed, Error> {
        self.check(source)?;
        let old = self.records.get(source).ok_or(Error::State)?;
        // Retirement is one durable admission tombstone. Every subsequent action-right
        // transition resolves it under this coordinator/CAS revision: historical invocation
        // phases alone never imply live authority. Cleanup/completion remain possible.
        if matches!(&event, Transition::Spawn(_) | Transition::Bind(_) | Transition::FenceRelease(_)
            | Transition::Released(_) | Transition::FenceEffect { .. }) {
            if let Original::Invocation { admission, .. } = &old.original {
                if !self.records.get(admission).is_some_and(|record| record.state == State::Admitted) {
                    return Err(Error::State);
                }
            } else { return Err(Error::State); }
        }
        let state = transition(&old.state, event)?;
        self.commit(source.clone(), Record { original: old.original.clone(), state })
    }
}
// No public setters. Custody identities/obligations never disappear across transitions.
enum Transition {
    Admit, Retire, Spawn(Custody), Bind(Custody), FenceRelease(Custody), Released(Custody),
    FenceEffect { custody: Custody, operation: Vec<u8> }, Complete { operation: Vec<u8>, outcome: Outcome },
    Unknown, Stop, Cleanup { custody: Custody, result: Cleanup },
}
fn transition(old: &State, event: Transition) -> Result<State, Error> {
    use State::*;
    Ok(match (old, event) {
        (AdmissionFenced, Transition::Admit) => Admitted,
        (AdmissionFenced | Admitted, Transition::Retire) => Retired,
        (Reserved, Transition::Spawn(c)) if valid_custody(&c) => SpawnFenced(c),
        (SpawnFenced(c), Transition::Bind(actual)) if c == &actual => Bound(actual),
        (Bound(c), Transition::FenceRelease(actual)) if c == &actual => ReleaseFenced(actual),
        (ReleaseFenced(c), Transition::Released(actual)) if c == &actual => Released(actual),
        (Released(c), Transition::FenceEffect { custody, operation }) if c == &custody =>
            EffectFenced { custody, operation },
        (EffectFenced { custody, operation }, Transition::Complete { operation: actual, outcome }) if operation == &actual =>
            Completed { custody: custody.clone(), operation: operation.clone(), outcome, cleanup: Cleanup::Pending },
        (EffectFenced { custody, operation }, Transition::Unknown) =>
            Unknown { custody: custody.clone(), operation: operation.clone(), cleanup: Cleanup::Pending },
        (Completed { operation, outcome, .. }, Transition::Complete { operation: actual, outcome: result })
            if operation == &actual && outcome == &result => return Err(Error::State), // no new receipt on retry
        (Reserved, Transition::Stop) => Stopped { custody: None, cleanup: Cleanup::Closed },
        (SpawnFenced(c) | Bound(c) | ReleaseFenced(c) | Released(c), Transition::Stop) =>
            Stopped { custody: Some(c.clone()), cleanup: Cleanup::Pending },
        (Stopped { custody: Some(c), cleanup }, Transition::Cleanup { custody, result }) if c == &custody && *cleanup != Cleanup::Closed =>
            Stopped { custody: Some(custody), cleanup: result },
        (Completed { custody: c, operation, outcome, cleanup }, Transition::Cleanup { custody, result })
            if c == &custody && *cleanup != Cleanup::Closed => Completed { custody, operation: operation.clone(), outcome: outcome.clone(), cleanup: result },
        (Unknown { custody: c, operation, cleanup }, Transition::Cleanup { custody, result })
            if c == &custody && *cleanup != Cleanup::Closed => Unknown { custody, operation: operation.clone(), cleanup: result },
        _ => return Err(Error::State),
    })
}
fn valid_custody(c: &Custody) -> bool {
    // No identity may appear twice under different obligations, including Preserve.
    !c.resources.is_empty() && c.resources.iter().enumerate()
        .all(|(i, r)| !c.resources[..i].iter().any(|other| other.identity == r.identity))
}
// Recovery is deliberately not implemented: deserializing records cannot prove complete
// monotonic history or native continuity. Test-only construction is in occurrence_tests.
#[cfg(test)]
#[path = "occurrence_tests.rs"]
mod tests;
