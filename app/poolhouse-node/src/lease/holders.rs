//! Who held a claim on a worktree of this device. The claim leaves no board entry, so this file is the record the landing reviews read
//! to leave the creator of a branch out of its reviewers.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::types::{Lease, Resource};
use crate::error::Result;
use crate::fsutil::write_atomic;

pub const HOLDERS_FILE: &str = "worktree-holders.json";
/// The most (board, worktree, session) records the file keeps; a new one past it drops the oldest.
pub const MOST_HOLDERS: usize = 4096;

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
struct Record {
    board: String,
    path: String,
    name: String,
}

pub struct Holders {
    path: PathBuf,
    records: Vec<Record>,
}

impl Holders {
    /// Read the file under ``dir``; a missing or unreadable file is an empty record.
    pub fn open(dir: &Path) -> Holders {
        let path = dir.join(HOLDERS_FILE);
        let records = std::fs::read(&path).ok().and_then(|bytes| serde_json::from_slice(&bytes).ok()).unwrap_or_default();
        Holders { path, records }
    }

    /// Note that the holder of ``lease`` was granted each worktree claim it names; writes the file only when something was new.
    pub fn note(&mut self, lease: &Lease) -> Result<()> {
        let mut changed = false;
        for r in &lease.resources {
            let Resource::Claim { kind, name: path } = r else { continue };
            if kind != "worktree" {
                continue;
            }
            let record = Record { board: lease.board.clone(), path: path.clone(), name: lease.name.clone() };
            if !self.records.contains(&record) {
                self.records.push(record);
                changed = true;
            }
        }
        if !changed {
            return Ok(());
        }
        let excess = self.records.len().saturating_sub(MOST_HOLDERS);
        self.records.drain(..excess);
        write_atomic(&self.path, &serde_json::to_vec(&self.records)?)
    }

    /// The sessions of ``board`` that ever held a claim on the worktree ``path``.
    pub fn of(&self, board: &str, path: &str) -> Vec<String> {
        self.records.iter().filter(|r| r.board == board && r.path == path).map(|r| r.name.clone()).collect()
    }
}
