//! Processes a test starts to be the other end of a request: in its own terminal, detached as a harness starts a command, or
//! left to the system's first process as a daemon is.

use std::os::unix::process::CommandExt;
use std::process::{Child, Command, Stdio};

use poolhouse_node::lineage::Peer;

/// A process that does nothing for a while: in the session of this test, or (``own_terminal``) leading a session of its own
/// with a controlling terminal, as a shell in another terminal does.
pub fn process(own_terminal: bool) -> Child {
    let mut slave = 0;
    if own_terminal {
        let mut master = 0;
        // SAFETY: openpty fills two descriptors; the master stays open for the life of the test so the terminal does not hang up.
        assert_eq!(unsafe { libc::openpty(&mut master, &mut slave, std::ptr::null_mut(), std::ptr::null_mut(), std::ptr::null_mut()) }, 0);
    }
    let mut command = Command::new("sleep");
    command.arg("60").stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    if own_terminal {
        // SAFETY: setsid and ioctl are async-signal-safe and run in the child before exec.
        unsafe {
            command.pre_exec(move || {
                libc::setsid();
                libc::ioctl(slave, libc::TIOCSCTTY as _, 0);
                Ok(())
            });
        }
    }
    let child = command.spawn().unwrap();
    if own_terminal {
        // SAFETY: the descriptor came from openpty above and is closed once.
        unsafe { libc::close(slave) };
    }
    child
}

/// A process in a session of its own with no terminal, as a harness starts each command it runs.
pub fn detached() -> Child {
    let mut command = Command::new("sleep");
    command.arg("60").stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    // SAFETY: setsid is async-signal-safe and runs in the child before exec.
    unsafe { command.pre_exec(|| { libc::setsid(); Ok(()) }) };
    command.spawn().unwrap()
}

/// A process whose parent has gone, as a daemon's is, and its id.
pub fn orphan() -> u32 {
    let out = Command::new("sh").args(["-c", "sleep 60 >/dev/null 2>&1 & echo $!"]).output().unwrap();
    String::from_utf8_lossy(&out.stdout).trim().parse().unwrap()
}

/// The request peer that is ``child``.
pub fn peer(child: &Child) -> Peer {
    Peer { pid: Some(child.id()) }
}

/// End the processes this test started.
pub fn done(mut children: Vec<Child>) {
    for child in &mut children {
        child.kill().unwrap();
        child.wait().unwrap();
    }
}
