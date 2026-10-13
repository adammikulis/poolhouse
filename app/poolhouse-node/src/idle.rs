//! How many live sessions a board holds and what becomes of idle ones. A session is idle when it is not retired and has neither been heard
//! from within `child_beat_s` nor holds a live lease. The node refuses to register a main session when its harness already has
//! `sessions_per_harness` live ones on the board, or the process lineage (terminal) it comes from has `sessions_per_lineage`; idle sessions
//! count for neither, and the node retires one that has been idle for `idle_retire_s`, whenever it lists the sessions or registers one.

use crate::error::{Error, Result};
use crate::lineage::{limit_key, Anchor};
use crate::node::Node;
use crate::writes::{holds_lease, is_live};

/// Refuse a main session of ``harness`` that would take the board past the live sessions it may hold; one already registered and not
/// retired is not new. Sessions idle past `idle_retire_s` are retired first.
pub fn admit_main(node: &mut Node, board: &str, harness: &str, session: &str, lineage: &[Anchor]) -> Result<()> {
    sweep(node, board)?;
    let Some(hosted) = node.boards.get(board) else { return Ok(()) };
    if hosted.names.find(harness, session).and_then(|n| hosted.names.get(n)).is_some_and(|i| !i.retired) {
        return Ok(());
    }
    let now = hosted.board.now_ms();
    let (mut here, mut from) = (0, 0);
    let key = limit_key(lineage);
    for (name, ident) in hosted.names.all().iter().filter(|(_, i)| i.parent.is_empty() && i.harness == harness) {
        if is_live(node, board, name, ident, now) {
            here += 1;
            from += usize::from(!lineage.is_empty() && limit_key(&ident.lineage) == key);
        }
    }
    let most = &node.writes;
    if here >= most.sessions_per_harness {
        return Err(Error::Quota(format!("this device already has {here} live {harness} sessions: end one before starting another")));
    }
    if from >= most.sessions_per_lineage {
        return Err(Error::Quota(format!("this terminal already has {from} live {harness} sessions: end one before starting another")));
    }
    Ok(())
}

/// Retire every session of ``board`` that has been idle for `idle_retire_s` and holds no live lease; how many.
pub fn sweep(node: &mut Node, board: &str) -> Result<usize> {
    let Some(hosted) = node.boards.get(board) else { return Ok(0) };
    let (now, after) = (hosted.board.now_ms(), node.writes.idle_retire_s.saturating_mul(1000));
    let stale: Vec<String> = hosted.names.all().iter()
        .filter(|(n, i)| !i.retired && now.saturating_sub(i.seen_ms) > after && !holds_lease(node, board, n))
        .map(|(n, _)| n.clone()).collect();
    for name in &stale {
        crate::sessions::end(node, board, name, "node", "idle")?;
    }
    Ok(stale.len())
}
