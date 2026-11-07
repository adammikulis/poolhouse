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
use crate::fsutil::{random_hex, sha256_hex, write_atomic};
use crate::row::canonical;

pub const DAY_MS: u64 = 24 * 3_600_000;
/// How long a scope that was declined is remembered and shown with the next request for it.
pub const DECLINED_MS: u64 = 30 * DAY_MS;
/// What a block lasts. The card a person blocks on says so.
pub const BLOCK_MS: u64 = DAY_MS;
const MOST_DECLINED: usize = 400;
/// A lift challenge lives for two minutes, long enough to read and answer the card.
pub const LIFT_TTL_MS: u64 = 120_000;

#[derive(Serialize, Deserialize, Clone, Debug)]
struct Declined {
    board: String,
    action: String,
    resource: String,
    at_ms: u64,
}

#[derive(Serialize, Deserialize, Clone, Default)]
struct File {
    declined: Vec<Declined>,
    /// The sessions (`board/name`) a person blocked, and when each block ends.
    blocked: BTreeMap<String, u64>,
}

/// The scopes a person declined and the sessions a person blocked on a node, kept in one atomically replaced file.
pub struct Answered {
    path: PathBuf,
    file: File,
    lifts: BTreeMap<String, Lift>,
    uncertain: bool,
    #[cfg(test)]
    fail_after_replace: bool,
    #[cfg(test)]
    fail_reconcile_sync: bool,
}

#[derive(Clone, Debug)]
pub struct Lift {
    pub who: String,
    pub until_ms: u64,
    pub expires_ms: u64,
    pub key: String,
    pub authority: String,
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
    pub fn uncertain(&self) -> bool { self.uncertain }

    pub fn open(path: &Path) -> Result<Answered> {
        let file = match std::fs::read(path) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| Error::Damaged(format!("answered unreadable: {e}")))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => File::default(),
            Err(e) => return Err(e.into()),
        };
        Ok(Answered { path: path.into(), file, lifts: BTreeMap::new(), uncertain: false, #[cfg(test)] fail_after_replace: false, #[cfg(test)] fail_reconcile_sync: false })
    }

    fn write_file(&self, next: &File) -> Result<()> {
        write_atomic(&self.path, &serde_json::to_vec(next)?)?;
        #[cfg(test)]
        if self.fail_after_replace { return Err(Error::Damaged("injected error after replacement".into())); }
        Ok(())
    }

    /// A failed atomic write may have replaced the file before directory sync failed. Never keep stale state or challenges.
    fn reconcile_after_error(&mut self) {
        self.lifts.clear();
        #[cfg(test)]
        if self.fail_reconcile_sync { self.uncertain = true; return; }
        if self.path.parent().is_none_or(|parent| crate::fsutil::sync_dir(parent).is_err()) {
            self.uncertain = true;
            return;
        }
        match std::fs::read(&self.path).ok().and_then(|bytes| serde_json::from_slice(&bytes).ok()) {
            Some(file) => self.file = file,
            None => self.uncertain = true,
        }
    }

    /// Forget what is old enough to have stopped counting.
    fn prune(&mut self, now: u64) {
        self.file.declined.retain(|d| now.saturating_sub(d.at_ms) < DECLINED_MS);
        self.file.blocked.retain(|_, until| *until > now);
        self.lifts.retain(|_, lift| lift.expires_ms > now && self.file.blocked.get(&lift.who) == Some(&lift.until_ms));
    }

    /// Remember that a person declined these scopes of ``board``.
    pub fn note_declined(&mut self, board: &str, asks: &[(&str, &str)], now: u64) -> Result<()> {
        if self.uncertain { return Err(Error::Damaged("answered state must be reopened".into())); }
        let mut next = self.file.clone();
        for (action, resource) in asks {
            next.declined.push(Declined { board: board.into(), action: (*action).into(), resource: (*resource).into(), at_ms: now });
        }
        let extra = next.declined.len().saturating_sub(MOST_DECLINED);
        next.declined.drain(..extra);
        if let Err(error) = self.write_file(&next) {
            self.reconcile_after_error();
            return Err(error);
        }
        self.file = next;
        Ok(())
    }

    /// How often the most declined of these scopes was declined before on ``board``.
    pub fn declined_before(&mut self, board: &str, asks: &[(&str, &str)], now: u64) -> u64 {
        self.prune(now);
        asks.iter().map(|(a, r)| self.file.declined.iter().filter(|d| d.board == board && d.action == *a && d.resource == *r).count() as u64).max().unwrap_or(0)
    }

    /// A person blocked the session ``who`` (`board/name`): nothing it asks is taken for `BLOCK_MS`.
    pub fn block(&mut self, who: &str, now: u64) -> Result<()> {
        if self.uncertain { return Err(Error::Damaged("answered state must be reopened".into())); }
        self.lifts.retain(|_, lift| lift.who != who);
        let mut next = self.file.clone();
        next.blocked.insert(who.to_string(), now.saturating_add(BLOCK_MS));
        if let Err(error) = self.write_file(&next) {
            self.reconcile_after_error();
            return Err(error);
        }
        self.file = next;
        Ok(())
    }

    /// When the block of the session ``who`` ends, if it is blocked now.
    pub fn blocked_until(&mut self, who: &str, now: u64) -> Option<u64> {
        if self.uncertain { return Some(u64::MAX); }
        self.prune(now);
        self.file.blocked.get(who).copied()
    }

    /// The active blocks for a person authorized to read them.
    pub fn blocks(&mut self, now: u64) -> BTreeMap<String, u64> {
        self.prune(now);
        self.file.blocked.clone()
    }

    /// Create an ephemeral one-use challenge for the block currently shown. It is lost on restart or reblock.
    pub fn offer_lift(&mut self, who: &str, key: &str, authority: &str, now: u64, ttl_ms: u64) -> Result<(String, Lift)> {
        if self.uncertain { return Err(Error::Damaged("answered state must be reopened".into())); }
        self.prune(now);
        let until_ms = self.file.blocked.get(who).copied().ok_or_else(|| Error::Invalid("That session is not blocked.".into()))?;
        self.lifts.retain(|_, lift| lift.who != who || lift.key != key || lift.authority == authority);
        if let Some((nonce, lift)) = self.lifts.iter().find(|(_, lift)| lift.who == who && lift.key == key) {
            return Ok((nonce.clone(), lift.clone()));
        }
        let nonce = random_hex(16)?;
        let lift = Lift { who: who.into(), until_ms, expires_ms: now.saturating_add(ttl_ms).min(until_ms), key: key.into(), authority: authority.into() };
        if self.lifts.contains_key(&nonce) { return Err(Error::Damaged("block lift nonce collision".into())); }
        self.lifts.insert(nonce.clone(), lift.clone());
        Ok((nonce, lift))
    }

    pub fn lift_challenge(&mut self, nonce: &str, now: u64) -> Option<Lift> {
        self.prune(now);
        self.lifts.get(nonce).cloned()
    }

    /// Persist first, then consume all challenges for that block. On an uncertain write, reload and invalidate every challenge.
    pub fn lift(&mut self, nonce: &str, now: u64) -> Result<bool> {
        let Some(challenge) = self.lift_challenge(nonce, now) else { return Ok(false) };
        let mut next = self.file.clone();
        next.blocked.remove(&challenge.who);
        if let Err(error) = self.write_file(&next) {
            self.reconcile_after_error();
            return Err(error);
        }
        self.file = next;
        self.lifts.retain(|_, lift| lift.who != challenge.who);
        Ok(true)
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

    #[test]
    fn a_lift_is_one_use_and_cannot_remove_a_later_block_or_survive_restart() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("answered.json");
        let mut f = Answered::open(&path).unwrap();
        let now = 5_000_000;
        f.block("demo/a", now).unwrap();
        f.block("demo/b", now).unwrap();
        let (old, _) = f.offer_lift("demo/a", "key", "revision", now, LIFT_TTL_MS).unwrap();
        f.block("demo/a", now).unwrap(); // The clock may repeat.
        assert!(!f.lift(&old, now).unwrap());
        let (nonce, _) = f.offer_lift("demo/a", "key", "revision", now, LIFT_TTL_MS).unwrap();
        for _ in 0..300 { assert_eq!(f.offer_lift("demo/a", "key", "revision", now, LIFT_TTL_MS).unwrap().0, nonce); }
        let (changed_authority, _) = f.offer_lift("demo/a", "key", "new revision", now, LIFT_TTL_MS).unwrap();
        assert!(!f.lift(&nonce, now).unwrap(), "an authority change invalidates the old offer");
        let nonce = changed_authority;
        let (other, _) = f.offer_lift("demo/a", "other-key", "revision", now, LIFT_TTL_MS).unwrap();
        assert!(f.lift(&nonce, now).unwrap());
        assert!(!f.lift(&nonce, now).unwrap());
        assert!(!f.lift(&other, now).unwrap());
        assert_eq!(f.blocked_until("demo/b", now), Some(now + BLOCK_MS));
        let (restart, _) = f.offer_lift("demo/b", "key", "revision", now, LIFT_TTL_MS).unwrap();
        let mut reopened = Answered::open(&path).unwrap();
        assert!(!reopened.lift(&restart, now).unwrap());
        assert_eq!(reopened.blocked_until("demo/b", now), Some(now + BLOCK_MS));
        let (expired, _) = reopened.offer_lift("demo/b", "key", "revision", now, LIFT_TTL_MS).unwrap();
        assert!(!reopened.lift(&expired, now + LIFT_TTL_MS).unwrap());
    }

    #[test]
    fn failed_lift_write_invalidates_the_challenge_and_retains_the_block() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("answered.json");
        let mut f = Answered::open(&path).unwrap();
        f.block("demo/a", 1).unwrap();
        let (nonce, _) = f.offer_lift("demo/a", "key", "revision", 1, LIFT_TTL_MS).unwrap();
        f.path = dir.path().join("missing").join("answered.json");
        assert!(f.lift(&nonce, 2).is_err());
        assert_eq!(f.blocked_until("demo/a", 2), Some(u64::MAX), "unknown ledger state fails closed");
        assert!(f.lift_challenge(&nonce, 2).is_none());
        assert!(f.offer_lift("demo/a", "key", "revision", 2, LIFT_TTL_MS).is_err());
        let mut reopened = Answered::open(&path).unwrap();
        let (fresh, _) = reopened.offer_lift("demo/a", "key", "revision", 2, LIFT_TTL_MS).unwrap();
        assert!(reopened.lift(&fresh, 2).unwrap());
    }

    #[test]
    fn an_error_after_replacement_reloads_state_and_invalidates_old_lifts() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("answered.json");
        let mut f = Answered::open(&path).unwrap();
        f.block("demo/a", 10).unwrap();
        let (old, _) = f.offer_lift("demo/a", "key", "revision", 10, LIFT_TTL_MS).unwrap();
        f.fail_after_replace = true;
        assert!(f.block("demo/a", 20).is_err());
        assert_eq!(f.blocked_until("demo/a", 20), Some(20 + BLOCK_MS));
        assert!(!f.lift(&old, 20).unwrap());
        f.fail_after_replace = false;
        f.note_declined("demo", &[("sync_now", "*")], 20).unwrap();
        assert_eq!(Answered::open(&path).unwrap().blocked_until("demo/a", 20), Some(20 + BLOCK_MS));
        let (fresh, _) = f.offer_lift("demo/a", "key", "revision", 20, LIFT_TTL_MS).unwrap();
        f.fail_after_replace = true;
        assert!(f.lift(&fresh, 20).is_err());
        assert_eq!(f.blocked_until("demo/a", 20), None);
        assert!(f.lift_challenge(&fresh, 20).is_none());
    }

    #[test]
    fn an_unconfirmed_directory_sync_blocks_further_mutations_until_reopened() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("answered.json");
        let mut f = Answered::open(&path).unwrap();
        f.block("demo/a", 10).unwrap();
        let (old, _) = f.offer_lift("demo/a", "key", "revision", 10, LIFT_TTL_MS).unwrap();
        f.fail_after_replace = true;
        f.fail_reconcile_sync = true;
        assert!(f.block("demo/a", 20).is_err());
        assert!(f.uncertain());
        assert_eq!(f.blocked_until("demo/a", 20), Some(u64::MAX));
        assert!(f.offer_lift("demo/a", "key", "revision", 20, LIFT_TTL_MS).is_err());
        assert!(f.note_declined("demo", &[("sync_now", "*")], 20).is_err());
        assert!(!f.lift(&old, 20).unwrap());
        let mut reopened = Answered::open(&path).unwrap();
        assert_eq!(reopened.blocked_until("demo/a", 20), Some(20 + BLOCK_MS));
    }

    #[test]
    fn a_computer_card_allows_the_full_prompt_and_ticket_time_but_no_more() {
        let mut f = ledger();
        let now = 5_000_000;
        let ttl = crate::presence::card::CARD_FOR_MS + crate::presence::card::TICKET_FOR_MS;
        f.block("demo/a", now).unwrap();
        let (nonce, _) = f.offer_lift("demo/a", "computer:pool/person", "revision", now, ttl).unwrap();
        assert!(f.lift(&nonce, now + crate::presence::card::CARD_FOR_MS).unwrap(), "a slow prompt can still be answered");
        f.block("demo/a", now).unwrap();
        let (expired, _) = f.offer_lift("demo/a", "computer:pool/person", "revision", now, ttl).unwrap();
        assert!(!f.lift(&expired, now + ttl).unwrap());
    }
}
