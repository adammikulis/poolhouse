//! One-use, connection-bound proof for registering a main session.

#[cfg(not(unix))]
use std::fs;
#[cfg(unix)]
use std::fs::File;
use std::fs::OpenOptions;
use std::io::{Read, Write};
use std::path::Path;
use std::time::Duration;

use hmac::{Hmac, Mac};
use serde_json::{json, Value};
use sha2::Sha256;

use crate::error::{Error, Result};
use crate::fsutil::{hex, random_hex, unhex};
use crate::node::Node;

pub const ROOT_KEY: &str = "root.key";
pub const REPORTER_KEY: &str = "reporter.key";
pub const CREDENTIALS: &str = "hook-credentials";
const DOMAIN: &str = "poolhouse/root-register/v1\n";
/// One connection's challenge is valid for 30 seconds (session-capability design S1).
pub const CHALLENGE_TTL: Duration = Duration::from_secs(30);

/// Independently generate the node's root and reporter credentials; neither is a person key.
#[cfg(not(unix))]
pub fn ensure(dir: &Path) -> Result<()> {
    let folder = dir.join(CREDENTIALS);
    match fs::symlink_metadata(&folder) {
        Ok(metadata) if metadata.file_type().is_dir() && owned(&metadata) && crate::sys::owner_only(&folder) => {}
        Ok(_) => return Err(Error::Damaged("hook credential directory is not private and owned".into())),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => crate::fsutil::private_dir(&folder)?,
        Err(error) => return Err(error.into()),
    }
    for name in [ROOT_KEY, REPORTER_KEY] {
        let path = folder.join(name);
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        match crate::sys::private_file(&mut options).open(&path) {
            Ok(mut file) => {
                file.write_all(random_hex(32)?.as_bytes())?;
                file.sync_all()?;
                crate::fsutil::sync_dir(&folder)?;
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(error) => return Err(error.into()),
        }
        key(dir, name)?;
    }
    if key(dir, ROOT_KEY)? == key(dir, REPORTER_KEY)? {
        return Err(Error::Damaged("root and reporter credentials must differ".into()));
    }
    Ok(())
}

#[cfg(not(unix))]
fn owned(_: &fs::Metadata) -> bool { true }

#[cfg(not(unix))]
fn key(dir: &Path, name: &str) -> Result<Vec<u8>> {
    let path = dir.join(CREDENTIALS).join(name);
    let metadata = fs::symlink_metadata(&path)?;
    if !metadata.file_type().is_file() || !owned(&metadata) || !crate::sys::owner_only(&path) || metadata.len() != 64 {
        return Err(Error::Damaged(format!("{name} is not a private owned credential")));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.nlink() != 1 { return Err(Error::Damaged(format!("{name} has another hard link"))); }
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    let file = options.open(&path)?;
    let opened = file.metadata()?;
    if !opened.is_file() || !owned(&opened) || opened.len() != 64 {
        return Err(Error::Damaged(format!("{name} changed during open")));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if opened.nlink() != 1 || opened.mode() & 0o077 != 0 {
            return Err(Error::Damaged(format!("{name} is not private and unlinked")));
        }
    }
    let mut text = String::new();
    file.take(65).read_to_string(&mut text)?;
    unhex(text.trim()).filter(|bytes| bytes.len() == 32).ok_or_else(|| Error::Damaged(format!("{name} is not a 32-byte credential")))
}

#[cfg(unix)]
fn folder(dir: &Path, create: bool) -> Result<File> {
    use std::os::fd::{AsRawFd, FromRawFd};
    use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
    let base = OpenOptions::new().read(true).custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW).open(dir.canonicalize()?)?;
    let name = b"hook-credentials\0";
    if create {
        // SAFETY: base and name remain valid for the call; no caller-controlled path is interpolated.
        let result = unsafe { libc::mkdirat(base.as_raw_fd(), name.as_ptr().cast(), 0o700) };
        if result != 0 {
            let error = std::io::Error::last_os_error();
            if error.kind() != std::io::ErrorKind::AlreadyExists { return Err(error.into()); }
        } else { base.sync_all()?; }
    }
    // SAFETY: base and name are valid and openat returns an owned descriptor on success.
    let fd = unsafe { libc::openat(base.as_raw_fd(), name.as_ptr().cast(), libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC) };
    if fd < 0 { return Err(std::io::Error::last_os_error().into()); }
    // SAFETY: fd was returned by openat and is now owned by this File.
    let folder = unsafe { File::from_raw_fd(fd) };
    let meta = folder.metadata()?;
    // SAFETY: geteuid has no preconditions.
    if meta.uid() != unsafe { libc::geteuid() } || meta.mode() & 0o077 != 0 {
        return Err(Error::Damaged("hook credential directory is not private and owned".into()));
    }
    Ok(folder)
}

#[cfg(unix)]
fn file_at(folder: &File, name: &str, create: bool) -> Result<File> {
    use std::os::fd::{AsRawFd, FromRawFd};
    let name = std::ffi::CString::new(name).map_err(|_| Error::Invalid("invalid credential name".into()))?;
    let flags = if create { libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL } else { libc::O_RDONLY | libc::O_NONBLOCK };
    // SAFETY: the folder descriptor and NUL-terminated name remain valid for the call.
    let fd = unsafe { libc::openat(folder.as_raw_fd(), name.as_ptr(), flags | libc::O_NOFOLLOW | libc::O_CLOEXEC, 0o600) };
    if fd < 0 { return Err(std::io::Error::last_os_error().into()); }
    // SAFETY: fd is a fresh descriptor owned by the returned File.
    Ok(unsafe { File::from_raw_fd(fd) })
}

#[cfg(unix)]
fn read_key(folder: &File, name: &str) -> Result<Vec<u8>> {
    use std::os::unix::fs::MetadataExt;
    let file = file_at(folder, name, false)?;
    let meta = file.metadata()?;
    // SAFETY: geteuid has no preconditions.
    if !meta.is_file() || meta.uid() != unsafe { libc::geteuid() } || meta.mode() & 0o077 != 0 || meta.nlink() != 1 || meta.len() != 64 {
        return Err(Error::Damaged(format!("{name} is not a private owned credential")));
    }
    let mut text = String::new();
    file.take(65).read_to_string(&mut text)?;
    unhex(text.trim()).filter(|bytes| bytes.len() == 32).ok_or_else(|| Error::Damaged(format!("{name} is not a 32-byte credential")))
}

#[cfg(unix)]
pub fn ensure(dir: &Path) -> Result<()> {
    use std::os::fd::AsRawFd;
    let folder = folder(dir, true)?;
    for name in [ROOT_KEY, REPORTER_KEY] {
        let pending = std::ffi::CString::new(format!("{name}.pending")).expect("fixed credential name");
        let target = std::ffi::CString::new(name).expect("fixed credential name");
        // SAFETY: folder and the fixed NUL-terminated pending name remain valid for the call.
        let removed = unsafe { libc::unlinkat(folder.as_raw_fd(), pending.as_ptr(), 0) };
        if removed != 0 {
            let error = std::io::Error::last_os_error();
            if error.kind() != std::io::ErrorKind::NotFound { return Err(error.into()); }
        }
        match file_at(&folder, name, false) {
            Ok(_) => {}
            Err(Error::Io(error)) if error.kind() == std::io::ErrorKind::NotFound => {
                let mut file = file_at(&folder, &format!("{name}.pending"), true)?;
                file.write_all(random_hex(32)?.as_bytes())?;
                file.sync_all()?;
                // SAFETY: both names and the directory descriptor are valid; linkat refuses an existing target.
                let linked = unsafe { libc::linkat(folder.as_raw_fd(), pending.as_ptr(), folder.as_raw_fd(), target.as_ptr(), 0) };
                if linked != 0 { return Err(std::io::Error::last_os_error().into()); }
                // SAFETY: the pending name is fixed and remains inside the opened directory.
                if unsafe { libc::unlinkat(folder.as_raw_fd(), pending.as_ptr(), 0) } != 0 {
                    return Err(std::io::Error::last_os_error().into());
                }
                folder.sync_all()?;
            }
            Err(error) => return Err(error),
        }
    }
    if read_key(&folder, ROOT_KEY)? == read_key(&folder, REPORTER_KEY)? {
        return Err(Error::Damaged("root and reporter credentials must differ".into()));
    }
    Ok(())
}

#[cfg(unix)]
fn key(dir: &Path, name: &str) -> Result<Vec<u8>> { read_key(&folder(dir, false)?, name) }

/// The exact request fields a root credential proves, independent of JSON object ordering.
pub fn bytes(nonce: &str, server_pid: u32, request: &Value) -> Vec<u8> {
    fn text(value: &Value) -> &str { value.as_str().unwrap_or("") }
    let p = &request["params"];
    let body = json!([nonce, server_pid, text(&request["board"]), text(&request["token"]), text(&p["model"]), text(&p["harness"]), text(&p["session"])]);
    format!("{DOMAIN}{}", crate::row::canonical(&body)).into_bytes()
}

pub fn sign(dir: &Path, nonce: &str, server_pid: u32, request: &Value) -> Result<String> {
    let mut mac = Hmac::<Sha256>::new_from_slice(&key(dir, ROOT_KEY)?).map_err(|_| Error::Damaged("invalid root credential".into()))?;
    mac.update(&bytes(nonce, server_pid, request));
    Ok(hex(&mac.finalize().into_bytes()))
}

pub fn verifies(dir: &Path, nonce: &str, request: &Value, proof: &str) -> Result<bool> {
    if proof.len() != 64 { return Ok(false); }
    let Some(proof) = unhex(proof) else { return Ok(false) };
    let mut mac = Hmac::<Sha256>::new_from_slice(&key(dir, ROOT_KEY)?).map_err(|_| Error::Damaged("invalid root credential".into()))?;
    mac.update(&bytes(nonce, std::process::id(), request));
    Ok(mac.verify_slice(&proof).is_ok())
}

/// A parent token may register a child; an empty, invalid or same-main-session token still needs root proof.
pub fn needs_proof(node: &Node, request: &Value) -> bool {
    let board = request["board"].as_str().unwrap_or("");
    let token = request["token"].as_str().unwrap_or("");
    let Some(holder) = node.tokens.resolve(token).filter(|holder| holder.board == board) else { return true };
    let harness = request["params"]["harness"].as_str().unwrap_or("");
    let session = request["params"]["session"].as_str().unwrap_or("");
    let Ok(harness) = crate::identity::clean(harness) else { return true };
    let Some(names) = node.boards.get(board).map(|board| &board.names) else { return true };
    if names.find(&harness, session) != Some(holder.name.as_str()) { return false; }
    names.get(&holder.name).is_none_or(|identity| identity.parent.is_empty())
}
