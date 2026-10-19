//! Removing the boards that hold nothing but the starts and ends of their sessions.
//!
//! A board is a replicated log, so only one that no other device has ever synced is removed: a board another device holds a copy of
//! would not be made again from nothing without that device refusing the new log. A removed board is made again, empty, by the next
//! registration on it.

use crate::error::Result;
use crate::node::Node;
use crate::row::Kind;

impl Node {
    /// Remove every board that `reapable` names; how many there were.
    pub fn reap_idle_boards(&mut self) -> Result<usize> {
        let now = self.now_ms();
        let ids: Vec<String> = self.boards.keys().filter(|id| self.reapable(id, now)).cloned().collect();
        for id in &ids {
            self.boards.remove(id);
            std::fs::remove_dir_all(self.dir.join("boards").join(id))?;
        }
        Ok(ids.len())
    }

    /// Whether the board ``id`` may be removed at ``now``: a registration made it, every session on it ended and none for cause, its
    /// log is this device's own and holds only identities, heads and the audits of endings, no other device ever synced it, no link or
    /// lease names it, and its last row is older than `board_grace_ms`.
    fn reapable(&self, id: &str, now: u64) -> bool {
        let Some(hosted) = self.boards.get(id) else { return false };
        let (board, own) = (&hosted.board, hosted.board.origin());
        let only_endings = board.rows(own).iter().all(|r| match r.kind {
            Kind::Identity | Kind::Head => true,
            Kind::Audit => r.body["event"] == "retire",
            _ => false,
        });
        hosted.made_by.is_some()
            && hosted.names.all().values().all(|i| i.retired && matches!(i.retired_reason.as_str(), "" | "done" | "idle"))
            && board.origins().iter().all(|o| o == own)
            && only_endings
            && board.peer_fingerprints().is_empty()
            && board.rows(own).last().is_none_or(|r| now.saturating_sub(r.hlc.0) >= self.board_grace_ms)
            && !self.links.all().iter().any(|l| l.from == id || l.to == id)
            && !self.leases.table.leases.iter().any(|l| l.board == id)
    }
}
