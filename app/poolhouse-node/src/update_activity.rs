//! Local writer accounting only. Other sources remain uncovered; no production drain
//! controller, complete inventory, quiescence result or cutover witness exists here.
//! Before enabling a controller, nested admitted network operations need reviewed
//! same-operation continuation: another new_effect check must not spend approval twice.
use std::sync::{Arc, Mutex, MutexGuard};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Source { LocalApi, Network, Peer, Sync, Beacon, Phone, Push, Shard, LeaseScheduler, DirectNode, DetachedStorage }
const UNCOVERED: &[Source] = &[Source::Network, Source::Peer, Source::Sync, Source::Beacon,
    Source::Phone, Source::Push, Source::Shard, Source::LeaseScheduler, Source::DirectNode, Source::DetachedStorage];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Phase { Running, Draining, Fenced }
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Activity { Maintenance, NewEffect }
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Refusal { Unavailable, NewWorkPaused, Fenced, ActiveWriters, IncompleteInventory }
struct State { phase: Phase, writers: usize, local_writers: usize, connections: usize }

#[derive(Clone)]
pub(crate) struct Registry { state: Arc<Mutex<State>> }

/// An idle connection is an owned handler lifetime, not an active storage writer.
pub(crate) struct Connection { state: Arc<Mutex<State>> }
/// Not cloneable: every admitted local mutation holds exactly one counted guard.
pub(crate) struct Writer { state: Arc<Mutex<State>>, source: Source }

// No production issuer. A locator, JSON value, UID or counter is not controller authority.
enum ControlWitness { #[cfg(test)] TestOnly }

impl Registry {
    #[cfg(test)]
    pub(crate) fn local_writer_count(&self) -> usize { self.state.lock().unwrap().local_writers }
    pub(crate) fn new() -> Self {
        Self { state: Arc::new(Mutex::new(State { phase: Phase::Running, writers: 0, local_writers: 0, connections: 0 })) }
    }
    pub(crate) fn local_writer(&self) -> Result<Writer, Refusal> {
        // New effects use this gate; typed API maintenance uses local_maintenance. Both
        // include read-side gov_tick/names.touch; production stays Running.
        self.enter(Source::LocalApi, Activity::NewEffect)
    }
    pub(crate) fn maintenance(&self, source: Source) -> Result<Writer, Refusal> { self.enter(source, Activity::Maintenance) }
    pub(crate) fn new_effect(&self, source: Source) -> Result<Writer, Refusal> { self.enter(source, Activity::NewEffect) }
    #[cfg(test)]
    pub(crate) fn active_writer_count_for_test(&self) -> usize { self.state.lock().unwrap().writers }
    pub(crate) fn local_maintenance(&self) -> Result<Writer, Refusal> { self.enter(Source::LocalApi, Activity::Maintenance) }
    pub(crate) fn lease_writer(&self) -> Result<Writer, Refusal> { self.enter(Source::LeaseScheduler, Activity::Maintenance) }
    pub(crate) fn reserve_grants(&self) -> Result<GrantReservation<'_>, Refusal> {
        let state=self.state.lock().map_err(|_| Refusal::Unavailable)?;
        if state.phase == Phase::Fenced { return Err(Refusal::Fenced); }
        Ok(GrantReservation { state })
    }
    #[cfg(test)]
    pub(crate) fn phase_available_for_test(&self) -> bool { self.state.try_lock().is_ok() }
    #[cfg(test)]
    pub(crate) fn drain_for_test(&self) { self.begin_drain(ControlWitness::TestOnly).unwrap(); }
    fn enter(&self, source: Source, activity: Activity) -> Result<Writer, Refusal> {
        let mut s=self.state.lock().map_err(|_| Refusal::Unavailable)?;
        match s.phase {
            Phase::Fenced => return Err(Refusal::Fenced),
            Phase::Draining if activity == Activity::NewEffect => return Err(Refusal::NewWorkPaused),
            _ => {},
        }
        s.writers=s.writers.checked_add(1).ok_or(Refusal::Unavailable)?;
        if source == Source::LocalApi { s.local_writers+=1; }
        Ok(Writer { state:self.state.clone(), source })
    }
    pub(crate) fn connection(&self) -> Result<Connection, Refusal> {
        let mut s=self.state.lock().map_err(|_| Refusal::Unavailable)?;
        if s.phase == Phase::Fenced { return Err(Refusal::Fenced); }
        s.connections=s.connections.checked_add(1).ok_or(Refusal::Unavailable)?;
        Ok(Connection { state:self.state.clone() })
    }
    fn begin_drain(&self, witness: ControlWitness) -> Result<(), Refusal> {
        match witness {
            #[cfg(test)] ControlWitness::TestOnly => {
                let mut s=self.state.lock().map_err(|_| Refusal::Unavailable)?;
                if s.phase == Phase::Fenced { return Err(Refusal::Fenced); }
                s.phase=Phase::Draining; Ok(())
            }
        }
    }
    fn try_fence(&self) -> Result<(), Refusal> {
        let s=self.state.lock().map_err(|_| Refusal::Unavailable)?;
        if s.writers != 0 { return Err(Refusal::ActiveWriters); }
        // A zero counter can NEVER certify missing peer/background/storage writers.
        // There is deliberately no production coverage-completion switch or Ready value.
        let _missing=UNCOVERED;
        Err(Refusal::IncompleteInventory)
    }
}

/// The same phase mutex is held only across pure table mutation; never clock/I/O/mirroring.
pub(crate) struct GrantReservation<'a> { state: MutexGuard<'a, State> }
impl GrantReservation<'_> {
    pub(crate) fn allows_compute(&self) -> bool { self.state.phase == Phase::Running }
}

impl Drop for Writer {
    fn drop(&mut self) {
        if let Ok(mut s)=self.state.lock() {
            // A poisoned/inconsistent registry never becomes evidence of an empty writer set.
            if self.source == Source::LocalApi { s.local_writers=s.local_writers.saturating_sub(1); }
            if let Some(left)=s.writers.checked_sub(1) { s.writers=left; }
            else { s.phase=Phase::Fenced; }
        }
        let _source=self.source;
    }
}
impl Drop for Connection {
    fn drop(&mut self) {
        if let Ok(mut s)=self.state.lock() {
            if let Some(left)=s.connections.checked_sub(1) { s.connections=left; }
            else { s.phase=Phase::Fenced; }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Barrier;
    #[test]
    fn local_read_side_writers_are_counted_until_guard_drops() {
        let r=Registry::new(); let w=r.local_writer().unwrap();
        assert_eq!(r.try_fence(),Err(Refusal::ActiveWriters)); drop(w);
        assert_eq!(r.try_fence(),Err(Refusal::IncompleteInventory));
    }
    #[test]
    fn idle_connections_are_not_work_and_keep_separate_inventory() {
        let r=Registry::new(); let c=r.connection().unwrap();
        assert_eq!(r.state.lock().unwrap().connections,1);
        assert_eq!(r.state.lock().unwrap().writers,0);
        assert_eq!(r.try_fence(),Err(Refusal::IncompleteInventory)); drop(c);
        assert_eq!(r.state.lock().unwrap().connections,0);
    }
    #[test]
    fn provisional_maintenance_preserves_existing_work_during_drain() {
        let r=Registry::new(); r.begin_drain(ControlWitness::TestOnly).unwrap();
        let w=r.enter(Source::LocalApi,Activity::Maintenance).unwrap();
        assert!(matches!(r.local_writer(),Err(Refusal::NewWorkPaused)));
        assert_eq!(r.try_fence(),Err(Refusal::ActiveWriters)); drop(w);
        assert_eq!(r.try_fence(),Err(Refusal::IncompleteInventory));
    }
    #[test]
    fn drain_and_registration_share_one_atomic_gate() {
        for _ in 0..32 {
            let r=Registry::new(); let other=r.clone(); let b=Arc::new(Barrier::new(2)); let bc=b.clone();
            let join=std::thread::spawn(move || { bc.wait(); other.local_writer() });
            b.wait(); r.begin_drain(ControlWitness::TestOnly).unwrap();
            match join.join().unwrap() {
                Ok(w) => { assert_eq!(r.try_fence(),Err(Refusal::ActiveWriters)); drop(w); },
                Err(e) => assert_eq!(e,Refusal::NewWorkPaused),
            }
            assert_eq!(r.try_fence(),Err(Refusal::IncompleteInventory));
        }
    }
    #[test]
    fn error_and_unwind_release_only_their_owned_activity() {
        let r=Registry::new();
        let error=|| -> Result<(),()> { let _w=r.local_writer().unwrap(); Err(()) };
        assert_eq!(error(),Err(())); assert_eq!(r.state.lock().unwrap().writers,0);
        let copy=r.clone();
        assert!(std::thread::spawn(move || { let _w=copy.local_writer().unwrap(); panic!("owned test unwind"); }).join().is_err());
        assert_eq!(r.state.lock().unwrap().writers,0);
        assert_eq!(r.try_fence(),Err(Refusal::IncompleteInventory));
    }
    #[test]
    fn dropping_registry_does_not_drop_live_guard_custody() {
        let r=Registry::new(); let observed=r.clone(); let w=r.local_writer().unwrap(); drop(r);
        assert_eq!(observed.try_fence(),Err(Refusal::ActiveWriters)); drop(w);
        assert_eq!(observed.try_fence(),Err(Refusal::IncompleteInventory));
    }
}
