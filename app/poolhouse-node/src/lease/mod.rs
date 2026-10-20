//! The lease service: one table for GPU one-at-a-time, CPU slots, memory admission, named
//! claims and served-model slots, with a queue, dead-holder detection and takeover.
//!
//! The device's own table is authoritative for the device's resources and lives in
//! `<state>/leases.json`. Every grant and end is also written to the holder's board as a
//! `lease` entry, so peers see it; `merge` reads those entries back.

pub mod live;
pub mod merge;
pub mod policy;
pub mod rpc;
pub mod table;
pub mod types;

use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::error::Result;
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
    pub cfg: Config,
    pub table: Table,
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
    pub fn open(dir: &Path) -> Result<Leases> {
        let read = |name: &str| match std::fs::read(dir.join(name)) {
            Ok(bytes) => Ok(Some(bytes)),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(e) => Err(e),
        };
        let config_raw = read(CONFIG_FILE)?;
        let cfg = config_raw.as_deref().and_then(Config::parse).unwrap_or_default();
        let mut table: Table = match read(TABLE_FILE)? {
            Some(bytes) => serde_json::from_slice(&bytes)?,
            None => Table::default(),
        };
        table.next_seq = table.next_seq.max(table.leases.iter().map(|l| l.seq).max().map_or(0, |s| s + 1));
        let saved = serde_json::to_vec(&table.leases)?;
        Ok(Leases { cfg, table, path: dir.join(TABLE_FILE), config_path: dir.join(CONFIG_FILE), config_raw, saved, clock: Box::new(wall_ms), deferred: false, pending: Vec::new() })
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
        let file = serde_json::to_vec(&json!({"leases": kept, "next_seq": self.table.next_seq}))?;
        write_atomic(&self.path, &file)?;
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

    /// Drop the dead and the expired, grant what the freed resources allow, and save when
    /// anything changed. Returns what happened, for the board.
    pub fn tick(&mut self, foreign: &Foreign) -> Result<Vec<Event>> {
        self.reload();
        let now = self.now();
        let mut ended = self.table.sweep(now);
        ended.extend(self.table.yield_to(foreign));
        let mut events = self.table.schedule(&self.cfg, now, foreign, &ended);
        events.extend(ended.into_iter().map(|(l, why)| Event::Ended(l, why)));
        if !events.is_empty() {
            self.save()?;
        }
        Ok(events)
    }
}
