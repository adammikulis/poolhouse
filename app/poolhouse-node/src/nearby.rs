//! The pools a device in no pool hears on its network. They are listed for the person to choose from, each by the fingerprint words of
//! the device that answers for it; hearing one dials nothing and joins nothing.

use std::collections::BTreeMap;
use std::net::SocketAddr;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use crate::beacon::Beacon;

/// How long a device stays listed after its last beacon: the time a beacon is valid.
const LISTED_FOR: Duration = Duration::from_secs(60);
/// The devices listed together; while that many are listed, another is not added.
const MOST: usize = 32;

/// What the last beacon of one device said, and when it was heard.
struct Heard {
    pool: String,
    addr: SocketAddr,
    at: Instant,
}

#[derive(Default)]
pub struct Nearby(Mutex<BTreeMap<String, Heard>>);

impl Nearby {
    /// List the founded pool the beacon ``b`` names, under the device that sent it.
    pub fn heard(&self, b: &Beacon) {
        let Ok(mut all) = self.0.lock() else { return };
        all.retain(|_, h| h.at.elapsed() < LISTED_FOR);
        if all.len() < MOST || all.contains_key(&b.fingerprint) {
            all.insert(b.fingerprint.clone(), Heard { pool: b.pool.clone(), addr: SocketAddr::new(b.addr, b.port), at: Instant::now() });
        }
    }

    /// The listed devices: what `join_pool` is called with, and the words a person compares with the other device's screen.
    pub fn list(&self) -> Value {
        let Ok(all) = self.0.lock() else { return json!([]) };
        all.iter().filter(|(_, h)| h.at.elapsed() < LISTED_FOR)
            .map(|(fp, h)| json!({"fingerprint": fp, "words": crate::phone::words(fp), "pool": h.pool, "host": h.addr.ip().to_string(), "port": h.addr.port()}))
            .collect()
    }
}
