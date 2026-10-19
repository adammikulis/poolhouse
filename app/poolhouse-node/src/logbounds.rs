//! How much one origin's log may hold: rows and bytes in all, a share of them for one session and for the sessions of one process tree
//! together, a reserve that only the node's own rows (an identity, the audit of a session ending) may use, and a last stretch that only
//! a head and a person's decisions may use, so a full log can still be signed and synced and a person's answer still lands.

use crate::error::Result;

/// Rows one origin's log keeps.
pub const MAX_LOG_ROWS: usize = 200_000;
/// Bytes of rows one origin's log keeps.
pub const MAX_LOG_BYTES: u64 = 128 * 1024 * 1024;
/// Rows at the end of `MAX_LOG_ROWS` that only the node's own rows use, the last `LAST_ROWS` of them for a head and a person's decisions.
pub const RESERVED_ROWS: usize = 2_000;
/// Bytes at the end of `MAX_LOG_BYTES` that only the node's own rows use, the last `LAST_BYTES` of them for a head and a person's decisions.
pub const RESERVED_BYTES: u64 = 8 * 1024 * 1024;
/// Rows at the very end of `MAX_LOG_ROWS` that only a head and the rows a person's decision makes use.
pub const LAST_ROWS: usize = 500;
/// Bytes at the very end of `MAX_LOG_BYTES` that only a head and the rows a person's decision makes use.
pub const LAST_BYTES: u64 = 2 * 1024 * 1024;
/// The share of the rows and of the bytes one session writes, in percent; its process tree's share (`LINEAGE_SHARE_PERCENT`) holds it.
pub const AUTHOR_SHARE_PERCENT: usize = 10;
/// The share of the rows and of the bytes the sessions of one process tree write together, in percent.
pub const LINEAGE_SHARE_PERCENT: usize = 25;

/// The bounds of one log: `Default` holds the constants above.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Bounds {
    pub rows: usize,
    pub bytes: u64,
    pub reserved_rows: usize,
    pub reserved_bytes: u64,
    pub last_rows: usize,
    pub last_bytes: u64,
    pub author_percent: usize,
    pub lineage_percent: usize,
}

impl Default for Bounds {
    fn default() -> Bounds {
        Bounds {
            rows: MAX_LOG_ROWS, bytes: MAX_LOG_BYTES, reserved_rows: RESERVED_ROWS, reserved_bytes: RESERVED_BYTES, last_rows: LAST_ROWS, last_bytes: LAST_BYTES,
            author_percent: AUTHOR_SHARE_PERCENT, lineage_percent: LINEAGE_SHARE_PERCENT,
        }
    }
}

/// Rows and bytes held.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Held {
    pub rows: usize,
    pub bytes: u64,
}

impl Held {
    pub fn add(&mut self, bytes: u64) {
        self.rows += 1;
        self.bytes += bytes;
    }

    /// What ``self`` and ``other`` hold together.
    pub fn plus(self, other: Held) -> Held {
        Held { rows: self.rows + other.rows, bytes: self.bytes + other.bytes }
    }
}

/// Who a row is written for, which decides what it may use.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Class {
    /// A row a session's own action asks for: held to the whole log less the reserve, to its session's share and to its process tree's.
    Author,
    /// A row the node writes because a session registered or ended (an identity, the audit of an ending): held to the whole log less the
    /// last stretch.
    Node,
    /// A head, or a row a person's decision makes: held only to the whole log.
    Last,
}

/// What an author and its process tree have in a log already.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Shares {
    pub author: Held,
    pub lineage: Held,
}

impl Bounds {
    /// Whether a row of ``bytes`` bytes by ``actor`` may be added to a log that holds ``log``, of which ``held`` is the author's and its
    /// process tree's.
    pub fn admit(&self, kind: Class, actor: &str, bytes: u64, log: Held, held: Shares) -> Result<()> {
        let (rows, size) = match kind {
            Class::Last => (self.rows, self.bytes),
            Class::Node => (self.rows.saturating_sub(self.last_rows), self.bytes.saturating_sub(self.last_bytes)),
            Class::Author => (self.rows.saturating_sub(self.reserved_rows), self.bytes.saturating_sub(self.reserved_bytes)),
        };
        if log.rows >= rows {
            return Err(crate::limits::refuse("this log has as many rows as a device keeps", self.rows, "MAX_LOG_ROWS", "logbounds.rs"));
        }
        if log.bytes + bytes > size {
            return Err(crate::limits::refuse("this log holds as many bytes as a device keeps", format!("{} bytes", self.bytes), "MAX_LOG_BYTES", "logbounds.rs"));
        }
        if kind == Class::Author {
            let over = |percent: usize, have: Held| have.rows >= self.rows * percent / 100 || have.bytes + bytes > self.bytes * percent as u64 / 100;
            let share = |percent: usize| format!("{percent} percent of the rows and of the bytes");
            if over(self.author_percent, held.author) {
                return Err(crate::limits::refuse(&format!("{actor} has written as much as one session may"), share(self.author_percent), "AUTHOR_SHARE_PERCENT", "logbounds.rs"));
            }
            if over(self.lineage_percent, held.lineage) {
                let what = format!("the sessions of the process tree {actor} belongs to have written as much as one tree may");
                return Err(crate::limits::refuse(&what, share(self.lineage_percent), "LINEAGE_SHARE_PERCENT", "logbounds.rs"));
            }
        }
        Ok(())
    }

    /// Whether ``incoming`` more rows of ``bytes`` bytes in all fit a copy of another device's log that holds ``held``.
    pub fn admit_copy(&self, held: Held, incoming: Held) -> Result<()> {
        if held.rows + incoming.rows > self.rows {
            return Err(crate::limits::refuse("this log would have more rows than a device keeps", self.rows, "MAX_LOG_ROWS", "logbounds.rs"));
        }
        if held.bytes + incoming.bytes > self.bytes {
            return Err(crate::limits::refuse("this log would hold more bytes than a device keeps", format!("{} bytes", self.bytes), "MAX_LOG_BYTES", "logbounds.rs"));
        }
        Ok(())
    }
}
