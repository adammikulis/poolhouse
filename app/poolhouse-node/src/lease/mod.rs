//! The lease service: one table for GPU one-at-a-time, CPU slots, memory admission, named
//! claims and served-model slots, with a queue, dead-holder detection and takeover.
//!
//! The device's own table is authoritative for the device's resources and lives in
//! `<state>/leases.json`. Every grant and end of a lease that holds something other devices can name
//! is also written to the holder's board as a `lease` entry, so peers see it; `merge` reads those entries back. A lease that holds
//! claims of things on this device alone (`types::DEVICE_CLAIMS`) writes nothing.

pub(crate) mod allocation_format;
pub mod holders;
pub mod live;
pub mod merge;
pub mod policy;
pub mod rpc;
pub mod table;
pub mod types;

use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::error::{Error, Result};
use crate::node::update_activity::Registry;
use crate::node::Node;
use crate::fsutil::write_atomic;
use table::{Event, Foreign, Table};
use serde_json::json;
use types::{Config, Lease};

pub const TABLE_FILE: &str = "leases.json";
pub const CONFIG_FILE: &str = "lease-config.json";

fn wall_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_millis() as u64)
}

pub struct Leases {
    activity: Registry,
    pub(crate) allocation_fence:Option<allocation_format::Fence>,
    pub(crate) allocation_parent:Option<std::fs::File>,
    pub cfg: Config,
    pub table: Table,
    /// Who held a claim on a worktree of this device.
    pub holders: holders::Holders,
    path: PathBuf,
    config_path: PathBuf,
    /// The bytes of the configuration file the settings came from; None when there was none.
    config_raw: Option<Vec<u8>>,
    /// The leases the file holds, serialised: a change that leaves them as they are writes nothing.
    saved: Vec<u8>,
    pub clock: Box<dyn Fn() -> u64 + Send>,
    /// While set, `save` writes nothing and the events for the boards are kept: a call that changes the table many times writes it and
    /// tells the boards once at its end (`Leases::batch`).
    deferred: bool,
    /// The events kept while `deferred`.
    pending: Vec<Event>,
}

impl Leases {
    /// Open the table under ``dir``; holders that died or ran out while the node was down are
    /// dropped at the next `tick`.
    pub(crate) fn open(dir: &Path, activity: Registry) -> Result<Leases> {
        let read = |name: &str| match std::fs::read(dir.join(name)) {
            Ok(bytes) => Ok(Some(bytes)),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(e) => Err(e),
        };
        let config_raw = read(CONFIG_FILE)?;
        let cfg = config_raw.as_deref().and_then(Config::parse).unwrap_or_default();
        let (mut table,allocation_fence)=match read(TABLE_FILE)? {
            Some(bytes)=>allocation_format::decode(&bytes)?,
            None=>(Table::default(),None),
        };
        table.next_seq = table.next_seq.max(table.leases.iter().map(|l| l.seq).max().map_or(0, |s| s + 1));
        let saved = serde_json::to_vec(&table.leases)?;
        Ok(Leases { activity, allocation_fence, allocation_parent:None, cfg, table, holders: holders::Holders::open(dir)?, path: dir.join(TABLE_FILE), config_path: dir.join(CONFIG_FILE), config_raw, saved, clock: Box::new(wall_ms), deferred: false, pending: Vec::new() })
    }

    /// Take the settings from the configuration file again when its bytes are not the ones they came from: a file that is gone
    /// sets the defaults and one that does not parse changes nothing.
    fn reload(&mut self) {
        let raw = match std::fs::read(&self.config_path) {
            Ok(bytes) => Some(bytes),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
            Err(_) => return,
        };
        if raw == self.config_raw {
            return;
        }
        let fresh = match &raw {
            Some(bytes) => Config::parse(bytes),
            None => Some(Config::default()),
        };
        self.config_raw = raw;
        if let Some(cfg) = fresh {
            self.cfg = cfg;
        }
    }

    pub fn now(&self) -> u64 {
        (self.clock)()
    }

    /// Write the table when its leases changed. A quiet lease is a request in flight, which ends with its process, so it is kept in
    /// memory and a table that changed only by one is not written.
    pub fn save(&mut self) -> Result<()> {
        if self.deferred {
            return Ok(());
        }
        let kept: Vec<&Lease> = self.table.leases.iter().filter(|l| !l.quiet).collect();
        let content = serde_json::to_vec(&kept)?;
        if content == self.saved {
            return Ok(());
        }
        let file=if let Some(fence)=&self.allocation_fence {allocation_format::encode(&self.table,fence)?}
            else {serde_json::to_vec(&json!({"leases":kept,"next_seq":self.table.next_seq}))?};
        if let Some(fence)=&self.allocation_fence {
            allocation_format::write(self.allocation_parent.as_ref().ok_or_else(||Error::Invalid("allocation storage recovery is unavailable".into()))?,&self.table,fence)?;
        } else {write_atomic(&self.path,&file)?;}
        self.saved = content;
        Ok(())
    }

    /// Keep ``events`` for the boards until the batch ends; false, with nothing kept, when no batch is running.
    pub fn hold(&mut self, events: &[Event]) -> bool {
        if self.deferred {
            self.pending.extend_from_slice(events);
        }
        self.deferred
    }

    /// Run ``change``, which changes the table many times, and write the table and tell the boards once when it ends, whether or not it
    /// failed.
    pub fn batch<T>(node: &mut Node, change: impl FnOnce(&mut Node) -> Result<T>) -> Result<T> {
        node.leases.deferred = true;
        let done = change(node);
        node.leases.deferred = false;
        let saved = node.leases.save();
        let events = std::mem::take(&mut node.leases.pending);
        let told = rpc::mirror(node, &events);
        let value = done?;
        saved.and(told).map(|()| value)
    }

    /// Both local RPC and shard admission use this mandatory same-node phase reservation.
    pub(crate) fn enqueue(&mut self, now: u64, req: &types::Request, id: (String, usize)) -> Result<String> {
        let _writer = self.activity.lease_writer().map_err(activity_error)?;
        // Kernel inspection is outside the phase mutex, as are every caller's clocks and I/O.
        let birth = if req.pid != 0 { live::start_of(req.pid) } else { None };
        let reservation = self.activity.reserve_grants().map_err(activity_error)?;
        self.table.enqueue(&self.cfg, now, req, id, birth, &reservation)
    }

    /// Drop the dead and the expired, grant what the freed resources allow, and save when
    /// anything changed. Returns what happened, for the board.
    pub fn tick(&mut self, foreign: &Foreign) -> Result<Vec<Event>> {
        let _writer = self.activity.lease_writer().map_err(activity_error)?;
        self.reload();
        let now = self.now();
        let mut ended = self.table.sweep(now);
        ended.extend(self.table.yield_to(foreign));
        let mut events = {
            let reservation = self.activity.reserve_grants().map_err(activity_error)?;
            self.table.schedule(&self.cfg, now, foreign, &ended, &reservation)
        };
        events.extend(ended.into_iter().map(|(l, why)| Event::Ended(l, why)));
        if !events.is_empty() {
            self.save()?;
        }
        Ok(events)
    }
}

fn activity_error(_: crate::node::update_activity::Refusal) -> Error {
    Error::say("unavailable", "node update admission is unavailable", "none", "retry")
}

#[cfg(test)]
mod drain_tests {
    use super::*;
    use types::{Class, Request, Resource, State, LOCAL};
    fn request(resources: Vec<Resource>, tag: &str) -> Request {
        Request { board: "work".into(), name: "actor".into(), resources, class: Class::Interactive,
            estimate_s: None, ttl_s: Some(60), pid: 0, remote: true, local: true, wait: true,
            tag: tag.into(), quiet: false }
    }
    fn cpu() -> Resource { Resource::CpuSlots { device:LOCAL.into(),count:1 } }
    fn claim() -> Resource { Resource::Claim { kind:"branch".into(),name:"work".into() } }
    #[test]
    fn central_tick_preserves_existing_compute_but_never_grants_queued_compute_during_drain() {
        let dir=tempfile::tempdir().unwrap(); let mut node=Node::open(dir.path()).unwrap();
        node.leases.clock=Box::new(||1000);
        let held=node.leases.enqueue(1000,&request(vec![cpu()],"held"),("held".into(),0)).unwrap();
        node.leases.tick(&Foreign::new()).unwrap();
        let queued=node.leases.enqueue(1000,&request(vec![cpu()],"queued"),("queued".into(),0)).unwrap();
        let cfg=node.leases.cfg.clone();
        // Make available capacity explicit so the drain gate, not contention, must prevent this grant.
        node.activity.drain_for_test();
        node.leases.table.renew(&cfg,1000,&held,"work/actor",60).unwrap();
        node.leases.tick(&Foreign::new()).unwrap();
        assert_eq!(node.leases.table.find(&held).unwrap().state,State::Held);
        assert_eq!(node.leases.table.find(&queued).unwrap().state,State::Queued);
        node.leases.table.release(&held,"work/actor").unwrap();
        node.leases.tick(&Foreign::new()).unwrap();
        assert_eq!(node.leases.table.find(&queued).unwrap().state,State::Queued);
        assert!(node.leases.enqueue(1000,&request(vec![cpu()],"new"),("new".into(),0)).is_err());
        assert!(node.leases.enqueue(1000,&request(vec![claim(),cpu()],"mixed"),("mixed".into(),0)).is_err());
        let id=node.leases.enqueue(1000,&request(vec![claim()],"claim"),("claim".into(),0)).unwrap();
        node.leases.tick(&Foreign::new()).unwrap();
        assert_eq!(node.leases.table.find(&id).unwrap().state,State::Held);
        node.leases.table.release(&id,"work/actor").unwrap();
    }
    #[test]
    fn callbacks_are_outside_phase_lock_and_claim_tick_can_reenter_registry() {
        let dir=tempfile::tempdir().unwrap(); let mut node=Node::open(dir.path()).unwrap();
        let registry=node.activity.clone();
        node.leases.clock=Box::new(move || {
            assert!(registry.phase_available_for_test(),"clock callback must not run under grant reservation");
            let _nested=registry.local_maintenance().unwrap(); 1000
        });
        node.activity.drain_for_test();
        node.leases.enqueue(1000,&request(vec![claim()],"claim"),("claim".into(),0)).unwrap();
        node.leases.tick(&Foreign::new()).unwrap();
        assert_eq!(node.leases.table.find("claim").unwrap().state,State::Held);
    }
    #[test]
    fn unknown_and_mixed_resources_cannot_be_claim_maintenance() {
        assert!(!types::claims_only(&[]));
        assert!(!types::claims_only(&[Resource::Claim{kind:"unknown".into(),name:"work".into()}]));
        assert!(!types::claims_only(&[claim(),cpu()]));
        assert!(serde_json::from_value::<Vec<Resource>>(serde_json::json!([
            {"type":"claim","kind":"branch","name":"work","count":1}])).is_err());
        assert!(serde_json::from_value::<Vec<Resource>>(serde_json::json!([
            {"type":"unknown","kind":"branch","name":"work"}])).is_err());
    }
    #[test]
    fn enqueue_and_drain_are_atomic_and_preexisting_queue_never_bypasses_tick() {
        use std::sync::{Arc,Mutex,Barrier};
        let dir=tempfile::tempdir().unwrap(); let node=Node::open(dir.path()).unwrap();
        let registry=node.activity.clone(); let owned=Arc::new(Mutex::new(node)); let other=owned.clone();
        let b=Arc::new(Barrier::new(2)); let bc=b.clone();
        let thread=std::thread::spawn(move || { bc.wait(); other.lock().unwrap().leases.enqueue(1000,
            &request(vec![cpu()],"race"),("race".into(),0)) });
        b.wait(); registry.drain_for_test(); let admitted=thread.join().unwrap();
        let mut node=owned.lock().unwrap(); node.leases.clock=Box::new(||1000);
        node.leases.tick(&Foreign::new()).unwrap();
        if admitted.is_ok() { assert_eq!(node.leases.table.find("race").unwrap().state,State::Queued); }
        else { assert!(node.leases.table.find("race").is_none()); }
    }
}
