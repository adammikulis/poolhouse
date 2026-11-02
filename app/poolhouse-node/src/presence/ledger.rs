//! What the node remembers of asking each asker: when it may ask again after a no, and how many prompts it raised in the last day.
//!
//! Every count is of one asker (`board/name` of the person's session), so a session that fills its own allowance, or that was refused, takes
//! nothing from another's. The ledger is kept in a file, so a restart of the node (which a session can ask for) does not forget it.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::error::{Error, Result};
use crate::fsutil::write_atomic;
use crate::limits::floor;

/// After a prompt that was refused or not answered, how long before the node asks the same asker again: a session cannot pester the person.
pub(crate) const QUIET_MS: u64 = 10 * 60_000;
/// How many prompts the node raises for one asker in ten minutes and in a day: a password prompt every few minutes teaches a person to type
/// the password into anything, so a request over the limit waits.
pub(crate) const MOST_PER_TEN_MINUTES: usize = 3;
pub(crate) const MOST_PER_DAY: usize = 20;
const TEN_MINUTES_MS: u64 = 10 * 60_000;
const DAY_MS: u64 = 24 * 3_600_000;
const FILE: &str = "prompts.json";

#[derive(Default, Serialize, Deserialize, Clone, Debug, PartialEq)]
struct Account {
    quiet_until_ms: u64,
    raised: Vec<u64>,
}

/// The accounts of the askers, in memory or in a file beside the node's other state.
pub struct Ledger {
    path: Option<PathBuf>,
    accounts: BTreeMap<String, Account>,
}

impl Ledger {
    /// A ledger that is lost with the node: for a node that never serves a person.
    pub fn memory() -> Ledger {
        Ledger { path: None, accounts: BTreeMap::new() }
    }

    /// The ledger kept in ``dir``: what an earlier run of the node wrote, so a restart starts where it stopped. A file that cannot be read
    /// starts the ledger empty and says so, since the limits it held are the person's protection.
    pub fn kept(dir: &Path) -> Ledger {
        let path = dir.join(FILE);
        let accounts = match std::fs::read(&path) {
            Ok(bytes) => serde_json::from_slice(&bytes).unwrap_or_else(|e| {
                eprintln!("node: {} is damaged ({e}); the prompt limits start again", path.display());
                BTreeMap::new()
            }),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => BTreeMap::new(),
            Err(e) => {
                eprintln!("node: {} cannot be read ({e}); the prompt limits start again", path.display());
                BTreeMap::new()
            }
        };
        Ledger { path: Some(path), accounts }
    }

    /// Whether ``by`` was refused or went unanswered a moment ago.
    pub fn quiet(&self, by: &str, now: u64) -> bool {
        self.accounts.get(by).is_some_and(|a| now < a.quiet_until_ms)
    }

    /// Whether another prompt may be raised for ``by`` now, or the sentence that says it waits.
    pub fn allows(&self, by: &str, now: u64) -> Result<()> {
        if self.quiet(by, now) {
            return Err(floor("The person was asked a moment ago and did not say yes; wait a few minutes before asking again."));
        }
        let raised = self.accounts.get(by).map(|a| a.raised.as_slice()).unwrap_or_default();
        let (day, ten) = (raised.iter().filter(|t| now.saturating_sub(**t) < DAY_MS).count(), raised.iter().filter(|t| now.saturating_sub(**t) < TEN_MINUTES_MS).count());
        if day >= MOST_PER_DAY || ten >= MOST_PER_TEN_MINUTES {
            return Err(floor("The person was asked a few times already; this change waits until they can be asked again."));
        }
        Ok(())
    }

    /// Note that a prompt was raised for ``by``.
    pub fn raised(&mut self, by: &str, now: u64) -> Result<()> {
        self.accounts.entry(by.into()).or_default().raised.push(now);
        self.save(now)
    }

    /// Note that ``by`` was refused or went unanswered: the node asks it nothing for a while.
    pub fn refused(&mut self, by: &str, now: u64) -> Result<()> {
        self.accounts.entry(by.into()).or_default().quiet_until_ms = now + QUIET_MS;
        self.save(now)
    }

    fn save(&mut self, now: u64) -> Result<()> {
        for account in self.accounts.values_mut() {
            account.raised.retain(|t| now.saturating_sub(*t) < DAY_MS);
        }
        self.accounts.retain(|_, a| now < a.quiet_until_ms || !a.raised.is_empty());
        match &self.path {
            Some(path) => write_atomic(path, &serde_json::to_vec(&self.accounts)?).map_err(|e| Error::Denied(format!("the prompt limits could not be kept: {e}"))),
            None => Ok(()),
        }
    }
}
