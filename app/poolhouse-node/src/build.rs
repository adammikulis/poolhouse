//! The build a node runs and what it has seen of its peers' builds.
//!
//! A build is the git commit of the runtime (`id`), the number of commits behind it on the
//! development branch (`seq`, so builds are ordered), and the peer wire version (`wire`). A device
//! that hears of a newer `seq` from an active member is stale: it is catching up and takes no work
//! (leases, shards, landing) until it runs the newest build. A peer on another wire version cannot
//! sync with this device at all; it is shown as needing an update, with the command that fixes it.

use serde_json::{json, Value};

use crate::error::{Error, Result};
use crate::node::Node;

/// The wire version this binary speaks. Bump it when a signing label or a peer op changes.
pub const WIRE: u32 = 1;

/// The command that brings a device onto the newest build.
pub const FIX: &str = "poolhouse runtime ensure";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Build {
    pub id: String,
    pub seq: u64,
    pub wire: u32,
    /// The person pinned this build: it does not update itself and is not held back for being behind.
    pub pinned: bool,
}

impl Build {
    /// The build named by `POOLHOUSE_BUILD_ID`, `POOLHOUSE_BUILD_SEQ` and `POOLHOUSE_WIRE` (any may be missing).
    pub fn from_env() -> Build {
        let text = |k: &str| std::env::var(k).unwrap_or_default();
        Build { id: text("POOLHOUSE_BUILD_ID"), seq: text("POOLHOUSE_BUILD_SEQ").parse().unwrap_or(0), wire: text("POOLHOUSE_WIRE").parse().unwrap_or(WIRE), pinned: matches!(text("POOLHOUSE_BUILD_PINNED").as_str(), "1" | "yes" | "on") }
    }

    pub fn json(&self) -> Value {
        json!({"build": self.id, "build_seq": self.seq, "wire": self.wire, "pinned": self.pinned})
    }
}

/// What a peer said its build is; `wire` 0 means it said nothing (a build older than this field).
#[derive(Default, Clone, Debug, PartialEq, Eq)]
pub struct Seen {
    pub id: String,
    pub seq: u64,
    pub wire: u32,
    pub pinned: bool,
}

impl Seen {
    pub fn read(v: &Value) -> Seen {
        Seen {
            id: v.get("build").and_then(Value::as_str).unwrap_or("").chars().take(64).collect(),
            seq: v.get("build_seq").and_then(Value::as_u64).unwrap_or(0),
            wire: v.get("wire").and_then(Value::as_u64).and_then(|w| u32::try_from(w).ok()).unwrap_or(0),
            pinned: v.get("pinned").and_then(Value::as_bool).unwrap_or(false),
        }
    }
}

/// How a peer's build stands against this device's.
pub fn state(mine: &Build, theirs: &Seen) -> &'static str {
    if theirs.wire != mine.wire {
        "needs update"
    } else if theirs.seq > mine.seq {
        "newer"
    } else if theirs.seq < mine.seq {
        "older"
    } else {
        "current"
    }
}

/// The exact fix for a wire mismatch with a peer, said from this device's side.
pub fn fix_for(mine: &Build, theirs: &Seen, name: &str) -> String {
    if theirs.wire != 0 && theirs.wire > mine.wire || (theirs.wire == mine.wire && theirs.seq > mine.seq) {
        format!("this device is on an older build (wire {}, the pool's is {}): run `{FIX}` here (it runs on its own unless the build is pinned)", mine.wire, theirs.wire)
    } else {
        format!("{name} is on an older build (wire {}, this device's is {}): run `{FIX}` on {name} (it runs on its own unless the build is pinned)", theirs.wire, mine.wire)
    }
}

impl Node {
    /// Remember the build the active member ``fp`` reported.
    pub fn observe_build(&mut self, fp: &str, said: &Value) {
        if self.members.is_active(fp) {
            self.facts.peer(fp).seen = Some(Seen::read(said));
        }
    }

    fn seen_active(&self) -> impl Iterator<Item = (&String, &Seen)> {
        self.facts.peers.iter().filter(|(fp, _)| self.members.is_active(fp)).filter_map(|(fp, f)| f.seen.as_ref().map(|s| (fp, s)))
    }

    /// The newest `seq` any wire-compatible active member reported.
    pub fn newest_seq(&self) -> u64 {
        self.seen_active().filter(|(_, s)| s.wire == self.build.wire).map(|(_, s)| s.seq).max().unwrap_or(0)
    }

    /// True while a member runs a newer build than this device.
    pub fn stale(&self) -> bool {
        self.newest_seq() > self.build.seq
    }

    /// Refuse work while stale: the device catches up first.
    pub fn work_gate(&self) -> Result<()> {
        if self.stale() && !self.build.pinned {
            return Err(Error::Denied(format!("this device is catching up: the pool runs build {} and this device runs {}; it takes no work until it is current (`{FIX}` brings it there, and it runs on its own)", self.newest_seq(), self.build.seq)));
        }
        Ok(())
    }

    /// The active members this device cannot sync with.
    pub fn incompatible_peers(&self) -> Vec<String> {
        self.seen_active().filter(|(_, s)| s.wire != self.build.wire).map(|(fp, _)| fp.clone()).collect()
    }

    /// This device's build as `pool_status` shows it, with the pool's newest.
    pub fn build_status(&self) -> Value {
        let mut out = self.build.json();
        let state = if self.build.pinned { "pinned" } else if self.stale() { "catching up" } else if self.incompatible_peers().is_empty() { "current" } else { "needs update" };
        out["newest_seq"] = json!(self.newest_seq().max(self.build.seq));
        out["state"] = json!(state);
        out
    }

    /// What `pool_status` shows of one member's build.
    pub fn member_build(&self, fp: &str, name: &str) -> Value {
        match self.facts.peers.get(fp).and_then(|f| f.seen.as_ref()) {
            None => json!({"build": "", "build_seq": 0, "wire": 0, "build_state": "unknown", "fix": ""}),
            Some(s) => {
                let st = state(&self.build, s);
                let fix = if st == "needs update" { fix_for(&self.build, s, name) } else { String::new() };
                json!({"build": s.id, "build_seq": s.seq, "wire": s.wire, "pinned": s.pinned, "build_state": if s.pinned && st != "needs update" { "pinned" } else { st }, "fix": fix})
            }
        }
    }
}
