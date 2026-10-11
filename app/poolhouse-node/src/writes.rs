//! What one session may write: how often it sends, how many announcements, how many messages wait unread in an inbox and from one
//! sender, how many notes an author keeps, and how many live subagents a session has. A count is kept in memory with the others of
//! `limits::Rates` (a restart clears it) and a use is counted when it was made.

use std::path::Path;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use crate::error::{Error, Result};
use crate::fold::{ANNOUNCE, DIRECT};
use crate::identity::Holder;
use crate::node::Node;
use crate::row::Kind;

pub const LIMITS_FILE: &str = "write-limits.json";

/// The numbers a session's writes are held to; `write-limits.json` in the state directory sets the ones it names.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub struct WriteLimits {
    /// Messages, notes and claims one session writes in `window_s`; a subagent writes `child_sends` of them, and counts against its parent's too.
    pub sends: usize,
    pub child_sends: usize,
    pub window_s: f64,
    /// Announcements one session writes in `announce_window_s` before the board is told it is over; an announcement is never refused for it.
    pub announces: usize,
    pub announce_window_s: f64,
    /// Direct messages unread in one session's inbox, and unread in it from one sender.
    pub inbox_pending: usize,
    pub unread_per_sender: usize,
    /// Notes one author keeps on a board.
    pub notes_per_author: usize,
    /// Live subagents one session has: not retired, and active within `child_life_s`.
    pub children: usize,
    pub child_life_s: u64,
}

impl Default for WriteLimits {
    fn default() -> WriteLimits {
        WriteLimits {
            sends: 30, child_sends: 10, window_s: 60.0, announces: 6, announce_window_s: 600.0,
            inbox_pending: 500, unread_per_sender: 100, notes_per_author: 500, children: 8, child_life_s: 8 * 3600,
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

    fn window(&self) -> Duration {
        Duration::from_secs_f64(self.window_s.max(0.0))
    }

    fn announce_window(&self) -> Duration {
        Duration::from_secs_f64(self.announce_window_s.max(0.0))
    }
}

/// What a post was let through as, for `wrote` to count.
#[derive(Clone, Copy, Debug)]
pub struct Admitted {
    announce: bool,
    within: bool,
}

fn key(what: &str, who: &Holder) -> String {
    format!("{what} {}/{}", who.board, who.name)
}

/// The parent of ``who``, when it is a subagent.
fn parent_of(node: &Node, who: &Holder) -> Option<String> {
    node.boards.get(&who.board).and_then(|h| h.names.get(&who.name)).map(|i| i.parent.clone()).filter(|p| !p.is_empty())
}

/// Whether ``who`` may send once more in the window: a subagent within its own count and its parent's.
fn may_send(node: &mut Node, who: &Holder) -> bool {
    let (limits, parent) = (node.writes.clone(), parent_of(node, who));
    let most = if parent.is_some() { limits.child_sends.min(limits.sends) } else { limits.sends };
    node.rates.permits(&key("send", who), most, limits.window())
        && parent.is_none_or(|p| node.rates.permits(&key("send", &Holder { board: who.board.clone(), name: p }), limits.sends, limits.window()))
}

fn count_send(node: &mut Node, who: &Holder) {
    let (window, parent) = (node.writes.window(), parent_of(node, who));
    node.rates.record(&key("send", who), window);
    if let Some(p) = parent {
        node.rates.record(&key("send", &Holder { board: who.board.clone(), name: p }), window);
    }
}

fn too_fast(who: &Holder, what: &str) -> Error {
    Error::Quota(format!("{} wrote {what} as fast as it may: try again in a minute", who.name))
}

/// Refuse a claim sent faster than ``who`` may send; `sent` counts it once it was made.
pub fn check_send(node: &mut Node, who: &Holder) -> Result<()> {
    if may_send(node, who) { Ok(()) } else { Err(too_fast(who, "claims")) }
}

/// Count one write by ``who`` that was made.
pub fn sent(node: &mut Node, who: &Holder) {
    count_send(node, who);
}

/// The direct messages waiting unread for ``to``, and those among them from ``from``; `None` when ``to`` is not a session of this device.
fn waiting(node: &mut Node, board: &str, to: &str, from: &str) -> Result<Option<(usize, usize)>> {
    let Some(seen) = node.boards.get(board).and_then(|h| h.names.get(to)).map(|i| i.inbox_read.clone()) else { return Ok(None) };
    let mut counts = (0, 0);
    for e in node.view(board)?.iter().filter(|e| e.channel == DIRECT && e.fields.get("to").and_then(Value::as_str) == Some(to)) {
        if e.seq > seen.get(&e.origin).copied().unwrap_or(0) {
            counts.0 += 1;
            counts.1 += usize::from(e.sender == from);
        }
    }
    Ok(Some(counts))
}

/// Hold a post to the limits before it is written: sends in a window, the notes of an author, the unread messages of a recipient.
/// An announcement is never refused; `wrote` tells the board when it is over. A refusal counts for nothing.
pub fn check_post(node: &mut Node, board: &str, who: &Holder, actor: &str, kind: Kind, fields: &Map<String, Value>) -> Result<Admitted> {
    let to = fields.get("to").and_then(Value::as_str).unwrap_or("");
    let announce = kind == Kind::Message && to == ANNOUNCE;
    let within = may_send(node, who);
    if !within && !announce {
        return Err(too_fast(who, if kind == Kind::Note { "notes" } else { "messages" }));
    }
    let limits = node.writes.clone();
    if kind == Kind::Note {
        let wrote = node.view(board)?.iter().filter(|e| e.kind == Kind::Note && e.sender == actor).count();
        if wrote >= limits.notes_per_author {
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
    Ok(Admitted { announce, within })
}

/// Count a post that was written, and tell the board when an announcement went over what a session announces.
pub fn wrote(node: &mut Node, board: &str, who: &Holder, actor: &str, post: Admitted) -> Result<()> {
    if post.within {
        count_send(node, who);
    }
    if !post.announce {
        return Ok(());
    }
    let (limits, announces) = (node.writes.clone(), key("announce", who));
    let over = !post.within || !node.rates.permits(&announces, limits.announces, limits.announce_window());
    node.rates.record(&announces, limits.announce_window());
    if over {
        let detail = format!("{actor} announced past {} in {:.0} s or faster than it may send", limits.announces, limits.announce_window_s);
        node.host(board)?.board.append(Kind::Audit, actor, json!({"event": "announce_over_quota", "subject": actor, "detail": detail}), "")?;
    }
    Ok(())
}

/// Refuse a registration that would give ``parent`` more live subagents than it may have; a session already registered is not new.
pub fn check_children(node: &Node, board: &str, parent: &str, harness: &str, session: &str) -> Result<()> {
    let Some(hosted) = node.boards.get(board).filter(|_| !parent.is_empty()) else { return Ok(()) };
    if hosted.names.find(harness, session).is_some() {
        return Ok(());
    }
    let now = hosted.board.now_ms();
    let live = hosted.names.all().values().filter(|i| i.parent == parent && !i.retired && now.saturating_sub(i.seen_ms) <= node.writes.child_life_s * 1000).count();
    if live >= node.writes.children {
        return Err(Error::Quota(format!("{parent} already has {live} live subagents: retire one before registering another")));
    }
    Ok(())
}
