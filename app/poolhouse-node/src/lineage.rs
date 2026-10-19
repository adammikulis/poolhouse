//! Where a registration came from: the process that made the request and the processes above it, up to the shell of its
//! terminal (the leader of a session that has a controlling terminal) or, with none, to the top.
//!
//! A main session is registered once, from some lineage; a later `register` of the same native session is answered only to
//! a process that shares one of those processes: the same process tree, or the same terminal. A harness that starts each
//! command in a session of its own is still its own terminal's, since the walk goes on past a session with no terminal. A
//! process that double-forks and is left to the system's first process has no parents to share. The processes are read from
//! the operating system by the peer id of the socket, so a request cannot name its own. Windows does not report the peer
//! yet: there nothing is recorded and nothing is refused (`ENFORCED`).
//!
//! Every token is bound to the lineage that registered it, and a grant is spent only by a request from a process that shares
//! it (`Asking`).

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::error::{Error, Result};
use crate::lease::live::inspect;
use crate::sys::Stream;

/// Whether this platform tells the node which process is on the other end of a request.
pub const ENFORCED: bool = cfg!(unix);
const MOST_ANCHORS: usize = 32;

/// One process of a lineage: its id and when it started, so a reused id is not mistaken for it.
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
pub struct Anchor {
    pub pid: u32,
    pub start: u64,
}

impl Anchor {
    /// The anchor a person signed, from the param `anchor`: an object of a process id and a start time, nothing else.
    pub fn signed(p: &Map<String, Value>) -> Result<Anchor> {
        let shape = || Error::Invalid("anchor is the terminal's process: {pid, start}".into());
        let map = p.get("anchor").and_then(Value::as_object).filter(|m| m.len() == 2).ok_or_else(shape)?;
        let pid = map.get("pid").and_then(Value::as_u64).and_then(|n| u32::try_from(n).ok()).ok_or_else(shape)?;
        Ok(Anchor { pid, start: map.get("start").and_then(Value::as_u64).ok_or_else(shape)? })
    }

    /// The anchor as the body a person signs holds it.
    pub fn body(&self) -> Value {
        serde_json::json!({"pid": self.pid, "start": self.start})
    }
}

/// Who is asking, as the grant checks of the request being answered need it: the token it presented, as the node keeps it,
/// and the process that sent it. Reset after every request.
#[derive(Default)]
pub struct Asking {
    pub token: String,
    pub peer: Peer,
    /// Set when a grant would have let the request through but the token was registered from another lineage.
    pub lineage_refused: bool,
}

impl Asking {
    pub fn of(token: &str, peer: &Peer) -> Asking {
        Asking { token: crate::identity::Tokens::key(token), peer: *peer, lineage_refused: false }
    }
}

/// The process on the other end of a request, as far as the system says.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Peer {
    pub pid: Option<u32>,
}

impl Peer {
    /// A request that came over no socket (a test, the node itself): its lineage is unknown.
    pub fn none() -> Peer {
        Peer { pid: None }
    }

    /// The process behind ``stream``.
    pub fn of(stream: &Stream) -> Peer {
        Peer { pid: crate::sys::peer_pid(stream) }
    }

    /// The lineage of this process; empty when it is unknown or the process is gone.
    pub fn lineage(&self) -> Vec<Anchor> {
        self.pid.map(of).unwrap_or_default()
    }
}

/// ``pid`` and its parents up to and including the leader of its terminal session, or up to the top without one.
pub fn of(pid: u32) -> Vec<Anchor> {
    let mut out = Vec::new();
    let mut at = pid;
    while at > 1 && out.len() < MOST_ANCHORS {
        let Some(p) = inspect(at).filter(|p| !p.zombie && p.start > 0) else { break };
        out.push(Anchor { pid: at, start: p.start });
        if (p.session == at && p.tty) || p.parent == at {
            break;
        }
        at = p.parent;
    }
    out
}

/// The process that stands for the whole process tree of ``lineage``: the top of it, which every lineage that shares a process with it
/// has too; None when the lineage is not known.
pub fn tree_of(lineage: &[Anchor]) -> Option<Anchor> {
    lineage.last().copied()
}

/// Whether the two lineages have a process in common.
pub fn shares(a: &[Anchor], b: &[Anchor]) -> bool {
    a.iter().any(|x| b.contains(x))
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::os::unix::process::CommandExt;
    use std::process::{Child, Command, Stdio};

    #[derive(Clone, Copy, PartialEq, Eq)]
    enum Where {
        /// In this test's own session.
        Here,
        /// In a session of its own with no terminal, as a harness starts a command.
        Detached,
        /// Leading a session of its own with a controlling terminal: another terminal.
        Terminal,
    }

    fn sleeper(at: Where) -> Child {
        let mut slave = 0;
        if at == Where::Terminal {
            let mut master = 0;
            // SAFETY: openpty fills two descriptors; the master stays open for the life of the test process so the terminal does not hang up.
            assert_eq!(unsafe { libc::openpty(&mut master, &mut slave, std::ptr::null_mut(), std::ptr::null_mut(), std::ptr::null_mut()) }, 0);
        }
        let mut command = Command::new("sleep");
        command.arg("30").stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
        if at != Where::Here {
            // SAFETY: setsid and ioctl are async-signal-safe and run in the child before exec.
            unsafe {
                command.pre_exec(move || {
                    libc::setsid();
                    if slave != 0 {
                        libc::ioctl(slave, libc::TIOCSCTTY as _, 0);
                    }
                    Ok(())
                });
            }
        }
        let child = command.spawn().unwrap();
        if slave != 0 {
            // SAFETY: the descriptor came from openpty above and is closed once.
            unsafe { libc::close(slave) };
        }
        child
    }

    fn end(mut child: Child) {
        child.kill().unwrap();
        child.wait().unwrap();
    }

    #[test]
    fn a_process_and_what_started_it_share_a_lineage_and_another_terminal_does_not() {
        let me = of(std::process::id());
        assert!(!me.is_empty() && me[0].pid == std::process::id());
        let (here, detached, apart) = (sleeper(Where::Here), sleeper(Where::Detached), sleeper(Where::Terminal));
        assert!(shares(&of(here.id()), &me), "a child in the same session shares the process that started it");
        assert!(shares(&of(detached.id()), &me), "a command a harness starts in a session of its own, with no terminal, is still its terminal's");
        let far = of(apart.id());
        assert!(!shares(&far, &me), "a terminal of its own is its own lineage");
        assert_eq!(far.len(), 1, "the shell of a terminal ends the walk");
        for child in [here, detached, apart] {
            end(child);
        }
    }

    #[test]
    fn a_process_that_is_gone_has_no_lineage_and_an_empty_one_shares_nothing() {
        let child = sleeper(Where::Here);
        let pid = child.id();
        end(child);
        assert!(of(pid).is_empty());
        assert!(of(0).is_empty() && of(1).is_empty());
        assert!(!shares(&[], &of(std::process::id())));
    }
}
