//! With which member devices this device shares each of its boards. The device that serves a board decides, for a list, a pull and a
//! push, and its own sync keeps to the same answer; nothing a member says changes it, and the choices are never replicated.
//!
//! A member a person or a delegate let in is shared every board unless this device says otherwise. A member nobody vouched for (it
//! joined an `open` pool without a code) is shared nothing until this device shares it (`board_share`).

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use crate::cert::valid_fingerprint;
use crate::error::{Error, Result};
use crate::fsutil::write_atomic;
use crate::membership::Status;
use crate::node::Node;

/// The grant, and the ticket operation, that changes a choice.
pub const SHARE: &str = "board_share";
/// How the record names a device that joined without a code.
const UNVOUCHED: &str = "open";

/// What this device chose for one member: for every board, and for single boards over that.
#[derive(Serialize, Deserialize, Default)]
struct Choice {
    #[serde(default)]
    every: Option<bool>,
    #[serde(default)]
    boards: BTreeMap<String, bool>,
}

#[derive(Serialize, Deserialize, Default)]
struct File {
    pool: String,
    devices: BTreeMap<String, Choice>,
}

/// The choices made in one pool, kept in one atomically replaced file.
pub struct Sharing {
    path: PathBuf,
    file: File,
}

impl Sharing {
    /// The choices in ``path``. A file that cannot be read stops the node: reading it as empty would share a board that was withheld.
    pub fn open(path: &Path) -> Result<Sharing> {
        let file = match std::fs::read(path) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| Error::Damaged(format!("{} unreadable: {e}", path.display())))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => File::default(),
            Err(e) => return Err(e.into()),
        };
        Ok(Sharing { path: path.into(), file })
    }

    /// What was chosen for ``board`` and the device ``fingerprint`` in ``pool``; None when nothing was.
    fn chosen(&self, pool: &str, fingerprint: &str, board: &str) -> Option<bool> {
        let choice = self.file.devices.get(fingerprint).filter(|_| self.file.pool == pool)?;
        choice.boards.get(board).copied().or(choice.every)
    }

    /// Share ``board`` with the device, or withhold it; without a board, every board, which replaces the choices for single ones.
    fn set(&mut self, pool: &str, fingerprint: &str, board: Option<&str>, shared: bool) -> Result<()> {
        if self.file.pool != pool {
            self.file = File { pool: pool.into(), devices: BTreeMap::new() };
        }
        let choice = self.file.devices.entry(fingerprint.into()).or_default();
        match board {
            Some(board) => {
                choice.boards.insert(board.into(), shared);
            }
            None => *choice = Choice { every: Some(shared), boards: BTreeMap::new() },
        }
        write_atomic(&self.path, &serde_json::to_vec(&self.file)?)
    }
}

impl Node {
    /// Whether this device shares ``board`` with the device ``fingerprint``: an active member, by this device's choice, else by whether
    /// someone vouched for it.
    pub fn shares(&self, board: &str, fingerprint: &str) -> bool {
        let Some(device) = self.members.get(fingerprint).filter(|d| d.status == Status::Active) else { return false };
        self.sharing.chosen(&self.members.id, fingerprint, board).unwrap_or(device.by != UNVOUCHED)
    }

    /// The boards of this device that it shares with the device ``fingerprint``.
    pub fn boards_shared_with(&self, fingerprint: &str) -> Vec<String> {
        self.boards.keys().filter(|board| self.shares(board, fingerprint)).cloned().collect()
    }

    /// Whether this device shares with ``fingerprint`` every board it was not told otherwise about.
    pub fn shares_by_default(&self, fingerprint: &str) -> bool {
        self.shares("", fingerprint)
    }
}

fn text<'a>(p: &'a Map<String, Value>, key: &str) -> Result<&'a str> {
    match p.get(key) {
        None => Ok(""),
        Some(Value::String(s)) => Ok(s),
        _ => Err(Error::Invalid(format!("{key} is text"))),
    }
}

/// The local `board_share`: a session that holds the grant `board_share` for the device, or the person's own session with the ticket
/// the node made for exactly this change.
pub fn call(node: &mut Node, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let (device, board, ticket) = (text(p, "device")?, text(p, "board")?, text(p, "ticket")?);
    let shared = p.get("shared").and_then(Value::as_bool).ok_or_else(|| Error::Invalid("shared is true or false".into()))?;
    if !valid_fingerprint(device) || device == node.cert.fingerprint() || !node.members.is_active(device) {
        return Err(Error::Invalid("boards are shared with another active member of this pool, named by its fingerprint".into()));
    }
    if !board.is_empty() && !node.boards.contains_key(board) {
        return Err(Error::Invalid("this device holds no board of that name".into()));
    }
    let by = if ticket.is_empty() {
        let holder = crate::poolapi::authorize(node, token, SHARE, device)?;
        format!("{}/{}", holder.board, holder.name)
    } else {
        let by = crate::poolapi::asker_by(node, token).ok_or_else(|| Error::Denied("A yes at the computer's prompt is for a session the node asks it of.".into()))?;
        let mut args = p.clone();
        args.remove("ticket");
        node.spend_op(&by, ticket, SHARE, &args)?;
        by
    };
    let pool = node.members.id.clone();
    node.sharing.set(&pool, device, (!board.is_empty()).then_some(board), shared)?;
    let what = if board.is_empty() { "every board".into() } else { format!("the board {board}") };
    node.record_event(SHARE, device, &format!("{what} {} by {by}", if shared { "shared" } else { "withheld" }))?;
    Ok(json!({"device": device, "board": board, "shared": shared}))
}
