//! Map-owned child custody dependency. No host launch path, settlement/removal API,
//! drain controller or readiness proof is enabled here. Service/process-death recovery
//! and watcher retention of the map owner remain host-integration obligations.
use std::collections::BTreeMap;
use std::process::{Child, Command, ExitStatus};
use std::sync::{Arc, Mutex, MutexGuard, atomic::{AtomicBool, Ordering}};

use crate::error::{Error, Result};
use crate::lease::types::{Resource, State, LOCAL};
use crate::node::{Node, update_activity::{AdmittedEffect, EffectFlow, Registry, Source}};
use crate::poolops::POOL_BOARD;

pub(super) struct LiveWorkers {
    node: Arc<Mutex<Node>>,
    registry: Registry,
    slots: Mutex<BTreeMap<String, Arc<Slot>>>,
}
struct LeaseBinding { id: String, holder: String }
struct Slot {
    lease: LeaseBinding,
    unknown: AtomicBool,
    process: Mutex<Process>,
    _parent: AdmittedEffect,
    _node: Arc<Mutex<Node>>, // Last: storage lifetime outlives every retained process and activity field.
}
struct Process { attempted: bool, child: Option<Child>, exit: Option<ExitStatus> }

/// The only launch entry: produced after actual registration in the owner map.
pub(super) struct LaunchTicket { owner: Arc<LiveWorkers>, slot: Arc<Slot> }
/// Watchers must retain this owner through actual wait and host settlement.
pub(super) struct WorkerHandle { owner: Arc<LiveWorkers>, slot: Arc<Slot> }
pub(super) struct LaunchFailure { pub error: Error, pub custody: WorkerHandle }
#[derive(Debug)]
pub(super) enum ProcessObservation { Prepared, Running(u32), Reaped(ExitStatus), NoHandleAfterAttempt }
#[derive(Debug)]
pub(super) struct Observation { pub process: ProcessObservation, pub unknown: bool }

impl Slot {
    fn process(&self) -> MutexGuard<'_, Process> {
        match self.process.lock() {
            Ok(held) => held,
            Err(poisoned) => {
                self.unknown.store(true, Ordering::SeqCst);
                poisoned.into_inner() // Preserve access to the actual owned Child; never substitute an empty slot.
            }
        }
    }
}
impl LiveWorkers {
    pub(super) fn new(node: Arc<Mutex<Node>>) -> Result<Arc<Self>> {
        let registry=node.lock().map_err(|_| Error::Damaged("node poisoned".into()))?.activity.clone();
        Ok(Arc::new(Self { node, registry, slots:Mutex::new(BTreeMap::new()) }))
    }
    pub(super) fn prepare(self: &Arc<Self>, node: &Node, job: &str, lease: &str,
        parent: AdmittedEffect) -> Result<LaunchTicket> {
        if !super::spec::valid_id(job) { return Err(Error::Invalid("invalid worker job identity".into())); }
        let binding={
            let _same=self.registry.continue_effect(&parent,Source::Shard,EffectFlow::ShardStart)
                .map_err(|_| Error::Denied("worker admission belongs to another operation".into()))?;
            let _node=node.activity.continue_effect(&parent,Source::Shard,EffectFlow::ShardStart)
                .map_err(|_| Error::Denied("worker node does not match its owner".into()))?;
            let held=node.leases.table.find(lease).filter(|l| l.state==State::Held && l.board==POOL_BOARD
                && l.name==format!("shard-{}",&job[..8]) && !l.resources.is_empty()
                && l.resources.iter().all(|r| matches!(r,Resource::CpuSlots{device,..} if device==LOCAL) && r.check().is_ok()))
                .ok_or_else(|| Error::Denied("worker requires its actual held CPU lease".into()))?;
            LeaseBinding { id:held.id.clone(), holder:held.holder.clone() }
        };
        let mut slots=self.slots.lock().map_err(|_| Error::Damaged("worker owner map needs recovery".into()))?;
        if slots.contains_key(job) { return Err(Error::Denied("worker job already has retained custody".into())); }
        let slot=Arc::new(Slot { _node:self.node.clone(), _parent:parent, lease:binding,
            unknown:AtomicBool::new(false), process:Mutex::new(Process { attempted:false,child:None,exit:None }) });
        slots.insert(job.into(),slot.clone());
        Ok(LaunchTicket { owner:self.clone(), slot })
    }
    pub(super) fn get(self: &Arc<Self>, job: &str) -> Option<WorkerHandle> {
        let slots=match self.slots.lock() {
            Ok(held) => held,
            Err(poisoned) => {
                let held=poisoned.into_inner();
                for slot in held.values() { slot.unknown.store(true,Ordering::SeqCst); }
                held
            }
        };
        slots.get(job).map(|slot| WorkerHandle { owner:self.clone(), slot:slot.clone() })
    }
}
impl LaunchTicket {
    pub(super) fn spawn(self, command: &mut Command) -> std::result::Result<WorkerHandle, LaunchFailure> {
        let handle=WorkerHandle { owner:self.owner, slot:self.slot };
        let result={
            // This is the envelope mutex, never the admission phase mutex or Node mutex.
            // No fallible metadata or watcher callback may run between OS spawn and installation.
            let mut process=handle.slot.process();
            if handle.slot.unknown.load(Ordering::SeqCst) || process.attempted {
                Err(Error::Denied("worker launch state needs recovery".into()))
            } else {
                process.attempted=true;
                match command.spawn() {
                    Ok(child) => { process.child=Some(child); Ok(()) },
                    Err(error) => {
                        handle.slot.unknown.store(true,Ordering::SeqCst);
                        Err(Error::Io(error)) // Retain binding; lack of a handle is not proof no process started.
                    }
                }
            }
        };
        match result { Ok(()) => Ok(handle), Err(error) => Err(LaunchFailure { error, custody:handle }) }
    }
}
impl WorkerHandle {
    pub(super) fn observe(&self) -> Result<Observation> {
        let mut process=self.slot.process();
        if process.exit.is_none() {
            if let Some(child)=process.child.as_mut() {
                match child.try_wait() {
                    Ok(exit) => process.exit=exit,
                    Err(error) => { self.slot.unknown.store(true,Ordering::SeqCst); return Err(error.into()); }
                }
            }
        }
        let observed=match process.exit {
            Some(exit) => ProcessObservation::Reaped(exit),
            None => match process.child.as_ref() {
                Some(child) => ProcessObservation::Running(child.id()),
                None if process.attempted => ProcessObservation::NoHandleAfterAttempt,
                None => ProcessObservation::Prepared,
            }
        };
        Ok(Observation { process:observed, unknown:self.slot.unknown.load(Ordering::SeqCst) })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lease::{table::Foreign, types::{Request,Class}};
    use crate::shard::host::Shards;
    fn setup() -> (tempfile::TempDir,Arc<Mutex<Node>>,Arc<Shards>,String,String) {
        let dir=tempfile::tempdir().unwrap(); let node=Arc::new(Mutex::new(Node::open(dir.path()).unwrap()));
        let shards=Shards::new(node.clone(),dir.path(),Arc::new(AtomicBool::new(false))).unwrap();
        let job="1".repeat(32); let lease="owned-worker-fixture".to_string();
        {
            let mut held=node.lock().unwrap(); held.leases.clock=Box::new(||1000);
            let request=Request { board:POOL_BOARD.into(),name:format!("shard-{}",&job[..8]),
                resources:vec![Resource::CpuSlots { device:LOCAL.into(),count:1 }],class:Class::Interactive,
                estimate_s:None,ttl_s:Some(60),pid:0,remote:true,local:true,wait:true,tag:String::new(),quiet:false };
            held.leases.enqueue(1000,&request,(lease.clone(),0)).unwrap(); held.leases.tick(&Foreign::new()).unwrap();
        }
        (dir,node,shards,job,lease)
    }
    fn parent(node: &Arc<Mutex<Node>>, flow: EffectFlow) -> AdmittedEffect {
        node.lock().unwrap().activity.admit_effect(Source::Shard,flow).unwrap()
    }
    #[test]
    fn prepare_refuses_wrong_flow_lease_and_duplicate_without_replacing_custody() {
        let (_dir,node,shards,job,lease)=setup();
        let foreign=Registry::new().admit_effect(Source::Shard,EffectFlow::ShardStart).unwrap();
        assert!(shards.live_workers.prepare(&node.lock().unwrap(),&job,&lease,foreign).is_err());
        let wrong=parent(&node,EffectFlow::ShardPut);
        assert!(shards.live_workers.prepare(&node.lock().unwrap(),&job,&lease,wrong).is_err());
        let valid=parent(&node,EffectFlow::ShardStart);
        assert!(shards.live_workers.prepare(&node.lock().unwrap(),&job,"missing",valid).is_err());
        let valid=parent(&node,EffectFlow::ShardStart);
        let ticket=shards.live_workers.prepare(&node.lock().unwrap(),&job,&lease,valid).unwrap();
        drop(ticket); // The map, not this external launch handle, owns the prepared operation.
        let duplicate=parent(&node,EffectFlow::ShardStart);
        assert!(shards.live_workers.prepare(&node.lock().unwrap(),&job,&lease,duplicate).is_err());
        assert!(matches!(shards.live_workers.get(&job).unwrap().observe().unwrap().process,ProcessObservation::Prepared));
    }
    #[test]
    #[cfg(unix)]
    fn real_child_stays_map_owned_through_external_drop_and_poison_until_actual_reap() {
        let (dir,node,shards,job,lease)=setup();
        let registry=node.lock().unwrap().activity.clone();
        let valid=parent(&node,EffectFlow::ShardStart);
        let ticket=shards.live_workers.prepare(&node.lock().unwrap(),&job,&lease,valid).unwrap();
        let handle=match ticket.spawn(Command::new("/bin/sleep").arg("0.05")) {
            Ok(handle) => handle,
            Err(failure) => panic!("authorized fixture failed to launch: {}",failure.error),
        };
        drop(handle);
        let handle=shards.live_workers.get(&job).unwrap();
        let poison=handle.slot.clone();
        assert!(std::thread::spawn(move || { let _held=poison.process.lock().unwrap(); panic!("owned watcher fault"); }).join().is_err());
        let deadline=std::time::Instant::now()+std::time::Duration::from_secs(2);
        loop {
            let observed=handle.observe().unwrap(); assert!(observed.unknown);
            if let ProcessObservation::Reaped(exit)=observed.process { assert!(exit.success()); break; }
            assert!(std::time::Instant::now()<deadline,"natural-exit fixture did not finish");
            std::thread::yield_now();
        }
        assert_eq!(registry.active_writer_count_for_test(),1,"reap does not fabricate host settlement");
        assert_eq!(node.lock().unwrap().leases.table.find(&lease).unwrap().state,State::Held);
        assert_eq!(handle.slot.lease.id,lease); assert!(!handle.slot.lease.holder.is_empty());
        drop(shards); drop(node);
        assert!(matches!(Node::open(dir.path()),Err(Error::Denied(_))),"retained owner still owns actual Node storage lock");
        assert!(handle.owner.get(&job).is_some()); // No settlement/removal API exists in this checkpoint.
    }
}
