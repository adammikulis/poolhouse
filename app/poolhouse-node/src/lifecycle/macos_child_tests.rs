//! Standalone sole-waiter fixture supervisor; never a production issuer.
use super::*;

pub fn run_fixture(path: &std::path::Path) {
    // This standalone executable has one thread and no other wait calls or
    // signal handler. Do not move this assumption into the running node.
    assert_ne!(unsafe { libc::signal(libc::SIGCHLD, libc::SIG_DFL) }, libc::SIG_ERR);
    let mut disposition = unsafe { std::mem::zeroed::<libc::sigaction>() };
    assert_eq!(unsafe { libc::sigaction(libc::SIGCHLD, std::ptr::null(), &mut disposition) }, 0);
    assert_eq!(disposition.sa_sigaction, libc::SIG_DFL);
    assert_eq!(disposition.sa_flags & libc::SA_NOCLDWAIT, 0);
    unknown_ownership_never_signals();
    let child = spawn_fixture(path).expect("spawn harmless suspended fixture");
    assert_eq!(child.fixture_marker().unwrap(), None);
    let pid = child.pid;
    let result = match child.cancel() {
        Ok(result) => result,
        Err(failure) => {
            eprintln!("fixture cleanup unresolved: {}", failure.error);
            drop(failure);
            panic!("fixture cleanup must settle");
        }
    };
    assert!(libc::WIFSIGNALED(result.status));
    assert_eq!(libc::WTERMSIG(result.status), libc::SIGKILL);
    let mut status = 0;
    assert_eq!(unsafe { libc::waitpid(pid, &mut status, libc::WNOHANG) }, -1);
    assert_eq!(std::io::Error::last_os_error().raw_os_error(), Some(libc::ECHILD));
    println!("suspended fixture cancelled and exact child reaped");
}

fn unknown_ownership_never_signals() {
    let mut state = State::Unwaited;
    let mut signals = 0;
    let error = cleanup_attempt(&mut state, 0,
        |_, _| Err(std::io::Error::from_raw_os_error(libc::ECHILD)),
        |_| { signals += 1; Ok(()) }).unwrap_err();
    assert_eq!(error.raw_os_error(), Some(libc::ECHILD));
    assert_eq!(state, State::Unknown);
    assert!(cleanup_attempt(&mut state, 0,
        |_, _| panic!("unknown ownership must not wait again"),
        |_| { signals += 1; Ok(()) }).is_err());
    assert_eq!(signals, 0);
}
