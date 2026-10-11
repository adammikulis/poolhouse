//! Board links: the only way anything crosses from one board to another.
//!
//! A link says the sessions of board `to` may use named channels (or the whole) of board
//! `from`, read-only or read-write. Nothing else crosses. Revoking stops it at once.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::error::{Error, Result};
use crate::fsutil::write_atomic;
use crate::row::valid_name;

/// How many links a board has open at once.
pub const MOST_ACTIVE: usize = 64;

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct Link {
    pub id: String,
    /// The board whose entries are shared.
    pub from: String,
    /// The board whose sessions may use them.
    pub to: String,
    /// The channels shared; empty means the whole board.
    pub channels: Vec<String>,
    pub write: bool,
    pub active: bool,
    /// The session that made the link, as `board/name`.
    pub by: String,
}

pub struct Links {
    path: PathBuf,
    all: Vec<Link>,
}

impl Links {
    pub fn open(path: &Path) -> Result<Links> {
        let all = match std::fs::read(path) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| Error::Damaged(format!("links unreadable: {e}")))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Vec::new(),
            Err(e) => return Err(e.into()),
        };
        Ok(Links { path: path.into(), all })
    }

    pub fn all(&self) -> &[Link] {
        &self.all
    }

    fn save(&self) -> Result<()> {
        write_atomic(&self.path, &serde_json::to_vec(&self.all)?)
    }

    /// Whether the board ``from`` can have another link: it has fewer than `MOST_ACTIVE` open.
    pub fn room_for(&self, from: &str) -> Result<()> {
        if self.all.iter().filter(|l| l.active && l.from == from).count() >= MOST_ACTIVE {
            return Err(Error::Quota(format!("a board has at most {MOST_ACTIVE} links open: end one first")));
        }
        Ok(())
    }

    /// Share ``channels`` of ``from`` with the sessions of ``to``.
    pub fn add(&mut self, from: &str, to: &str, channels: Vec<String>, write: bool, by: &str) -> Result<Link> {
        if from == to || !valid_name(to) || channels.iter().any(|c| !c.starts_with('#') || c.len() > 64) || channels.len() > 16 {
            return Err(Error::Invalid("a link joins two different boards through named #channels".into()));
        }
        self.room_for(from)?;
        let link = Link { id: format!("link-{}", self.all.len() + 1), from: from.into(), to: to.into(), channels, write, active: true, by: by.into() };
        self.all.push(link.clone());
        self.save()?;
        Ok(link)
    }

    /// Revoke link ``id`` of board ``from``; the link is kept, inactive, for the record.
    pub fn revoke(&mut self, from: &str, id: &str) -> Result<Link> {
        let link = self.all.iter_mut().find(|l| l.from == from && l.id == id && l.active)
            .ok_or_else(|| Error::Invalid("no such active link on this board".into()))?;
        link.active = false;
        let done = link.clone();
        self.save()?;
        Ok(done)
    }

    /// The channels of ``target`` that sessions of ``home`` may use, `None` when nothing is
    /// shared; an empty list means the whole board.
    pub fn grants(&self, home: &str, target: &str, write: bool) -> Option<Vec<String>> {
        let mut found: Option<Vec<String>> = None;
        for link in self.all.iter().filter(|l| l.active && l.from == target && l.to == home && (!write || l.write)) {
            if link.channels.is_empty() {
                return Some(Vec::new());
            }
            found.get_or_insert_with(Vec::new).extend(link.channels.iter().cloned());
        }
        found
    }

    /// Whether a session of ``home`` may use ``channel`` of ``target`` (writing or reading).
    pub fn allows(&self, home: &str, target: &str, channel: &str, write: bool) -> bool {
        self.grants(home, target, write).is_some_and(|c| c.is_empty() || c.iter().any(|x| x == channel))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_board_has_a_bounded_number_of_links_open_and_ending_one_makes_room() {
        let dir = tempfile::tempdir().unwrap();
        let mut links = Links::open(&dir.path().join("links.json")).unwrap();
        for _ in 0..MOST_ACTIVE {
            links.add("a", "b", vec![], false, "a/x").unwrap();
        }
        assert!(matches!(links.add("a", "b", vec![], false, "a/x"), Err(Error::Quota(_))));
        assert!(links.add("c", "b", vec![], false, "c/x").is_ok(), "another board has its own room");
        links.revoke("a", "link-1").unwrap();
        assert!(links.add("a", "b", vec![], true, "a/x").is_ok(), "a link ended is room for another");
        assert!(matches!(links.add("a", "b", vec![], true, "a/x"), Err(Error::Quota(_))));
    }
}
