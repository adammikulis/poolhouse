//! Who held a claim on a worktree of this device. The claim leaves no board entry, so this file is the record the landing reviews read
//! to leave the creator of a branch out of its reviewers. A record is never dropped to make room: a process tree that has made
//! `max_worktree_records` of them is refused further worktree claims and keeps those records, so no session can push its own record
//! (or another's) out by claiming more paths.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::types::{Lease, Request, Resource};
use crate::error::Result;
use crate::fsutil::write_atomic;

pub const HOLDERS_FILE: &str = "worktree-holders.json";
/// The most records the file holds.
pub const MOST_HOLDERS: usize = 8192;

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
    /// Read durable worktree evidence under ``dir``.
    pub fn open(dir: &Path) -> Result<Holders> {
        let path = dir.join(HOLDERS_FILE);
        let records = match std::fs::read(&path) {
            Ok(bytes) => serde_json::from_slice(&bytes)?,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Vec::new(),
            Err(error) => return Err(error.into()),
        };
        Ok(Holders { path, records })
    }

    fn known(&self, board: &str, path: &str, name: &str) -> bool {
        self.records.iter().any(|r| r.board == board && r.path == path && r.name == name)
    }

    /// Reserve durable evidence before a request enters the scheduling queue.
    pub fn reserve(&mut self, request: &Request, (tree, per_tree): (&str, usize), now: u64) -> Result<()> {
        let new = worktrees(&request.resources).filter(|p| !self.known(&request.board, p, &request.name)).count();
        let mine = self.records.iter().filter(|r| r.tree == tree).count();
        if new > 0 && mine + new > per_tree {
            return Err(crate::limits::refuse("the sessions of this process tree have claimed as many worktrees as this device remembers", per_tree, "max_worktree_records", "lease-config.json"));
        }
        if new > 0 && self.records.len() + new > MOST_HOLDERS {
            return Err(crate::limits::refuse("this device remembers as many worktree claims as it may", MOST_HOLDERS, "MOST_HOLDERS", "lease/holders.rs"));
        }
        self.record(&request.board, &request.name, &request.resources, tree, now)
    }

    /// Note that the holder of ``lease`` (in the process tree ``tree``) was granted each worktree claim it names; writes the file only
    /// when something was new.
    pub fn note(&mut self, lease: &Lease, tree: &str, now: u64) -> Result<()> {
        self.record(&lease.board, &lease.name, &lease.resources, tree, now)
    }

    fn record(&mut self, board: &str, name: &str, resources: &[Resource], tree: &str, now: u64) -> Result<()> {
        let mut candidate = self.records.clone();
        for path in worktrees(resources) {
            if !candidate.iter().any(|r| r.board == board && r.path == path && r.name == name) {
                candidate.push(Record { board: board.into(), path: path.into(), name: name.into(), tree: tree.into(), at_ms: now });
            }
        }
        if candidate.len() > MOST_HOLDERS {
            return Err(crate::limits::refuse("this device remembers as many worktree claims as it may", MOST_HOLDERS, "MOST_HOLDERS", "lease/holders.rs"));
        }
        if candidate != self.records {
            write_atomic(&self.path, &serde_json::to_vec(&candidate)?)?;
            self.records = candidate;
        }
        Ok(())
    }

    /// The sessions of ``board`` that ever held a claim on the worktree ``path``.
    pub fn of(&self, board: &str, path: &str) -> Vec<String> {
        self.records.iter().filter(|r| r.board == board && r.path == path).map(|r| r.name.clone()).collect()
    }
}
