//! How much one origin's log may hold: rows and bytes in all, a share of them for one author, and a last stretch that only the node's
//! own rows (a head, an identity, an audit of a session ending) may use, so a full log can still be signed, synced and a session ended.

use crate::error::Result;

/// Rows one origin's log keeps.
pub const MAX_LOG_ROWS: usize = 200_000;
/// Bytes of rows one origin's log keeps.
pub const MAX_LOG_BYTES: u64 = 128 * 1024 * 1024;
/// Rows at the end of `MAX_LOG_ROWS` that only the node's own rows use.
pub const RESERVED_ROWS: usize = 2_000;
/// Bytes at the end of `MAX_LOG_BYTES` that only the node's own rows use.
pub const RESERVED_BYTES: u64 = 8 * 1024 * 1024;
/// The share of the rows and of the bytes one author writes, in percent.
pub const AUTHOR_SHARE_PERCENT: usize = 25;

/// The bounds of one log: `Default` holds the constants above.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Bounds {
    pub rows: usize,
    pub bytes: u64,
    pub reserved_rows: usize,
    pub reserved_bytes: u64,
    pub author_percent: usize,
}

impl Default for Bounds {
    fn default() -> Bounds {
        Bounds { rows: MAX_LOG_ROWS, bytes: MAX_LOG_BYTES, reserved_rows: RESERVED_ROWS, reserved_bytes: RESERVED_BYTES, author_percent: AUTHOR_SHARE_PERCENT }
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
}

/// Who a row is written for, which decides what it may use.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Class {
    /// A row a session's own action asks for: held to the whole log less the reserve, and to its author's share.
    Author,
    /// A row the node writes for itself (a head, an identity, an audit of a session ending): held only to the whole log.
    Node,
}

impl Bounds {
    /// Whether a row of ``bytes`` bytes by ``actor`` may be added to a log that holds ``log``, of which ``author`` is the author's.
    pub fn admit(&self, kind: Class, actor: &str, bytes: u64, log: Held, author: Held) -> Result<()> {
        let (rows, size) = match kind {
            Class::Node => (self.rows, self.bytes),
            Class::Author => (self.rows.saturating_sub(self.reserved_rows), self.bytes.saturating_sub(self.reserved_bytes)),
        };
        if log.rows >= rows {
            return Err(crate::limits::refuse("this log has as many rows as a device keeps", self.rows, "MAX_LOG_ROWS", "logbounds.rs"));
        }
        if log.bytes + bytes > size {
            return Err(crate::limits::refuse("this log holds as many bytes as a device keeps", format!("{} bytes", self.bytes), "MAX_LOG_BYTES", "logbounds.rs"));
        }
        if kind == Class::Author {
            let (share_rows, share_bytes) = (self.rows * self.author_percent / 100, self.bytes * self.author_percent as u64 / 100);
            let share = format!("{} percent of the rows and of the bytes", self.author_percent);
            if author.rows >= share_rows || author.bytes + bytes > share_bytes {
                return Err(crate::limits::refuse(&format!("{actor} has written as much as one author may"), share, "AUTHOR_SHARE_PERCENT", "logbounds.rs"));
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
