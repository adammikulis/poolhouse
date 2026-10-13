//! Phones: the devices a person approves from. A phone is added by one scan, holds a key that signs approvals, a token that reads
//! them and a push key that opens its notifications; the node keeps a record of each and a person key for the first.
//!
//! | piece | does |
//! |---|---|
//! | `enrol` | the one-time code a person's command leaves, and a phone's addition |
//! | `calls` | the phone methods of the local API |
//! | `http` | the HTTPS endpoint a phone talks to |
//! | `gate` | who may hold a connection to it: a share each, and places kept for phones |
//! | `cert` | the P-256 certificate of that endpoint, which the code pins |
//! | `push` | the sealed notification and its delivery |

pub mod calls;
pub mod cert;
pub mod enrol;
pub mod gate;
pub mod http;
pub mod push;

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::approvals::decide::view;
use crate::approvals::fatigue::{QUIET, SILENT, URGENT};
use crate::approvals::Approval;
use crate::error::{Error, Result};
use crate::fsutil::{sha256_hex, write_atomic};
use crate::grants::person::P256;
use crate::node::Node;

/// The port a phone talks to when none is chosen.
pub const PORT: u16 = 7449;
/// The most phones a node holds, active or waiting for approval.
pub const MOST_PHONES: usize = 8;
pub const ACTIVE: &str = "active";
pub const PENDING: &str = "pending";
pub const REMOVED: &str = "removed";

/// Where a phone's notifications are sent, and the keys they are sealed to.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Push {
    /// The phone's push key, an uncompressed P-256 point in hex, and its secret in hex (16 bytes).
    pub p256dh: String,
    pub auth: String,
    /// An `https` address, or empty until the phone registers one.
    pub endpoint: String,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Phone {
    pub id: String,
    pub name: String,
    /// The fingerprint of its approval key, as a person key is known by.
    pub fingerprint: String,
    /// The approval key in hex, kept so a phone that waits for approval can be enrolled when it is given.
    pub key: String,
    pub token_sha256: String,
    pub state: String,
    pub push: Push,
    pub added_ms: u64,
    pub seen_ms: u64,
}

/// The phones of a node, kept in one atomically replaced file.
pub struct Phones {
    path: PathBuf,
    items: BTreeMap<String, Phone>,
}

fn same(a: &str, b: &str) -> bool {
    a.len() == b.len() && a.bytes().zip(b.bytes()).fold(0u8, |d, (x, y)| d | (x ^ y)) == 0
}

impl Phones {
    pub fn open(path: &Path) -> Result<Phones> {
        let items = match std::fs::read(path) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| Error::Damaged(format!("phones unreadable: {e}")))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => BTreeMap::new(),
            Err(e) => return Err(e.into()),
        };
        Ok(Phones { path: path.into(), items })
    }

    fn save(&self) -> Result<()> {
        write_atomic(&self.path, &serde_json::to_vec(&self.items)?)
    }

    pub fn all(&self) -> impl Iterator<Item = &Phone> {
        self.items.values()
    }

    pub fn get(&self, id: &str) -> Option<&Phone> {
        self.items.get(id)
    }

    /// The phone whose token is ``token`` and that is not removed.
    pub fn by_token(&self, token: &str) -> Option<&Phone> {
        let hash = sha256_hex(token.as_bytes());
        self.items.values().find(|p| p.state != REMOVED && same(&p.token_sha256, &hash))
    }

    pub fn by_key(&self, fingerprint: &str) -> Option<&Phone> {
        self.items.values().find(|p| p.fingerprint == fingerprint)
    }

    /// Whether the key ``fingerprint`` belonged to a phone that was removed.
    pub fn removed(&self, fingerprint: &str) -> bool {
        self.items.values().any(|p| p.fingerprint == fingerprint && p.state == REMOVED)
    }

    pub fn held(&self) -> usize {
        self.items.values().filter(|p| p.state != REMOVED).count()
    }

    pub fn add(&mut self, phone: Phone) -> Result<()> {
        if self.held() >= MOST_PHONES {
            return Err(Error::Quota("This device holds as many phones as it will; remove one first.".into()));
        }
        self.items.insert(phone.id.clone(), phone);
        self.save()
    }

    pub fn set_state(&mut self, id: &str, state: &str) -> Result<()> {
        if let Some(p) = self.items.get_mut(id) {
            p.state = state.into();
            if state == REMOVED {
                p.token_sha256.clear();
                p.push.endpoint.clear();
            }
        }
        self.save()
    }

    pub fn touch(&mut self, id: &str, now_ms: u64) -> Result<()> {
        if let Some(p) = self.items.get_mut(id).filter(|p| now_ms.saturating_sub(p.seen_ms) > 30_000) {
            p.seen_ms = now_ms;
            self.save()?;
        }
        Ok(())
    }

    pub fn set_endpoint(&mut self, id: &str, endpoint: &str) -> Result<()> {
        if let Some(p) = self.items.get_mut(id) {
            p.push.endpoint = endpoint.into();
        }
        self.save()
    }
}

/// A phone as a client reads it: never its token.
pub fn shown(p: &Phone, this: &str) -> Value {
    json!({"id": p.id, "name": p.name, "fingerprint": p.fingerprint, "state": p.state, "added_ms": p.added_ms, "seen_ms": p.seen_ms,
           "push": !p.push.endpoint.is_empty(), "this": p.id == this})
}

/// The first twenty-four characters of a key's fingerprint in groups of four, as a person compares them across two screens: 96 bits, so
/// no key can be ground to look like another.
pub fn words(fingerprint: &str) -> String {
    fingerprint.as_bytes().chunks(4).take(6).map(|c| String::from_utf8_lossy(c).into_owned()).collect::<Vec<_>>().join(" ")
}

/// The sealed message for each active phone that has an address: the request as the phone needs it to show and sign it, or, when that
/// does not fit in a push, the title and the id alone, and the phone fetches the rest.
pub fn announce(node: &mut Node, a: &Approval) {
    let now = node.now_ms();
    let quiet = a.urgency == QUIET;
    if a.urgency == SILENT || (quiet && node.fatigue.quiet_this_hour(now) >= node.fatigue.rules.quiet_per_hour) {
        return;
    }
    let full = view(node, a);
    let urgency = if quiet { QUIET } else { URGENT };
    let mut payload = json!({"v": 1, "id": a.id, "title": a.title, "agent": a.session, "risk": a.risk, "urgency": urgency, "expires_ms": a.expires_ms, "challenge": full["challenge"]});
    if payload.to_string().len() > push::MAX_PLAINTEXT {
        payload = json!({"v": 1, "id": a.id, "title": a.title, "agent": a.session, "risk": a.risk, "urgency": urgency, "expires_ms": a.expires_ms, "more": true});
    }
    let body = payload.to_string();
    let targets: Vec<Push> = node.phones.all().filter(|p| p.state == ACTIVE && !p.push.endpoint.is_empty()).map(|p| p.push.clone()).collect();
    if targets.is_empty() {
        return;
    }
    let by = if a.asker().is_empty() { String::new() } else { format!("session:{}", a.asker()) };
    let _ = node.fatigue.record_push(quiet, &by, now);
    let _ = node.approvals.update(&a.id, |x| x.notified = true);
    for t in targets {
        let sealed = crate::fsutil::unhex(&t.p256dh).zip(crate::fsutil::unhex(&t.auth)).ok_or(()).and_then(|(key, auth)| push::seal(body.as_bytes(), &key, &auth).map_err(|_| ()));
        if let Ok(sealed) = sealed {
            node.pusher.send(&t.endpoint, sealed);
        }
    }
}

/// A phone that waited for approval and was denied or timed out is removed without ever having been a key.
pub fn drop_pending(node: &mut Node, a: &Approval) {
    let id = a.action.get("phone").and_then(Value::as_str).unwrap_or("").to_string();
    if node.phones.get(&id).is_some_and(|p| p.state == PENDING) {
        let _ = node.phones.set_state(&id, REMOVED);
    }
}

/// Enrol the key of the phone a request names, and let it read.
pub fn activate(node: &mut Node, a: &Approval) -> Result<()> {
    let id = a.action.get("phone").and_then(Value::as_str).unwrap_or("");
    let phone = node.phones.get(id).filter(|p| p.state == PENDING).cloned().ok_or_else(|| Error::Invalid("That phone is no longer waiting to be added.".into()))?;
    if a.action.get("key").and_then(Value::as_str) != Some(phone.key.as_str()) {
        return Err(Error::Invalid("That phone's key is not the key the request named, so nothing was added.".into()));
    }
    let now = node.now_ms();
    node.persons.add_phone_key(P256, &phone.key, &phone.name, now)?;
    node.phones.set_state(id, ACTIVE)?;
    let _ = node.record_event("phone.add", &phone.fingerprint, &format!("{}; approved by {}", phone.name, a.decided_by.get(..12).unwrap_or("")));
    Ok(())
}

/// Take a phone and its key off the node; the last key stays.
pub fn remove(node: &mut Node, a: &Approval) -> Result<()> {
    let id = a.action.get("phone").and_then(Value::as_str).unwrap_or("");
    let phone = node.phones.get(id).filter(|p| p.state == ACTIVE).cloned().ok_or_else(|| Error::Invalid("That phone is not one of this device's phones.".into()))?;
    if a.action.get("fingerprint").and_then(Value::as_str) != Some(phone.fingerprint.as_str()) {
        return Err(Error::Invalid("That phone's key is not the key the request named, so nothing was removed.".into()));
    }
    let now = node.now_ms();
    node.persons.remove(&phone.fingerprint, now)?;
    node.phones.set_state(id, REMOVED)?;
    let _ = node.record_event("phone.remove", &phone.fingerprint, &format!("{}; approved by {}", phone.name, a.decided_by.get(..12).unwrap_or("")));
    Ok(())
}
