use serde::{Deserialize, Serialize};
use crate::error::{Error, Result};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(super) struct Stamp {
    pub boot_count: u64,
    pub uptime_ms: u64,
}

pub(super) struct LocalClock {
    boot_count: u64,
    uptime: Box<dyn Fn() -> Result<u64> + Send>,
}

impl LocalClock {
    pub fn sample(&self) -> Result<Stamp> {
        Ok(Stamp { boot_count: self.boot_count, uptime_ms: (self.uptime)()? })
    }

    #[cfg(target_os = "android")]
    pub fn android(boot_count: u64) -> Self {
        Self { boot_count, uptime: Box::new(native_uptime) }
    }

    #[cfg(test)]
    pub fn test(boot_count: u64, uptime: impl Fn() -> Result<u64> + Send + 'static) -> Self {
        Self { boot_count, uptime: Box::new(uptime) }
    }
}

impl Stamp {
    pub fn earned_since(&self, previous: &Stamp) -> Result<i64> {
        if self.boot_count < previous.boot_count {
            return Err(Error::Damaged("The phone's boot counter moved backwards.".into()));
        }
        if self.boot_count > previous.boot_count { return Ok(0) }
        let elapsed = self.uptime_ms.checked_sub(previous.uptime_ms).and_then(|n| i64::try_from(n).ok());
        elapsed.ok_or_else(|| Error::Damaged("The phone's awake clock moved backwards or overflowed.".into()))
    }
}

#[cfg(target_os = "android")]
fn native_uptime() -> Result<u64> {
    let mut time = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut time) } != 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    let seconds = u64::try_from(time.tv_sec).ok();
    let nanos = u64::try_from(time.tv_nsec).ok().filter(|n| *n < 1_000_000_000);
    seconds.and_then(|s| s.checked_mul(1000)).zip(nanos).and_then(|(ms, ns)| ms.checked_add(ns / 1_000_000))
        .ok_or_else(|| Error::Damaged("The phone's awake clock is unavailable.".into()))
}
