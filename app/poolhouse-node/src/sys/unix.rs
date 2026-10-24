//! The Unix side: modes, `flock`, `SO_PEERCRED` and the socket.

use std::fs::{self, File, OpenOptions};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt};
use std::os::unix::net::{UnixListener, UnixStream};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;

use super::Principal;
use crate::error::Result;

pub type Stream = UnixStream;

#[cfg(target_os = "linux")]
type PeerCred = libc::ucred;

#[cfg(target_os = "android")]
#[repr(C)]
struct PeerCred {
    pid: libc::pid_t,
    uid: libc::uid_t,
    gid: libc::gid_t,
}

#[cfg(target_os = "android")]
const _: () = assert!(std::mem::size_of::<PeerCred>() == 12 && std::mem::align_of::<PeerCred>() == 4);

const SOCKET: &str = "node.sock";

pub(super) fn endpoint_of(state: &Path) -> PathBuf {
    state.join(SOCKET)
}

/// Create ``path`` and its parents as directories only the owner can enter.
pub fn private_dir(path: &Path) -> Result<()> {
    fs::DirBuilder::new().recursive(true).mode(0o700).create(path)?;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    Ok(())
}

/// Whether only the owner can reach ``path``.
pub fn owner_only(path: &Path) -> bool {
    fs::metadata(path).is_ok_and(|m| m.permissions().mode() & 0o077 == 0)
}

/// Flush a directory so a new or renamed entry survives a crash.
pub fn sync_dir(path: &Path) -> Result<()> {
    File::open(path)?.sync_all()?;
    Ok(())
}

/// Move ``from`` over ``to`` in one step.
pub fn replace_file(from: &Path, to: &Path) -> Result<()> {
    fs::rename(from, to)?;
    Ok(())
}

/// Options for a file only its owner can read.
pub fn private_file(options: &mut OpenOptions) -> &mut OpenOptions {
    options.mode(0o600)
}

/// Open the append-only log at ``path``, readable and writable by the owner alone.
pub fn open_log(path: &Path) -> Result<File> {
    Ok(private_file(OpenOptions::new().read(true).append(true).create(true)).open(path)?)
}

/// This machine's name, or empty.
pub fn hostname() -> String {
    let mut buf = [0u8; 256];
    // SAFETY: the buffer is valid for its length for the call.
    let rc = unsafe { libc::gethostname(buf.as_mut_ptr().cast(), buf.len() - 1) };
    if rc == 0 { String::from_utf8_lossy(&buf).trim_end_matches('\0').to_string() } else { String::new() }
}

/// The directory a user's own state lives under.
pub fn home_dir() -> PathBuf {
    PathBuf::from(std::env::var_os("HOME").unwrap_or_default())
}

pub(super) fn current_principal() -> Result<Principal> {
    // SAFETY: getuid has no preconditions.
    Ok(Principal::new(unsafe { libc::getuid() }.to_string()))
}

/// The user of the process on the other end of ``stream``.
pub fn peer(stream: &Stream) -> Result<Principal> {
    #[cfg(any(target_os = "linux", target_os = "android"))]
    {
        let mut cred = PeerCred { pid: 0, uid: 0, gid: 0 };
        let mut len = std::mem::size_of::<PeerCred>() as libc::socklen_t;
        // SAFETY: `cred` and `len` are valid for the call and sized as getsockopt expects.
        let rc = unsafe { libc::getsockopt(stream.as_raw_fd(), libc::SOL_SOCKET, libc::SO_PEERCRED, (&mut cred as *mut PeerCred).cast(), &mut len) };
        if rc != 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        if len as usize != std::mem::size_of::<PeerCred>() {
            return Err(std::io::Error::new(std::io::ErrorKind::InvalidData, "the socket returned incomplete peer credentials").into());
        }
        Ok(Principal::new(cred.uid.to_string()))
    }
    #[cfg(not(any(target_os = "linux", target_os = "android")))]
    {
        let (mut uid, mut gid) = (0, 0);
        // SAFETY: the out-pointers are valid for the call.
        if unsafe { libc::getpeereid(stream.as_raw_fd(), &mut uid, &mut gid) } != 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        Ok(Principal::new(uid.to_string()))
    }
}

/// The id of the process on the other end of ``stream``, when the system says (`SO_PEERCRED`, `LOCAL_PEERPID`).
pub fn peer_pid(stream: &Stream) -> Option<u32> {
    #[cfg(any(target_os = "linux", target_os = "android"))]
    {
        let mut cred = PeerCred { pid: 0, uid: 0, gid: 0 };
        let mut len = std::mem::size_of::<PeerCred>() as libc::socklen_t;
        // SAFETY: `cred` and `len` are valid for the call and sized as getsockopt expects.
        let rc = unsafe { libc::getsockopt(stream.as_raw_fd(), libc::SOL_SOCKET, libc::SO_PEERCRED, (&mut cred as *mut PeerCred).cast(), &mut len) };
        (rc == 0 && len as usize == std::mem::size_of::<PeerCred>() && cred.pid > 0).then_some(cred.pid as u32)
    }
    #[cfg(target_os = "macos")]
    {
        let mut pid: libc::pid_t = 0;
        let mut len = std::mem::size_of::<libc::pid_t>() as libc::socklen_t;
        // SAFETY: `pid` and `len` are valid for the call and sized as getsockopt expects.
        let rc = unsafe { libc::getsockopt(stream.as_raw_fd(), libc::SOL_LOCAL, libc::LOCAL_PEERPID, (&mut pid as *mut libc::pid_t).cast(), &mut len) };
        (rc == 0 && pid > 0).then_some(pid as u32)
    }
    #[cfg(not(any(target_os = "linux", target_os = "android", target_os = "macos")))]
    {
        let _ = stream;
        None
    }
}

/// A held advisory lock on a file; released when dropped.
pub struct Lock {
    _file: File,
}

impl Lock {
    /// Current private parent containing this exact held lock. This does not prove
    /// original Node-root provenance if its owner moved the held lock file.
    pub(crate) fn checked_parent(&self, parent: &Path, leaf: &str) -> std::io::Result<File> {
        use std::ffi::CString;
        use std::os::fd::FromRawFd;
        use std::os::unix::fs::MetadataExt;
        let invalid=||std::io::Error::other("pending storage does not match the held lock");
        if leaf.is_empty() || leaf.contains('/') || leaf=="." || leaf==".." { return Err(invalid()); }
        let dir=OpenOptions::new().read(true).custom_flags(libc::O_DIRECTORY|libc::O_NOFOLLOW|libc::O_CLOEXEC).open(parent)?;
        let dm=dir.metadata()?;
        // SAFETY: geteuid has no preconditions.
        let uid=unsafe {libc::geteuid()};
        if !dm.is_dir() || dm.uid()!=uid || dm.mode()&0o077!=0 { return Err(invalid()); }
        let name=CString::new(leaf).map_err(|_|invalid())?;
        // SAFETY: descriptor is live and name is a single NUL-terminated leaf.
        let fd=unsafe{libc::openat(dir.as_raw_fd(),name.as_ptr(),libc::O_RDONLY|libc::O_NOFOLLOW|libc::O_CLOEXEC|libc::O_NONBLOCK)};
        if fd<0 { return Err(std::io::Error::last_os_error()); }
        // SAFETY: successful openat returned one new descriptor, owned exactly once.
        let observed=unsafe{File::from_raw_fd(fd)};
        let matches=|a:&std::fs::Metadata,b:&std::fs::Metadata| a.is_file() && b.is_file() &&
            a.uid()==uid && b.uid()==uid && a.mode()&0o077==0 && b.mode()&0o077==0 &&
            a.nlink()==1 && b.nlink()==1 && a.dev()==b.dev() && a.ino()==b.ino();
        if !matches(&self._file.metadata()?,&observed.metadata()?) {return Err(invalid());}
        // Reopen the directory entry after inspection to reject replacement during the check.
        // SAFETY: same retained descriptor and validated leaf as above.
        let fd=unsafe{libc::openat(dir.as_raw_fd(),name.as_ptr(),libc::O_RDONLY|libc::O_NOFOLLOW|libc::O_CLOEXEC|libc::O_NONBLOCK)};
        if fd<0 {return Err(std::io::Error::last_os_error());}
        // SAFETY: this second successful open transfers its descriptor exactly once.
        let again=unsafe{File::from_raw_fd(fd)};
        if !matches(&self._file.metadata()?,&again.metadata()?) {return Err(invalid());}
        Ok(dir)
    }
}

fn flock(path: &Path, blocking: bool) -> Result<Option<Lock>> {
    let file = private_file(OpenOptions::new().read(true).write(true).create(true).truncate(false)).open(path)?;
    let op = if blocking { libc::LOCK_EX } else { libc::LOCK_EX | libc::LOCK_NB };
    // SAFETY: the descriptor is open for the whole call.
    if unsafe { libc::flock(file.as_raw_fd(), op) } == 0 {
        return Ok(Some(Lock { _file: file }));
    }
    let err = std::io::Error::last_os_error();
    if err.kind() == std::io::ErrorKind::WouldBlock { Ok(None) } else { Err(err.into()) }
}

/// Take the lock at ``path`` without waiting; None when someone holds it.
pub fn try_lock(path: &Path) -> Result<Option<Lock>> {
    flock(path, false)
}

/// Take the lock at ``path``, waiting for its holder.
pub fn lock(path: &Path) -> Result<Lock> {
    flock(path, true).map(|l| l.expect("a blocking lock is held when it returns"))
}

/// The socket the node of a state directory listens on.
pub struct Listener {
    inner: UnixListener,
}

impl Listener {
    /// Bind the socket of ``state``, replacing a dead node's file; owner-only.
    pub fn bind(state: &Path) -> Result<Listener> {
        let path = endpoint_of(state);
        let _ = fs::remove_file(&path);
        let inner = UnixListener::bind(&path)?;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600))?;
        inner.set_nonblocking(true)?;
        Ok(Listener { inner })
    }

    /// The next client; `WouldBlock` when none is waiting.
    pub fn accept(&self) -> std::io::Result<Stream> {
        let (stream, _) = self.inner.accept()?;
        stream.set_nonblocking(false)?;
        Ok(stream)
    }
}

/// Connect to the node of ``state``.
pub fn connect(state: &Path) -> std::io::Result<Stream> {
    UnixStream::connect(endpoint_of(state))
}

/// Start ``command`` so that it outlives this process and its terminal.
pub fn spawn_detached(command: &mut Command) -> std::io::Result<std::process::Child> {
    command.process_group(0).spawn()
}

/// Nothing on Unix: a stop is the `shutdown` request or a signal.
pub fn watch_stop(_state: &Path, _stop: Arc<AtomicBool>) {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn connected_socket_reports_the_kernel_owner() {
        let directory = tempfile::tempdir().unwrap();
        let socket = directory.path().join("node.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let one = UnixStream::connect(&socket).unwrap();
        let (_two, _) = listener.accept().unwrap();
        assert_eq!(peer(&one).unwrap(), Principal::me().unwrap());
        #[cfg(any(target_os = "linux", target_os = "android"))]
        assert_eq!(peer_pid(&one), Some(std::process::id()));
    }
}
