//! What becomes of idle sessions. A session is idle when it is not retired and has neither been heard from within `child_beat_s` nor holds
//! a live lease; the node retires one that has been idle for `idle_retire_s`, whenever it lists the sessions or registers one.

use crate::error::Result;
use crate::node::Node;
use crate::writes::holds_lease;

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
