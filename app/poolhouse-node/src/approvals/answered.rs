//! What the node remembers of the answers a person gave: the scopes they declined and the sessions they blocked.
//!
//! Nothing here holds a request back. A request is shown to the person at once, whoever asks and however often: the same request made
//! again is the same item (`same`), a session has one request open at a time (`request`), and a scope the person declined before is
//! shown with how often (`declined_before`) and takes one more deliberate step to approve. A block is the one refusal: the person chose
//! it on the card they answered, the card says for how long, and the agent is told. Declines and blocks are kept on disk, so a restart
//! forgets none.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::error::{Error, Result};
use crate::fsutil::{sha256_hex, write_atomic};
use crate::row::canonical;

pub const DAY_MS: u64 = 24 * 3_600_000;
/// How long a scope that was declined is remembered and shown with the next request for it.
pub const DECLINED_MS: u64 = 30 * DAY_MS;
/// What a block lasts. The card a person blocks on says so.
pub const BLOCK_MS: u64 = DAY_MS;
const MOST_DECLINED: usize = 400;

#[derive(Serialize, Deserialize, Clone, Debug)]
struct Declined {
    board: String,
    action: String,
    resource: String,
    at_ms: u64,
}

#[derive(Serialize, Deserialize, Default)]
struct File {
    declined: Vec<Declined>,
    /// The sessions (`board/name`) a person blocked, and when each block ends.
    blocked: BTreeMap<String, u64>,
}

/// The scopes a person declined and the sessions a person blocked on a node, kept in one atomically replaced file.
pub struct Answered {
    path: PathBuf,
    file: File,
}

/// What makes two requests the same: the board, the session and the sorted actions and resources, and nothing else. The note, the
/// time asked for and the number of uses are left out, so asking again a little differently is still the same request.
pub fn same(board: &str, session: &str, asks: &[(&str, &str)]) -> String {
    let mut all: Vec<(&str, &str)> = asks.to_vec();
    all.sort_unstable();
    all.dedup();
    sha256_hex(canonical(&serde_json::json!({"board": board, "session": session, "asks": all})).as_bytes())
}

impl Answered {
    pub fn open(path: &Path) -> Result<Answered> {
        let file = match std::fs::read(path) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| Error::Damaged(format!("answered unreadable: {e}")))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => File::default(),
            Err(e) => return Err(e.into()),
        };
        Ok(Answered { path: path.into(), file })
    }

    fn save(&mut self) -> Result<()> {
        write_atomic(&self.path, &serde_json::to_vec(&self.file)?)
    }

    /// Forget what is old enough to have stopped counting.
    fn prune(&mut self, now: u64) {
        self.file.declined.retain(|d| now.saturating_sub(d.at_ms) < DECLINED_MS);
        self.file.blocked.retain(|_, until| *until > now);
    }

    /// Remember that a person declined these scopes of ``board``.
    pub fn note_declined(&mut self, board: &str, asks: &[(&str, &str)], now: u64) -> Result<()> {
        for (action, resource) in asks {
            self.file.declined.push(Declined { board: board.into(), action: (*action).into(), resource: (*resource).into(), at_ms: now });
        }
        let extra = self.file.declined.len().saturating_sub(MOST_DECLINED);
        self.file.declined.drain(..extra);
        self.save()
    }

    /// How often the most declined of these scopes was declined before on ``board``.
    pub fn declined_before(&mut self, board: &str, asks: &[(&str, &str)], now: u64) -> u64 {
        self.prune(now);
        asks.iter().map(|(a, r)| self.file.declined.iter().filter(|d| d.board == board && d.action == *a && d.resource == *r).count() as u64).max().unwrap_or(0)
    }

    /// A person blocked the session ``who`` (`board/name`): nothing it asks is taken for `BLOCK_MS`.
    pub fn block(&mut self, who: &str, now: u64) -> Result<()> {
        self.file.blocked.insert(who.to_string(), now + BLOCK_MS);
        self.save()
    }

    /// When the block of the session ``who`` ends, if it is blocked now.
    pub fn blocked_until(&mut self, who: &str, now: u64) -> Option<u64> {
        self.prune(now);
        self.file.blocked.get(who).copied()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ledger() -> Answered {
        let dir = tempfile::tempdir().unwrap();
        Answered::open(&dir.keep().join("answered.json")).unwrap()
    }

    #[test]
    fn the_same_request_with_a_different_note_or_time_is_the_same() {
        let a = same("demo", "claude-a", &[("add_device", "*"), ("link_read", "a>b")]);
        assert_eq!(a, same("demo", "claude-a", &[("link_read", "a>b"), ("add_device", "*")]));
        assert_ne!(a, same("demo", "claude-b", &[("add_device", "*"), ("link_read", "a>b")]));
        assert_ne!(a, same("demo", "claude-a", &[("add_device", "*")]));
    }

    #[test]
    fn a_declined_scope_is_remembered_with_how_often_and_forgotten_after_thirty_days() {
        let mut f = ledger();
        let now = 9_000_000;
        assert_eq!(f.declined_before("demo", &[("add_device", "*")], now), 0);
        f.note_declined("demo", &[("add_device", "*")], now).unwrap();
        f.note_declined("demo", &[("add_device", "*"), ("sync_now", "*")], now + 1).unwrap();
        assert_eq!(f.declined_before("demo", &[("add_device", "*"), ("link_read", "a>b")], now + 2), 2);
        assert_eq!(f.declined_before("lab", &[("add_device", "*")], now + 2), 0, "another board");
        assert_eq!(f.declined_before("demo", &[("add_device", "*")], now + 31 * DAY_MS), 0, "forgotten after thirty days");
    }

    #[test]
    fn a_block_refuses_only_that_session_for_a_day_and_survives_a_restart() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("answered.json");
        let mut f = Answered::open(&path).unwrap();
        let now = 5_000_000;
        assert_eq!(f.blocked_until("demo/claude-a", now), None);
        f.block("demo/claude-a", now).unwrap();
        assert_eq!(f.blocked_until("demo/claude-a", now + 10), Some(now + BLOCK_MS));
        assert_eq!(f.blocked_until("demo/claude-b", now + 10), None, "another session is not blocked");
        let mut again = Answered::open(&path).unwrap();
        assert_eq!(again.blocked_until("demo/claude-a", now + 20), Some(now + BLOCK_MS), "a restart forgets nothing");
        assert_eq!(again.blocked_until("demo/claude-a", now + BLOCK_MS), None, "and the block ends");
    }
}
