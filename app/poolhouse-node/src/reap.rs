//! Removing the boards that hold nothing but the starts and ends of their sessions.
//!
//! A board is a replicated log, so only one that no other device has ever synced is removed: a board another device holds a copy of
//! would not be made again from nothing without that device refusing the new log. A removed board is made again, empty, by the next
//! registration on it. A board is renamed to a trash name inside `boards/` before its files are deleted, so a deletion that stops
//! halfway leaves no live directory; the node removes what is left under a trash name when it starts and before it removes a board.

use std::path::Path;

use crate::error::Result;
use crate::node::Node;
use crate::row::Kind;

const TRASH: &str = ".trash-";

/// Delete every folder of ``boards`` that a removal left under a trash name; how many there were. A folder that cannot be deleted stays
/// for the next call.
pub fn clear_trash(boards: &Path) -> usize {
    let Ok(entries) = std::fs::read_dir(boards) else { return 0 };
    let left: Vec<_> = entries.filter_map(|e| e.ok()).filter(|e| e.file_name().to_string_lossy().starts_with(TRASH)).collect();
    left.iter().filter(|e| std::fs::remove_dir_all(e.path()).is_ok()).count()
}

impl Node {
    /// Remove every board that `reapable` names; how many there were.
    pub fn reap_idle_boards(&mut self) -> Result<usize> {
        let now = self.now_ms();
        let ids: Vec<String> = self.boards.keys().filter(|id| self.reapable(id, now)).cloned().collect();
        let root = self.dir.join("boards");
        clear_trash(&root);
        for id in &ids {
            self.boards.remove(id);
            let gone = root.join(format!("{TRASH}{id}-{now}"));
            std::fs::rename(root.join(id), &gone)?;
            // A deletion that fails leaves the folder under its trash name for `clear_trash`.
            let _ = std::fs::remove_dir_all(&gone);
        }
        Ok(ids.len())
    }

    /// Whether the board ``id`` may be removed at ``now``: a registration made it, every session on it ended and none for cause, its
    /// log is this device's own and holds only identities and heads, no other device ever synced it, no link or
    /// lease names it, it holds no file and no flagged entry, and its last row is older than `board_grace_ms`.
    fn reapable(&self, id: &str, now: u64) -> bool {
        let Some(hosted) = self.boards.get(id) else { return false };
        let (board, own) = (&hosted.board, hosted.board.origin());
        let only_endings = board.rows(own).iter().all(|r| match r.kind {
            Kind::Identity | Kind::Head => true,
            _ => false,
        });
        hosted.made_by.is_some()
            && hosted.names.all().values().all(|i| i.retired && matches!(i.retired_reason.as_str(), "" | "done" | "idle"))
            && board.origins().iter().all(|o| o == own)
            && only_endings
            && board.peer_fingerprints().is_empty()
            && hosted.quarantine.is_empty()
            && !std::fs::read_dir(self.dir.join("boards").join(id).join("files")).is_ok_and(|mut files| files.next().is_some())
            && board.rows(own).last().is_none_or(|r| now.saturating_sub(r.hlc.0) >= self.board_grace_ms)
            && !self.links.all().iter().any(|l| l.from == id || l.to == id)
            && !self.leases.table.leases.iter().any(|l| l.board == id)
    }
}
