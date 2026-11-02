//! Provenance boundary: intentionally no production issuer, conversion, or native implementation.
//! Test issuers below exercise state logic only; they do not authenticate native callers.
//! A future native issuer MUST canonicalize retained lifetimes by actual kernel process identity.
//! Arc identity here is an internal object check, never a substitute for OS provenance.
use super::{Binding, CleanupPhase, Context, Key, Placement};
use super::event::Admission;
use std::sync::Arc;

pub(super) trait Lifetime: Send + Sync { fn live(&self) -> bool; }
pub(super) struct Process { lifetime: Box<dyn Lifetime> }
impl Process { pub(super) fn live(&self) -> bool { self.lifetime.live() } }

pub(super) struct ProducerBody {
    pub admission: Key, pub installation: [u8; 32], pub launch_epoch: [u8; 32], pub process: Arc<Process>,
}
pub(super) struct Producer { body: ProducerBody }
impl Producer { pub(super) fn body(&self) -> &ProducerBody { &self.body } }

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Origin { Birth, Resume(Key) }
pub(super) struct SessionEventBody {
    pub producer: Arc<Producer>, pub origin: Origin, pub stream_event_id: [u8; 32],
    pub identity: String, pub context: Vec<u8>, pub placement: Placement,
}
pub(super) struct SessionEvent { body: SessionEventBody }
impl SessionEvent { pub(super) fn body(&self) -> &SessionEventBody { &self.body } }

pub(super) struct InvocationEventBody {
    pub producer: Arc<Producer>, pub session: Key, pub admission: Admission, pub context: Context,
    pub stream_event_id: [u8; 32], pub kind: String, pub purpose: String, pub payload: Vec<u8>, pub spawn_plan: Vec<u8>,
}
pub(super) struct InvocationEvent { body: InvocationEventBody }
impl InvocationEvent { pub(super) fn body(&self) -> &InvocationEventBody { &self.body } }

pub(super) struct HeldBody {
    pub binding: Binding, pub producer: Arc<Producer>, pub process: Arc<Process>, pub holder: Arc<Process>,
    pub completion_owner: Arc<Process>,
}
pub(super) struct Held { body: HeldBody }
impl Held { pub(super) fn body(&self) -> &HeldBody { &self.body } }

pub(super) struct Caller { binding: Binding, process: Arc<Process> }
impl Caller {
    pub(super) fn binding(&self) -> &Binding { &self.binding }
    pub(super) fn process(&self) -> &Arc<Process> { &self.process }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum CleanupOutcome { Closed, Failed }
pub(super) struct Cleanup {
    binding: Binding, process: Arc<Process>, holder: Arc<Process>, phase: CleanupPhase, outcome: CleanupOutcome,
}
impl Cleanup {
    pub(super) fn binding(&self) -> &Binding { &self.binding }
    pub(super) fn process(&self) -> &Arc<Process> { &self.process }
    pub(super) fn holder(&self) -> &Arc<Process> { &self.holder }
    pub(super) fn phase(&self) -> CleanupPhase { self.phase }
    pub(super) fn outcome(&self) -> CleanupOutcome { self.outcome }
}

pub(super) struct Completion {
    binding: Binding, permit: Key, owner: Arc<Process>, result: Vec<u8>,
}
impl Completion {
    pub(super) fn binding(&self) -> &Binding { &self.binding }
    pub(super) fn permit(&self) -> Key { self.permit }
    pub(super) fn owner(&self) -> &Arc<Process> { &self.owner }
    pub(super) fn result(&self) -> &[u8] { &self.result }
}

#[cfg(test)]
pub(super) mod testing {
    use super::*;
    use std::sync::atomic::{AtomicBool, Ordering};
    struct TestLifetime(Arc<AtomicBool>);
    impl Lifetime for TestLifetime { fn live(&self) -> bool { self.0.load(Ordering::SeqCst) } }
    pub fn process() -> (Arc<Process>, Arc<AtomicBool>) {
        let live = Arc::new(AtomicBool::new(true));
        (Arc::new(Process { lifetime: Box::new(TestLifetime(live.clone())) }), live)
    }
    pub fn producer(registry: &super::super::Registry) -> (Arc<Producer>, Arc<AtomicBool>) { producer_for(registry, [1; 32]) }
    pub fn producer_for(registry: &super::super::Registry, installation: [u8; 32]) -> (Arc<Producer>, Arc<AtomicBool>) {
        let (process, live) = process();
        let admission = registry.lock().unwrap().allocate().unwrap();
        (Arc::new(Producer { body: ProducerBody { admission, installation, launch_epoch: [2; 32], process } }), live)
    }
    pub fn event(body: SessionEventBody) -> SessionEvent { SessionEvent { body } }
    pub fn invocation(body: InvocationEventBody) -> InvocationEvent { InvocationEvent { body } }
    pub fn readmit(registry: &super::super::Registry, prior: &Arc<Producer>) -> Arc<Producer> {
        let old = prior.body();
        let admission = registry.lock().unwrap().allocate().unwrap();
        Arc::new(Producer { body: ProducerBody { admission, installation: old.installation,
            launch_epoch: old.launch_epoch, process: old.process.clone() } })
    }
    pub fn held(body: HeldBody) -> Held { Held { body } }
    pub fn caller(binding: Binding, process: Arc<Process>) -> Caller { Caller { binding, process } }
    pub fn cleanup(binding: Binding, process: Arc<Process>, holder: Arc<Process>, phase: CleanupPhase, outcome: CleanupOutcome) -> Cleanup {
        Cleanup { binding, process, holder, phase, outcome }
    }
    pub fn completion(binding: Binding, permit: Key, owner: Arc<Process>, result: &[u8]) -> Completion {
        Completion { binding, permit, owner, result: result.to_vec() }
    }
}
