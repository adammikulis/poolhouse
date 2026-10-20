//! What a removal does to the logs of boards: the admin that removes a device names how much of each log of it this device holds,
//! and every device closes those logs there, so a member that has not heard of the removal cannot hand the removed device's later
//! entries on. Entries are checked by their own signatures; a relay proves nothing about them.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Value};

use crate::cert::board_fingerprint;
use crate::error::Result;
use crate::membership::Status;
use crate::node::Node;
use crate::state::Cut;

use super::body::MOST_LOG_CUTS;

impl Node {
    /// The logs of boards that the device ``fingerprint`` wrote and this device holds, each with how many entries of it are held:
    /// what a removal of that device names.
    pub(super) fn logs_of(&self, fingerprint: &str) -> Vec<Value> {
        let Some(key) = self.members.get(fingerprint).and_then(|d| d.der()).and_then(|der| board_fingerprint(&der).ok()) else { return Vec::new() };
        let mut out = Vec::new();
        for (id, hosted) in &self.boards {
            for origin in hosted.board.origins_of(&key) {
                let rows = hosted.board.chain(&origin);
                out.push(json!({"board": id, "origin": origin, "seq": rows.last().map_or(0, |r| r.seq), "hash": rows.last().map(|r| r.hash.clone()).unwrap_or_default()}));
            }
        }
        out.truncate(MOST_LOG_CUTS);
        out
    }

    /// Close the logs of every removed device on the board ``only``, or on every board hosted when it is empty.
    pub(crate) fn apply_log_cuts(&mut self, only: &str) -> Result<()> {
        let Some(gov) = self.members.gov() else { return Ok(()) };
        let removed: BTreeSet<String> = gov.state().roster.devices.values().filter(|d| d.status == Status::Revoked).filter_map(|d| d.der()).filter_map(|der| board_fingerprint(&der).ok()).collect();
        let cuts = gov.state().roster.log_cuts.clone();
        for (id, hosted) in self.boards.iter_mut().filter(|(id, _)| only.is_empty() || id.as_str() == only) {
            let named: BTreeMap<String, Cut> = cuts.iter().filter(|((board, _), _)| board == id).map(|((_, origin), (seq, hash))| (origin.clone(), Cut { seq: *seq, hash: hash.clone() })).collect();
            hosted.board.close_removed(removed.clone(), &named)?;
        }
        Ok(())
    }
}
