//! The awake time every hold, window and expiry is measured in.
//!
//! A hold is not read from the wall clock or from the time a signer wrote on an act: it is the time this device has been awake
//! since it first saw the act. The clock counts the monotonic time of this process (which does not run while the machine sleeps)
//! on top of the total saved at the last checkpoint, so a restart does not give time back and a hold never ends early for a device
//! that was off. A test or a rig freezes it and moves it by hand.

use std::sync::Mutex;
use std::time::Instant;

pub struct Awake {
    origin: Instant,
    base_ms: i64,
    frozen: Mutex<Option<i64>>,
}

impl Awake {
    /// A clock that already counts ``base_ms`` of awake time (what the last checkpoint saved).
    pub fn new(base_ms: i64) -> Awake {
        Awake { origin: Instant::now(), base_ms, frozen: Mutex::new(None) }
    }

    /// Milliseconds awake in all.
    pub fn now_ms(&self) -> i64 {
        match self.frozen.lock() {
            Ok(f) if f.is_some() => f.unwrap_or(0),
            _ => self.base_ms.saturating_add(i64::try_from(self.origin.elapsed().as_millis()).unwrap_or(i64::MAX)),
        }
    }

    /// Stop the clock where it is; it moves only by `advance_ms` from here.
    pub fn freeze(&self) {
        let now = self.now_ms();
        if let Ok(mut f) = self.frozen.lock() {
            f.get_or_insert(now);
        }
    }

    /// Move a frozen clock forward by ``ms``; a clock that is running is frozen first.
    pub fn advance_ms(&self, ms: i64) {
        self.freeze();
        if let Ok(mut f) = self.frozen.lock() {
            *f = f.map(|v| v.saturating_add(ms));
        }
    }
}
