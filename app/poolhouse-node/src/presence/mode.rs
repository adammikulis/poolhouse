//! How a node asks: nobody (`Auto`). The node raises no dialog of the operating system and reads no terminal: nothing in Poolhouse waits for a
//! person at a prompt the node puts up. A test of this crate puts its own `Presence` on `Node::firsts`, a library field no binary sets.

use super::{Asking, Presence};
use crate::error::Result;

/// How long a prompt waits for the person.
pub(crate) const ASK_FOR_MS: u64 = 120_000;

/// The text of a prompt as the system shows it: plain, short, with nothing in it a script could be written through.
pub(crate) fn plain(what: &str) -> String {
    what.chars().filter(|c| c.is_ascii_alphanumeric() || " .,_-/()".contains(*c)).take(super::card::MOST_SENTENCE).collect()
}

/// Whether ``state`` is the device's own node directory of the account this process runs as (`~/.poolhouse/node`, the account's home from
/// the password database and never from `HOME`), the same directory and no other under it: the only place a real prompt is raised.
pub(crate) fn is_the_real_state(state: &std::path::Path, account_home: &std::path::Path) -> bool {
    match (std::fs::canonicalize(account_home.join(".poolhouse").join("node")), std::fs::canonicalize(state)) {
        (Ok(real), Ok(mine)) => mine == real,
        _ => false,
    }
}

#[cfg(unix)]
pub(crate) fn on_real_home(state: &std::path::Path) -> bool {
    let mut entry: libc::passwd = unsafe { std::mem::zeroed() };
    let (mut buf, mut found) = (vec![0 as libc::c_char; 4096], std::ptr::null_mut());
    // SAFETY: entry and buf are valid for the call and buf's length is passed; getpwuid_r sets found to entry or null.
    let ok = unsafe { libc::getpwuid_r(libc::geteuid(), &mut entry, buf.as_mut_ptr(), buf.len(), &mut found) };
    if ok != 0 || found.is_null() || entry.pw_dir.is_null() {
        return false;
    }
    // SAFETY: pw_dir is a NUL-terminated string that lives in buf.
    let home = unsafe { std::ffi::CStr::from_ptr(entry.pw_dir) }.to_string_lossy().into_owned();
    is_the_real_state(state, std::path::Path::new(&home))
}

/// On Windows the account's directory comes from the system; when it can not be read nothing is known, so every directory counts as the real one.
#[cfg(windows)]
pub(crate) fn on_real_home(state: &std::path::Path) -> bool {
    crate::sys::account_home().is_none_or(|home| is_the_real_state(state, &home))
}

#[cfg(not(any(unix, windows)))]
pub(crate) fn on_real_home(_state: &std::path::Path) -> bool {
    true
}

/// Nobody is asked: the user of this computer runs the node, so what the prompt would have confirmed is confirmed at once. The ticket, the
/// secret and the key a yes makes are exactly what they were, made by the node and kept in its memory; only the wait for a person is gone.
pub struct Auto;

/// An answer that was given as it was asked for.
struct Answered;

impl Asking for Answered {
    fn poll(&mut self) -> Option<bool> {
        Some(true)
    }
}

impl Presence for Auto {
    fn kind(&self) -> &'static str {
        "auto"
    }

    fn begin(&mut self, _what: &str, _terminal: &str) -> Result<Box<dyn Asking>> {
        Ok(Box::new(Answered))
    }
}

/// The presence a node asks with unless it was started with another: nobody is asked.
pub fn default_presence() -> Box<dyn Presence> {
    Box::new(Auto)
}
