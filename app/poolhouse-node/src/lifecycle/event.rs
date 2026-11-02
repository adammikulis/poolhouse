//! Stream message IDs must be globally unique across Birth/Resume/Invocation in one launch.
//! They identify original native event occurrences; retransmission MUST retain the same ID.
//! They are NOT transport-attempt or upstream actor-scoped turn/call IDs; no real issuer exists yet.
use super::*;
use proof::InvocationEvent;
use sha2::{Digest, Sha256};

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub(super) struct EventOrigin {
    pub producer: Key, pub installation: [u8; 32], pub launch_epoch: [u8; 32],
    pub actor: String, pub stream_event_id: [u8; 32],
}
impl EventOrigin {
    pub(super) fn new(producer: &Arc<Producer>, actor: &str, stream_event_id: [u8; 32]) -> Self {
        let body = producer.body();
        Self { producer: body.admission, installation: body.installation, launch_epoch: body.launch_epoch,
            actor: actor.into(), stream_event_id }
    }
    pub(super) fn stream(&self) -> StreamEvent {
        StreamEvent { producer: self.producer, installation: self.installation,
            launch_epoch: self.launch_epoch, stream_event_id: self.stream_event_id }
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub(super) struct StreamEvent {
    producer: Key, installation: [u8; 32], launch_epoch: [u8; 32], stream_event_id: [u8; 32],
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct Admission { pub source: EventOrigin, pub origin: Origin }
#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct NativeInvocation {
    pub source: EventOrigin, pub session: Key, pub admission: Admission, pub kind: String,
}
pub(super) enum EventRecord { Admission, Invocation(Key) }

impl Registry {
    pub(super) fn reserve(&self, owner: &SessionRight, event: InvocationEvent) -> Result<Reservation, Error> {
        let mut store = self.lock()?;
        let body = event.body();
        let session = store.live(owner.key)?;
        // Validate every source/context/generation field BEFORE revealing a ledger disposition.
        store.check_producer(&body.producer)?;
        if !Arc::ptr_eq(&body.producer, &session.producer) || body.session != owner.key
            || body.admission != session.admission || body.context != session.context { return Err(Error::Mismatch); }
        let context = session.context.clone();
        let installation = session.producer.body().installation;
        let launch_epoch = session.producer.body().launch_epoch;
        let source = EventOrigin::new(&body.producer, &context.identity, body.stream_event_id);
        let native = NativeInvocation { source: source.clone(), session: owner.key,
            admission: session.admission.clone(), kind: body.kind.clone() };
        let request = Request { event: native, purpose: body.purpose.clone(),
            digest: Sha256::digest(&body.payload).into(), spawn_plan: body.spawn_plan.clone() };
        if let Some(record) = store.events.get(&source.stream()) {
            if let EventRecord::Invocation(key) = record {
                let old = &store.invocations.get(key).ok_or(Error::Stale)?.binding;
                if old.session == owner.key && old.context == context && old.request == request
                    && old.installation == installation && old.launch_epoch == launch_epoch {
                    return Err(Error::AlreadyReserved);
                }
            }
            return Err(Error::Mismatch);
        }
        let key = store.allocate()?;
        let binding = Binding { invocation: key, session: owner.key, context, request, installation, launch_epoch };
        store.events.insert(source.stream(), EventRecord::Invocation(key));
        store.invocations.insert(key, Invocation { binding, _event: event, state: InvocationState::Reserved, held: None,
            cleanup: CleanupState::Pending, cleanup_phase: CleanupPhase::BeforeConsume, permit: None, result: None });
        Ok(Reservation { key })
    }
}
impl Store {
    fn source_matches(left: &Producer, right: &Producer) -> bool {
        let (a, b) = (left.body(), right.body());
        a.installation == b.installation && a.launch_epoch == b.launch_epoch && Arc::ptr_eq(&a.process, &b.process)
    }
    pub(super) fn register_producer(&mut self, producer: &Arc<Producer>) -> Result<(), Error> {
        let body = producer.body();
        if body.admission.boot != self.boot || !body.process.live() { return Err(Error::Stale); }
        for prior in &self.producers {
            let same_source = Self::source_matches(prior, producer);
            let same_admission = prior.body().admission == body.admission;
            // A retained actual process lifetime cannot be relabelled as a new installation/launch.
            let same_process = Arc::ptr_eq(&prior.body().process, &body.process);
            if same_source != same_admission || (same_process && !same_source) { return Err(Error::Origin); }
            if same_source { return Ok(()); }
        }
        self.producers.push(producer.clone());
        Ok(())
    }
    pub(super) fn check_producer(&self, producer: &Arc<Producer>) -> Result<(), Error> {
        let body = producer.body();
        if body.admission.boot != self.boot || !body.process.live() { return Err(Error::Stale); }
        if !self.producers.iter().any(|prior| prior.body().admission == body.admission
            && Self::source_matches(prior, producer)) { return Err(Error::Origin); }
        Ok(())
    }
}
