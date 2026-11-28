//! What the node does with its pool record: admit and remove devices by the pool's acts, and write each change to the board named
//! `pool` so it is part of the signed history.

use std::io::Read;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

use crate::error::Result;
use crate::gov::charter::MOST_DEVICE_NAME;
use crate::membership::{shown_name, Device};
use crate::row::Kind;

/// The board that carries the pool's own history (members added and put out, policy changes).
pub const POOL_BOARD: &str = "pool";
const ACTOR: &str = "node";

pub fn wall_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_millis() as u64)
}

/// The file in the state directory that holds the name the person gave this device (`poolhouse.node_pool.name_device` writes it).
const NAME_FILE: &str = "device-name";
/// The most bytes read of it: more than the longest name a pool lists.
const MOST_NAME_BYTES: u64 = 512;

fn host_name() -> String {
    let name = crate::sys::hostname();
    let name: String = name.chars().filter(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '.' | '_')).take(40).collect();
    if name.is_empty() { "device".into() } else { name }
}

/// The name the person gave the device of the state directory ``dir``, as a pool can list it; None when they gave none. Only a regular
/// file is read: a link is not followed, and a pipe holds nobody.
fn typed_name(dir: &Path) -> Option<String> {
    let mut bytes = Vec::new();
    crate::sys::open_regular(&dir.join(NAME_FILE))?.take(MOST_NAME_BYTES).read_to_end(&mut bytes).ok()?;
    let name = shown_name(String::from_utf8_lossy(&bytes).trim(), MOST_DEVICE_NAME);
    Some(name).filter(|n| !n.is_empty())
}

impl crate::node::Node {
    /// Milliseconds since the epoch.
    pub fn now_ms(&self) -> u64 {
        wall_ms()
    }

    /// What a pool this device founds or joins lists it as: the name the person gave it, else its host name. A pool keeps the name it was
    /// given then.
    pub(crate) fn own_name(&self) -> String {
        typed_name(&self.dir).unwrap_or_else(host_name)
    }

    /// List this device itself as the only member of the pool of one it has until it founds or joins a pool.
    pub fn enrol_self(&mut self) {
        if self.members.is_unfounded() {
            let (der, name, now) = (self.cert.der.clone(), self.own_name(), wall_ms());
            self.members.list_self(&der, &name, now);
        }
    }

    /// Write one event to the pool board; the pool record stays the authority.
    pub fn record_event(&mut self, event: &str, subject: &str, detail: &str) -> Result<()> {
        let body = json!({"event": event, "subject": subject.chars().take(100).collect::<String>(), "detail": detail.chars().take(250).collect::<String>()});
        self.host(POOL_BOARD)?.board.append(Kind::Audit, ACTOR, body, "")?;
        Ok(())
    }

    /// Let the device with this certificate in by an admit the pool's admins sign (or this device signs where the pool lets it); the
    /// pool board notes it.
    pub fn enrol_device(&mut self, der: &[u8], name: &str, by: &str, window: bool) -> Result<Device> {
        self.gov_admit(der, name, by, window)
    }

    /// Put a device out for good by a removal the pool's admins sign; the pool board notes it.
    pub fn remove_device(&mut self, fingerprint: &str) -> Result<Device> {
        self.gov_remove(fingerprint)
    }

    /// Everything that waits for the person: the questions of the system's own prompt, then every open request they are to answer on their
    /// phone (an agent's, a phone's addition or removal), each with the sentence to tell them.
    fn waiting(&self, now: u64) -> Vec<Value> {
        let mut out = self.firsts.waiting(now);
        out.extend(self.approvals.all().iter().filter(|a| a.pending()).map(|a| {
            json!({"by": a.asker(), "say": format!("waiting for you: {}", a.title), "sentence": a.title, "via": "phone", "id": a.id, "left_ms": a.expires_ms.saturating_sub(now)})
        }));
        out
    }

    /// The pool as a local client sees it: members with what is known of each, the policy,
    /// whether a join code is open, and this node's listening address.
    pub fn pool_status(&self) -> Value {
        let me = self.cert.fingerprint();
        let now = wall_ms();
        let members: Vec<Value> = self.members.devices().iter().map(|d| {
            let f = self.facts.peers.get(&d.fingerprint);
            let (reach, hint) = if d.status == crate::membership::Status::Active { crate::reach::of(d.fingerprint == me, f) } else { ("none", String::new()) };
            // a pool of one lists this device by the name it would found or join with now
            let name = if d.fingerprint == me && self.members.is_unfounded() { self.own_name() } else { d.name.clone() };
            let mut row = json!({
                "reach": reach, "reach_hint": hint, "network": if d.fingerprint == me { self.network.as_str() } else { f.map_or("", |f| f.network.as_str()) },
                "fingerprint": d.fingerprint, "name": name, "status": d.status, "since": d.at, "by": d.by, "self": d.fingerprint == me,
                "connected": f.is_some_and(|f| f.sessions > 0), "addr": f.and_then(|f| f.addr.clone()),
                "last_seen_ms": f.map_or(0, |f| f.last_seen_ms), "last_sync_ms": f.map_or(0, |f| f.last_sync_ms),
                "last_error": f.map(|f| f.last_error.clone()).unwrap_or_default(),
            });
            let build = if d.fingerprint == me { json!({"build": self.build.id, "build_seq": self.build.seq, "wire": self.build.wire, "build_state": "self", "fix": ""}) } else { self.member_build(&d.fingerprint, &d.name) };
            row.as_object_mut().expect("an object").extend(build.as_object().cloned().unwrap_or_default());
            row
        }).collect();
        json!({
            "pool": self.members.id, "project": self.members.project, "policy": self.members.policy.name(), "fingerprint": me, "device_key": self.custody.status(), "listen": self.facts.listen,
            "governance": self.gov_view(),
            "waiting": self.waiting(now),
            "beacon": self.facts.beacon, "join_code_open": self.facts.join_code_until_ms > now,
            "join_code_expires_in_s": self.facts.join_code_until_ms.saturating_sub(now) / 1000, "members": members, "build": self.build_status(),
            "leases": {"held": self.leases.table.leases.iter().filter(|l| l.state == crate::lease::types::State::Held).count(), "queued": self.leases.table.leases.iter().filter(|l| l.state == crate::lease::types::State::Queued).count()},
        })
    }
}
