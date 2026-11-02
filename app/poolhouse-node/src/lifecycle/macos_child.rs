//! Suspended own-child mechanics only. No native proof or confinement issuer.
//! Production construction is deliberately unavailable. The fixture harness is a
//! sole waiter; a Rust type cannot exclude another thread's waitpid or SIGCHLD policy.
//! START_SUSPENDED does not prevent another same-user process from sending SIGCONT.
use std::{ffi::CString, io, os::fd::{AsRawFd, FromRawFd, OwnedFd}, ptr};
#[cfg(test)] #[path = "macos_child_tests.rs"] mod macos_child_tests;
#[cfg(test)] pub(super) fn run_fixture(path: &std::path::Path) { macos_child_tests::run_fixture(path); }

struct FixtureImage {
    // A cooperative isolated fixture pathname, NOT an atomically measured vnode.
    executable: CString,
    arguments: Vec<CString>,
    argument_pointers: Vec<*mut libc::c_char>,
    environment_pointers: [*mut libc::c_char; 1],
    marker_write: OwnedFd,
    marker_read: OwnedFd,
}

#[derive(Debug, PartialEq, Eq)]
pub(super) enum State { Unwaited, Unknown, Reaped }

#[must_use = "the sole waiter must explicitly cancel and reap its child"]
pub(super) struct SuspendedChild {
    pid: libc::pid_t,
    state: State,
    cleanup_attempted: bool,
    image: FixtureImage,
}

pub(super) struct CleanupFailure {
    pub(super) child: SuspendedChild,
    pub(super) error: io::Error,
}

pub(super) struct ReapedChild { pub(super) status: libc::c_int }

impl SuspendedChild {
    /// The exclusive parent waiter alone may call this. Unknown ownership never
    /// becomes a fresh PID lookup or another signal attempt.
    fn settle(&mut self) -> io::Result<libc::c_int> {
        cleanup_attempt(&mut self.state, self.pid, |pid, flags| {
            let mut status = 0;
            // SAFETY: status is writable, and pid names the owned direct child.
            let found = unsafe { libc::waitpid(pid, &mut status, flags) };
            if found == pid { Ok(Some(status)) }
            else if found == 0 { Ok(None) }
            else { Err(io::Error::last_os_error()) }
        }, |pid| {
            if unsafe { libc::kill(pid, libc::SIGKILL) } == 0 { Ok(()) }
            else { Err(io::Error::last_os_error()) }
        })
    }

    pub(super) fn cancel(mut self) -> Result<ReapedChild, CleanupFailure> {
        self.cleanup_attempted = true;
        match self.settle() {
            Ok(status) => Ok(ReapedChild { status }),
            Err(error) => Err(CleanupFailure { child: self, error }),
        }
    }

    #[cfg(test)]
    pub(super) fn fixture_marker(&self) -> io::Result<Option<u8>> {
        let mut byte = 0;
        let count = unsafe { libc::read(self.image.marker_read.as_raw_fd(), (&mut byte as *mut u8).cast(), 1) };
        if count == 1 { return Ok(Some(byte)); }
        if count == 0 { return Ok(None); }
        let error = io::Error::last_os_error();
        if error.kind() == io::ErrorKind::WouldBlock { Ok(None) } else { Err(error) }
    }
}

// Pure syscall decision seam, not a process/provenance constructor.
fn cleanup_attempt(
    state: &mut State, pid: libc::pid_t,
    mut wait: impl FnMut(libc::pid_t, libc::c_int) -> io::Result<Option<libc::c_int>>,
    mut kill: impl FnMut(libc::pid_t) -> io::Result<()>,
) -> io::Result<libc::c_int> {
    if *state != State::Unwaited {
        return Err(io::Error::other("child wait ownership unavailable"));
    }
    loop {
        match wait(pid, libc::WNOHANG) {
            Ok(Some(status)) => { *state = State::Reaped; return Ok(status); }
            Ok(None) => break,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => {
                if error.raw_os_error() == Some(libc::ECHILD) { *state = State::Unknown; }
                return Err(error);
            }
        }
    }
    // Exclusive parent wait ownership reserves the PID even after child exit.
    if let Err(error) = kill(pid) {
        if error.raw_os_error() != Some(libc::ESRCH) { return Err(error); }
    }
    loop {
        match wait(pid, 0) {
            Ok(Some(status)) => { *state = State::Reaped; return Ok(status); }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => {
                if error.raw_os_error() == Some(libc::ECHILD) { *state = State::Unknown; }
                return Err(error);
            }
            Ok(None) => return Err(io::Error::other("blocking child wait returned no status")),
        }
    }
}

impl Drop for SuspendedChild {
    fn drop(&mut self) {
        if !self.cleanup_attempted && self.state == State::Unwaited && self.settle().is_err() {
            // Diagnostic only: Drop cannot manufacture successful cleanup. The
            // explicit failure object is the supported recovery owner.
            eprintln!("own-child cleanup remains unresolved");
        }
    }
}

/// Harness-only constructor. No production caller can select an executable.
/// The isolated single-thread supervisor first establishes default SIGCHLD,
/// has no other waiter and retains its fixture directory until exact cleanup.
#[cfg(test)]
pub(super) fn spawn_fixture(path: &std::path::Path) -> io::Result<SuspendedChild> {
    use std::os::unix::ffi::OsStrExt;
    let executable = CString::new(path.as_os_str().as_bytes())?;
    let arguments = vec![CString::new("poolhouse-inert-fixture")?];
    let argument_pointers = arguments.iter().map(|v| v.as_ptr().cast_mut()).chain(std::iter::once(ptr::null_mut())).collect();
    let mut pipe = [-1; 2];
    if unsafe { libc::pipe(pipe.as_mut_ptr()) } != 0 { return Err(io::Error::last_os_error()); }
    let marker_read = unsafe { OwnedFd::from_raw_fd(pipe[0]) };
    let marker_write = unsafe { OwnedFd::from_raw_fd(pipe[1]) };
    if unsafe { libc::fcntl(marker_read.as_raw_fd(), libc::F_SETFL, libc::O_NONBLOCK) } == -1 {
        return Err(io::Error::last_os_error());
    }
    let image = FixtureImage { executable, arguments, argument_pointers,
        environment_pointers: [ptr::null_mut()], marker_write, marker_read };
    let mut attributes = ptr::null_mut();
    let mut actions = ptr::null_mut();
    fn checked(code: libc::c_int) -> io::Result<()> {
        if code == 0 { Ok(()) } else { Err(io::Error::from_raw_os_error(code)) }
    }
    checked(unsafe { libc::posix_spawnattr_init(&mut attributes) })?;
    let result = (|| {
        checked(unsafe { libc::posix_spawn_file_actions_init(&mut actions) })?;
        // Public Darwin SDK flags; not a hostile same-account security hold.
        checked(unsafe { libc::posix_spawnattr_setflags(&mut attributes,
            (libc::POSIX_SPAWN_START_SUSPENDED | libc::POSIX_SPAWN_CLOEXEC_DEFAULT) as i16) })?;
        for fd in 0..3 {
            checked(unsafe { libc::posix_spawn_file_actions_addopen(&mut actions, fd,
                c"/dev/null".as_ptr(), libc::O_RDWR, 0) })?;
        }
        checked(unsafe { libc::posix_spawn_file_actions_adddup2(&mut actions, image.marker_write.as_raw_fd(), 3) })?;
        let mut pid = 0;
        checked(unsafe { libc::posix_spawn(&mut pid, image.executable.as_ptr(), &actions, &attributes,
            image.argument_pointers.as_ptr(), image.environment_pointers.as_ptr()) })?;
        Ok(SuspendedChild { pid, state: State::Unwaited, cleanup_attempted: false, image })
    })();
    if !actions.is_null() { unsafe { libc::posix_spawn_file_actions_destroy(&mut actions); } }
    unsafe { libc::posix_spawnattr_destroy(&mut attributes); }
    result
}
