//! What one session may write: how many messages wait unread in an inbox and from one sender, and how many notes an author keeps.
//! The unread messages and the notes are counted by `tally` as rows arrive and again from the log when the node starts.

use std::path::Path;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::error::{Error, Result};
use crate::identity::Ident;
use crate::lease::live;
use crate::lease::types::State;
use crate::node::Node;
use crate::row::Kind;

pub const LIMITS_FILE: &str = "write-limits.json";

/// The numbers a session's writes are held to; `write-limits.json` in the state directory sets the ones it names.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub struct WriteLimits {
    /// Direct messages unread in one session's inbox, and unread in it from one sender.
    pub inbox_pending: usize,
    pub unread_per_sender: usize,
    /// Notes one author keeps on a board.
    pub notes_per_author: usize,
    /// Seconds a session may go unheard before it is idle, unless it holds a live lease.
    pub child_beat_s: u64,
    /// Seconds a session may be idle before the node retires it.
    pub idle_retire_s: u64,
}

impl Default for WriteLimits {
    fn default() -> WriteLimits {
        WriteLimits {
            inbox_pending: 500, unread_per_sender: 100, notes_per_author: 500, child_beat_s: 1800, idle_retire_s: 86_400,
        }
    }
}

impl WriteLimits {
    /// The limits in force for the node whose state lives in ``dir``.
    pub fn open(dir: &Path) -> Result<WriteLimits> {
        match std::fs::read(dir.join(LIMITS_FILE)) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| Error::Damaged(format!("{LIMITS_FILE} unreadable: {e}"))),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(WriteLimits::default()),
            Err(e) => Err(e.into()),
        }
    }
}

/// The direct messages waiting unread for ``to``, and those among them from ``from``; `None` when ``to`` is not a session of this device.
fn waiting(node: &mut Node, board: &str, to: &str, from: &str) -> Result<Option<(usize, usize)>> {
    let Some(seen) = node.boards.get(board).and_then(|h| h.names.get(to)).map(|i| i.inbox_read.clone()) else { return Ok(None) };
    Ok(Some(node.tally(board)?.unread(to, &seen, from)))
}

/// Hold a post to the limits before it is written: the notes of an author and the unread messages of a recipient. A refusal counts for nothing.
pub fn check_post(node: &mut Node, board: &str, actor: &str, kind: Kind, fields: &Map<String, Value>) -> Result<()> {
    let to = fields.get("to").and_then(Value::as_str).unwrap_or("");
    let limits = node.writes.clone();
    if kind == Kind::Note {
        if node.tally(board)?.notes_by(actor) >= limits.notes_per_author {
            return Err(Error::Quota(format!("{actor} already wrote {} notes", limits.notes_per_author)));
        }
    }
    if kind == Kind::Message && !to.is_empty() && !to.starts_with('#') {
        if let Some((all, mine)) = waiting(node, board, to, actor)? {
            if all >= limits.inbox_pending {
                return Err(Error::Quota(format!("{to} has {} unread messages; wait for it to read", limits.inbox_pending)));
            }
            if mine >= limits.unread_per_sender {
                return Err(Error::Quota(format!("{actor} already has {} unread messages waiting for {to}", limits.unread_per_sender)));
            }
        }
    }
    Ok(())
}

/// Whether ``name`` of ``board`` holds a lease that is held and whose holder process is still the one that took it.
pub fn holds_lease(node: &Node, board: &str, name: &str) -> bool {
    let at = node.leases.now();
    node.leases.table.leases.iter().any(|l| {
        l.board == board && l.name == name && l.state == State::Held && l.expires_ms > at && (l.pid == 0 || live::is_alive(l.pid, l.pid_start))
    })
}

/// Whether the session ``name`` of ``board`` is live: not retired, and either holding a lease that is held and whose holder process is
/// still the one that took it, or heard from within `child_beat_s`. A session that is not retired and not live is idle.
pub fn is_live(node: &Node, board: &str, name: &str, ident: &Ident, now: u64) -> bool {
    !ident.retired && (now.saturating_sub(ident.seen_ms) <= node.writes.child_beat_s * 1000 || holds_lease(node, board, name))
}
