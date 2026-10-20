//! Who held a claim on a worktree of this device. The claim leaves no board entry, so this file is the record the landing reviews read
//! to leave the creator of a branch out of its reviewers. A record is never dropped to make room: a process tree that has made
//! `max_worktree_records` of them is refused further worktree claims until its oldest ages out, so no session can push its own record
//! (or another's) out by claiming more paths.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::types::{Lease, Request, Resource};
use crate::error::Result;
use crate::fsutil::write_atomic;

pub const HOLDERS_FILE: &str = "worktree-holders.json";
/// The most records the file holds.
pub const MOST_HOLDERS: usize = 8192;
/// How long a record is kept.
pub const KEPT_MS: u64 = 90 * 24 * 3600 * 1000;

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
struct Record {
    board: String,
    path: String,
    name: String,
    tree: String,
    at_ms: u64,
}

pub struct Holders {
    path: PathBuf,
    records: Vec<Record>,
}

fn worktrees(resources: &[Resource]) -> impl Iterator<Item = &str> {
    resources.iter().filter_map(|r| match r {
        Resource::Claim { kind, name } if kind == "worktree" => Some(name.as_str()),
        _ => None,
    })
}

impl Holders {
    /// Read the file under ``dir``; a missing or unreadable file is an empty record.
    pub fn open(dir: &Path) -> Holders {
        let path = dir.join(HOLDERS_FILE);
        let records = std::fs::read(&path).ok().and_then(|bytes| serde_json::from_slice(&bytes).ok()).unwrap_or_default();
        Holders { path, records }
    }

    fn known(&self, board: &str, path: &str, name: &str) -> bool {
        self.records.iter().any(|r| r.board == board && r.path == path && r.name == name)
    }

    /// Whether ``request`` of a session in the process tree ``tree`` may be granted: the worktrees it names that are not on record
    /// must fit in the tree's share and in the file. Records older than `KEPT_MS` are dropped first.
    pub fn room_for(&mut self, request: &Request, (tree, per_tree): (&str, usize), now: u64) -> Result<()> {
        let before = self.records.len();
        self.records.retain(|r| now.saturating_sub(r.at_ms) < KEPT_MS);
        if self.records.len() != before {
            write_atomic(&self.path, &serde_json::to_vec(&self.records)?)?;
        }
        let new = worktrees(&request.resources).filter(|p| !self.known(&request.board, p, &request.name)).count();
        let mine = self.records.iter().filter(|r| r.tree == tree).count();
        if new > 0 && mine + new > per_tree {
            return Err(crate::limits::refuse("the sessions of this process tree have claimed as many worktrees as this device remembers", per_tree, "max_worktree_records", "lease-config.json"));
        }
        if new > 0 && self.records.len() + new > MOST_HOLDERS {
            return Err(crate::limits::refuse("this device remembers as many worktree claims as it may", MOST_HOLDERS, "MOST_HOLDERS", "lease/holders.rs"));
        }
        Ok(())
    }

    /// Note that the holder of ``lease`` (in the process tree ``tree``) was granted each worktree claim it names; writes the file only
    /// when something was new.
    pub fn note(&mut self, lease: &Lease, tree: &str, now: u64) -> Result<()> {
        let fresh: Vec<Record> = worktrees(&lease.resources).filter(|p| !self.known(&lease.board, p, &lease.name))
            .map(|p| Record { board: lease.board.clone(), path: p.into(), name: lease.name.clone(), tree: tree.into(), at_ms: now }).collect();
        if fresh.is_empty() {
            return Ok(());
        }
        self.records.extend(fresh);
        write_atomic(&self.path, &serde_json::to_vec(&self.records)?)
    }

    /// The sessions of ``board`` that ever held a claim on the worktree ``path``.
    pub fn of(&self, board: &str, path: &str) -> Vec<String> {
        self.records.iter().filter(|r| r.board == board && r.path == path).map(|r| r.name.clone()).collect()
    }
}
