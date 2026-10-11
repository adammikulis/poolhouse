//! How this device reaches a member, said in `pool_status` with what to do when it is not by dialing.
//!
//! `direct`: this device dialed the member and it answered. `channel`: this device cannot dial it, but it dials this device and keeps
//! a connection open, so requests go down that (docs/node.md, "Reaching a device nothing can dial"). `none`: no address answers
//! and no connection is open. `self`: this device.

use crate::facts::PeerFacts;

/// The fix for a WSL2 device in NAT mode, which no other device can dial.
pub const WSL_NAT_HINT: &str = "this device looks like WSL2 behind NAT: set networkingMode=mirrored in .wslconfig and run wsl --shutdown, or run `python -m poolhouse.device_setup --yes` on it";

/// The fix for a member nothing can dial and that has not dialed in.
pub const NONE_HINT: &str = "no member can dial it yet: have it dial a member, or give it an address";

/// The fix for a member that is reached only over the connection it opened.
pub const CHANNEL_HINT: &str = "it is reached only over the connection it opened to this device, so only for the members it dials: give it an address the others can dial (allow its node through its firewall), or run `python -m poolhouse.device_setup --yes` on it";

/// How a member is reached and, when that is not directly, the exact fix: `(reach, reach_hint)`.
pub fn of(is_self: bool, facts: Option<&PeerFacts>) -> (&'static str, String) {
    if is_self {
        return ("self", String::new());
    }
    let Some(f) = facts else { return ("none", NONE_HINT.into()) };    if f.addr.is_some() && f.last_sync_ms > 0 && f.fails == 0 && f.last_error.is_empty() {
        return ("direct", String::new());
    }
    let reach = if f.channel { "channel" } else { "none" };
    let hint = if f.network == "wsl-nat" {
        WSL_NAT_HINT.to_string()
    } else if f.channel {
        CHANNEL_HINT.to_string()
    } else if let Some(addr) = f.addr.as_deref().filter(|_| f.fails > 0) {
        format!("it did not answer at {addr}: {NONE_HINT}")
    } else {
        NONE_HINT.to_string()
    };
    (reach, hint)
}
