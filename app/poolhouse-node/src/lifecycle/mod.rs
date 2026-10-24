//! Dormant common H state core. Sealed native provenance has no production issuer.
//! This module neither admits wire callers nor closes channels or terminates processes.
//! Resume is deliberately restricted to the SAME admitted producer/process instance.
//! Replacement-producer or replacement-process continuity has no proof interface in this increment.
mod source_context;
#[cfg(test)] mod source_context_tests;
mod occurrence;
mod invocation;
mod event;
mod proof;
#[cfg(test)] mod tests;
use proof::{Origin, Producer, SessionEvent};
use std::collections::{HashMap, HashSet};
use event::{Admission, EventOrigin, EventRecord};
use std::sync::{Arc, Mutex, MutexGuard};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct Key { boot: [u8; 32], generation: u64 }
#[derive(Clone, Debug, PartialEq, Eq)]
enum Placement { Root, Child { root: Key, parent: Key } }
#[derive(Clone, Debug, PartialEq, Eq)]
struct Context { identity: String, data: Vec<u8>, root: Key, parent: Option<Key> }
#[derive(Clone, Debug, PartialEq, Eq)]
struct Request { event: event::NativeInvocation, purpose: String, digest: [u8; 32], spawn_plan: Vec<u8> }
#[derive(Clone, Debug, PartialEq, Eq)]
struct Binding {
    invocation: Key, session: Key, context: Context, request: Request,
    installation: [u8; 32], launch_epoch: [u8; 32],
}
#[derive(Debug, PartialEq, Eq)]
enum Error { Entropy, Exhausted, Poisoned, Stale, Graph, Origin, Mismatch, State, Cleanup, Pending, AlreadyReserved }
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum SessionState { Opening, Live, Retired }
struct Session { state: SessionState, context: Context, producer: Arc<Producer>,
    admission: Admission, _event: SessionEvent }
struct SessionRight { key: Key }
struct Reservation { key: Key }
/// The responsible holder retains its rejected child; failure never silently drops that duty.
#[must_use]
struct BindFailure { error: Error, held: proof::Held }
/// Exactly one is issued at consume's terminal transition. Dropping it never permits retry.
struct Execution { invocation: Key, permit: Key }
/// A request to the future responsible holder; registry revocation is not OS cleanup.
struct Termination { invocation: Key, binding: Binding, held: Arc<proof::Held> }
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum CleanupState { Pending, Closed, Failed }
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum CleanupPhase { BeforeConsume, Terminal }
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum InvocationState { Reserved, Bound, Released, Consumed, Cancelled, Invalidated }
struct Invocation {
    binding: Binding, _event: proof::InvocationEvent, state: InvocationState, held: Option<Arc<proof::Held>>,
    cleanup: CleanupState, cleanup_phase: CleanupPhase, permit: Option<Key>, result: Option<Vec<u8>>,
}
struct Store {
    boot: [u8; 32], next: u64, sessions: HashMap<Key, Session>, identities: HashMap<([u8; 32], String), Key>,
    producers: Vec<Arc<Producer>>, events: HashMap<event::StreamEvent, EventRecord>, invocations: HashMap<Key, Invocation>,
}
struct Registry { store: Mutex<Store> }
impl Registry {
    fn new() -> Result<Self, Error> {
        let mut boot = [0; 32];
        getrandom::fill(&mut boot).map_err(|_| Error::Entropy)?;
        Ok(Self { store: Mutex::new(Store { boot, next: 0, sessions: HashMap::new(),
            identities: HashMap::new(), producers: Vec::new(), events: HashMap::new(), invocations: HashMap::new() }) })
    }
    fn lock(&self) -> Result<MutexGuard<'_, Store>, Error> { self.store.lock().map_err(|_| Error::Poisoned) }
    fn open(&self, event: SessionEvent) -> Result<SessionRight, Error> {
        let mut store = self.lock()?;
        let body = event.body();
        store.register_producer(&body.producer)?;
        let source = EventOrigin::new(&body.producer, &body.identity, body.stream_event_id);
        if store.events.contains_key(&source.stream()) { return Err(Error::Origin); }
        let identity = (body.producer.body().installation, body.identity.clone());
        match (body.origin, store.identities.get(&identity).copied()) {
            (Origin::Birth, None) => (),
            (Origin::Resume(previous), Some(current)) if previous == current => {
                let old = store.sessions.get(&previous).ok_or(Error::Stale)?;
                if old.state != SessionState::Retired || !Arc::ptr_eq(&old.producer, &body.producer)
                    || old.context.data != body.context {
                    return Err(Error::Origin);
                }
                store.resume_placement(old, &body.placement)?;
            },
            _ => return Err(Error::Origin),
        }
        store.placement(&body.placement, &body.producer)?;
        let key = store.allocate()?;
        let (root, parent) = match body.placement { Placement::Root => (key, None),
            Placement::Child { root, parent } => (root, Some(parent)) };
        let context = Context { identity: body.identity.clone(), data: body.context.clone(), root, parent };
        let admission = Admission { source: source.clone(), origin: body.origin };
        store.events.insert(source.stream(), EventRecord::Admission);
        store.identities.insert(identity, key);
        let producer = body.producer.clone();
        store.sessions.insert(key, Session { state: SessionState::Opening, context, producer, admission, _event: event });
        Ok(SessionRight { key })
    }
    fn admit(&self, right: &SessionRight) -> Result<(), Error> {
        let mut store = self.lock()?;
        let session = store.sessions.get(&right.key).ok_or(Error::Stale)?;
        if session.state != SessionState::Opening { return Err(Error::State); }
        if !session.producer.body().process.live() { return Err(Error::Stale); }
        if let Some(parent) = session.context.parent {
            store.placement(&Placement::Child { root: session.context.root, parent }, &session.producer)?;
        }
        store.sessions.get_mut(&right.key).unwrap().state = SessionState::Live;
        Ok(())
    }

}
impl Store {
    fn allocate(&mut self) -> Result<Key, Error> {
        self.next = self.next.checked_add(1).ok_or(Error::Exhausted)?;
        Ok(Key { boot: self.boot, generation: self.next })
    }
    fn live(&self, key: Key) -> Result<&Session, Error> {
        if key.boot != self.boot { return Err(Error::Stale); }
        let session = self.sessions.get(&key).ok_or(Error::Stale)?;
        if session.state != SessionState::Live || !session.producer.body().process.live() { return Err(Error::Stale); }
        if let Some(parent) = session.context.parent {
            let parent = self.live(parent)?;
            if parent.context.root != session.context.root || !Arc::ptr_eq(&parent.producer, &session.producer) {
                return Err(Error::Graph);
            }
        } else if session.context.root != key { return Err(Error::Graph); }
        Ok(session)
    }
    fn resume_placement(&self, previous: &Session, placement: &Placement) -> Result<(), Error> {
        match (previous.context.parent, placement) {
            (None, Placement::Root) => Ok(()),
            (Some(old_parent), Placement::Child { root, parent }) => {
                let old_root = self.sessions.get(&previous.context.root).ok_or(Error::Graph)?;
                let old_parent = self.sessions.get(&old_parent).ok_or(Error::Graph)?;
                if self.live(*root)?.context.identity != old_root.context.identity
                    || self.live(*parent)?.context.identity != old_parent.context.identity { return Err(Error::Graph); }
                Ok(())
            },
            _ => Err(Error::Graph),
        }
    }
    fn placement(&self, placement: &Placement, producer: &Arc<Producer>) -> Result<(), Error> {
        if let Placement::Child { root, parent } = *placement {
            let actual_parent = self.live(parent)?;
            let actual_root = self.live(root)?;
            if actual_parent.context.root != root || actual_root.context.parent.is_some()
                || actual_root.context.root != root || !Arc::ptr_eq(&actual_parent.producer, producer)
                || !Arc::ptr_eq(&actual_root.producer, producer) { return Err(Error::Graph); }
        }
        Ok(())
    }
}
