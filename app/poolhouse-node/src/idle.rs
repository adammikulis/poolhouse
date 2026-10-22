//! What becomes of idle sessions. A session is idle when it is not retired and has neither been heard from within `child_beat_s` nor holds
//! a live lease; the node retires one that has been idle for `idle_retire_s`, whenever it lists the sessions or registers one. The person's
//! own session of a terminal is retired as soon as every process it was registered from has ended.

use crate::error::Result;
use crate::identity::Ident;
use crate::lease::live::is_alive;
use crate::node::Node;
use crate::writes::holds_lease;

/// Why ``session`` is retired now, when it is: `idle` after `idle_retire_s` unheard from, or `done` when it is the person's session of a
/// terminal whose processes have all ended (a lineage the node could not read is never ended).
fn ending(session: &Ident, now: u64, after: u64) -> Option<&'static str> {
    if session.retired {
        None
    } else if now.saturating_sub(session.seen_ms) > after {
        Some("idle")
    } else {
        let gone = !session.lineage.is_empty() && !session.lineage.iter().any(|a| is_alive(a.pid, Some(a.start)));
        (session.person && gone).then_some("done")
    }
}

/// Retire every session of ``board`` that holds no live lease and has been idle for `idle_retire_s`, or is the person's session of a
/// terminal that ended; how many.
pub fn sweep(node: &mut Node, board: &str) -> Result<usize> {
    let Some(hosted) = node.boards.get(board) else { return Ok(0) };
    let (now, after) = (hosted.board.now_ms(), node.writes.idle_retire_s.saturating_mul(1000));
    let stale: Vec<(String, &'static str)> = hosted.names.all().iter()
        .filter_map(|(n, i)| ending(i, now, after).filter(|_| !holds_lease(node, board, n)).map(|why| (n.clone(), why))).collect();
    for (name, why) in &stale {
        crate::sessions::end(node, board, name, "node", why)?;
    }
    Ok(stale.len())
}
