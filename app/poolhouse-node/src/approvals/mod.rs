//! Approvals: an agent asks a person for grants, the node writes the question in plain words, and a person's key answers it.
//!
//! The question is made by the node (`content`) from fields it checked, never from the agent's words, and the answer is a signature
//! over a challenge that contains that text (`challenge`), so what the person saw is what was signed. A request is decided once
//! (`decide`), by a key enrolled on this node: a phone's P-256 key or a person's Ed25519 key. Nothing a session can send decides it.
//!
//! | piece | does |
//! |---|---|
//! | `content` | the plain-words text of a request, the risk of each action, the pool line |
//! | `context` | what else a person weighs, signed with the request: what they said, the verdict, the taint, the undo window, the steps |
//! | `flags` | what looks off about a request, from what the node knows of the session |
//! | `fatigue` | what keeps a request that is made again and again from wearing a person down: one item, holds, caps, who is told |
//! | `challenge` | the bytes a key signs, and the grant entries an approval writes |
//! | `decide` | the signature check, the one-time state change and what an approval does |
//! | `calls` | the methods of the local API |

pub mod calls;
pub mod challenge;
pub mod content;
pub mod context;
pub mod decide;
pub mod fatigue;
pub mod flags;
pub mod request;
pub mod summary;

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::approvals::context::Context;
use crate::error::{Error, Result};
use crate::fsutil::write_atomic;

pub const PENDING: &str = "pending";
pub const APPROVED: &str = "approved";
pub const DENIED: &str = "denied";
pub const EXPIRED: &str = "expired";
pub const CANCELLED: &str = "cancelled";
pub const FAILED: &str = "failed";
/// A request a held or over-busy requester made: kept, not shown to anyone until a person lets it through.
pub const HELD: &str = "held";

/// The kinds of approval: grants an agent asks for, a phone's own addition, a phone's removal, and the summary of requests kept back.
pub const GRANTS: &str = "grants";
pub const HOLD: &str = "hold";
pub const PHONE_ADD: &str = "phone_add";
pub const PHONE_REMOVE: &str = "phone_remove";

/// How many requests wait on a node, how many one session has waiting, and how many a session makes in ten minutes.
pub const MOST_PENDING: usize = 64;
pub const MOST_PENDING_PER_SESSION: usize = 8;
pub const MOST_REQUESTS: usize = 6;
pub const REQUEST_WINDOW_MS: u64 = 10 * 60 * 1000;
/// The longest a request lives, and how long it lives when the agent says nothing.
pub const MAX_TTL_MS: u64 = 24 * 3600 * 1000;
pub const DEFAULT_TTL_MS: u64 = 30 * 60 * 1000;
pub const MIN_TTL_MS: u64 = 60 * 1000;
/// How many decided requests are kept, and for how long.
const KEEP_DECIDED: usize = 200;
const KEEP_DECIDED_MS: u64 = 7 * 24 * 3600 * 1000;

/// One request and what became of it.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Approval {
    /// `ap-` and 16 hex digits.
    pub id: String,
    pub kind: String,
    /// The board the request was made on; the pool board for a phone's own requests.
    pub board: String,
    /// The requesting session; empty for a request a phone made about itself.
    pub session: String,
    /// What the request carries, as the challenge holds it.
    pub action: Value,
    /// The plain-words text: what the person is shown and what is signed.
    pub text: String,
    pub title: String,
    /// `routine`, `sensitive` or `irreversible`.
    pub risk: String,
    pub nonce: String,
    pub created_ms: u64,
    pub expires_ms: u64,
    pub state: String,
    pub decided_ms: u64,
    /// The fingerprint of the key that decided it.
    pub decided_by: String,
    /// Why a request failed or was refused after a valid signature, or what was done.
    pub reason: String,
    /// The grant ids written when it was approved.
    pub grants: Vec<String>,
    pub seq: u64,
    /// What else the person weighs, signed with the request.
    #[serde(default)]
    pub context: Context,
    /// The number the agent is to show the person; never sent to a phone. Empty when no number has to be matched.
    #[serde(default)]
    pub pick_code: String,
    /// How the request is delivered: `urgent` (a push that interrupts), `quiet` (a push that does not) or `silent` (the list only).
    #[serde(default = "urgent")]
    pub urgency: String,
    /// How many times the same request was made again while this one waited.
    #[serde(default)]
    pub retries: u64,
    /// What makes another request the same as this one (`fatigue::same`).
    #[serde(default)]
    pub same: String,
    /// The device the request came from: this one for a local session.
    #[serde(default)]
    pub source: String,
    /// While the request is held, the level of the ledger that holds its requester (`fatigue::requester`).
    #[serde(default)]
    pub held_by: String,
    /// Whether a push told the person about it: withdrawing a request the person was told of is not free.
    #[serde(default)]
    pub notified: bool,
}

fn urgent() -> String {
    "urgent".into()
}

impl Approval {
    /// A pending request of no kind, with nothing in it: the base the makers of requests fill in.
    pub fn empty() -> Approval {
        Approval {
            id: String::new(), kind: String::new(), board: String::new(), session: String::new(), action: Value::Null, text: String::new(), title: String::new(),
            risk: String::new(), nonce: String::new(), created_ms: 0, expires_ms: 0, state: PENDING.into(), decided_ms: 0, decided_by: String::new(),
            reason: String::new(), grants: Vec::new(), seq: 0, context: Context::default(), pick_code: String::new(), urgency: urgent(), retries: 0,
            same: String::new(), source: String::new(), held_by: String::new(), notified: false,
        }
    }

    pub fn pending(&self) -> bool {
        self.state == PENDING
    }

    /// Pending, or kept back and not yet let through: not over.
    pub fn live(&self) -> bool {
        self.state == PENDING || self.state == HELD
    }

    /// The grants a request of kind `grants` asks for, as the challenge names them.
    pub fn asks(&self) -> Vec<Value> {
        self.action.get("grants").and_then(Value::as_array).cloned().unwrap_or_default()
    }
}

#[derive(Serialize, Deserialize, Default)]
struct File {
    seq: u64,
    items: Vec<Approval>,
}

/// Every request this node holds, kept in one atomically replaced file.
pub struct Approvals {
    path: PathBuf,
    seq: u64,
    items: Vec<Approval>,
    /// Changes whenever a request is added or changes, so a phone that waits is answered when anything did. Not kept across a restart.
    version: u64,
}

impl Approvals {
    pub fn open(path: &Path) -> Result<Approvals> {
        let file: File = match std::fs::read(path) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| Error::Damaged(format!("approvals unreadable: {e}")))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => File::default(),
            Err(e) => return Err(e.into()),
        };
        Ok(Approvals { path: path.into(), seq: file.seq, items: file.items, version: 1 })
    }

    pub fn version(&self) -> u64 {
        self.version
    }

    pub fn get(&self, id: &str) -> Option<&Approval> {
        self.items.iter().find(|a| a.id == id)
    }

    pub fn all(&self) -> &[Approval] {
        &self.items
    }

    pub fn pending_count(&self) -> usize {
        self.items.iter().filter(|a| a.pending()).count()
    }

    pub fn pending_of(&self, session: &str) -> usize {
        self.items.iter().filter(|a| a.pending() && a.session == session).count()
    }

    /// Keep ``a`` as the newest request.
    pub fn add(&mut self, mut a: Approval) -> Result<()> {
        self.seq += 1;
        a.seq = self.seq;
        self.items.push(a);
        self.save()
    }

    /// Change the request ``id`` with ``edit`` and keep the result on disk.
    pub fn update(&mut self, id: &str, edit: impl FnOnce(&mut Approval)) -> Result<()> {
        let a = self.items.iter_mut().find(|a| a.id == id).ok_or_else(|| Error::Invalid("no request has that id".into()))?;
        edit(a);
        self.save()
    }

    /// The requests that were pending or held and are now over, marked expired at ``now_ms``, each with whether it was kept back.
    pub fn sweep(&mut self, now_ms: u64) -> Result<Vec<(Approval, bool)>> {
        let mut ended = Vec::new();
        for a in self.items.iter_mut().filter(|a| a.live() && a.expires_ms <= now_ms) {
            let held = a.state == HELD;
            a.state = EXPIRED.into();
            a.decided_ms = now_ms;
            ended.push((a.clone(), held));
        }
        if !ended.is_empty() {
            self.save()?;
        }
        Ok(ended)
    }

    fn save(&mut self) -> Result<()> {
        self.version += 1;
        let now = crate::poolops::wall_ms();
        let mut decided: Vec<u64> = self.items.iter().filter(|a| !a.live()).map(|a| a.seq).collect();
        decided.sort_unstable();
        let floor = decided.len().checked_sub(KEEP_DECIDED).map_or(0, |i| decided[i]);
        self.items.retain(|a| a.live() || (a.seq >= floor && now.saturating_sub(a.decided_ms.max(a.created_ms)) <= KEEP_DECIDED_MS));
        write_atomic(&self.path, &serde_json::to_vec(&File { seq: self.seq, items: self.items.clone() })?)
    }
}
