//! The anchored terminal as the one presence step: the node itself writes the question on the person's terminal device and reads the
//! line typed there, so the answer never passes through the process that asked.
//!
//! A program that runs as the person and asks cannot answer with a line of its own, since the node reads the terminal, not the asker. It
//! still can open a pseudo-terminal of its own and type `yes` into it: this is the custody of the typed `yes` in docs/node.md, "Key custody",
//! and is why the operating system's own prompt (`Os`) is what a node uses wherever the system has one. The terminal is for a node that
//! has no such prompt (a server, a container, the pool check's rig, which plays the person at a pseudo-terminal) and is chosen when the
//! node starts (`--presence terminal`), never by a request. On Windows no lineage is read and no terminal device is opened: nothing here
//! answers there, and the first key or phone is refused.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use super::{plain, Asking, Presence};
use crate::error::Result;
#[cfg(not(unix))]
use crate::error::Error;

/// Ask on the terminal device the request names.
pub struct Terminal;

/// A question that is on a terminal: what became of it, and a flag that ends the wait when the question is dropped.
struct Typed {
    answer: Arc<Mutex<Option<bool>>>,
    stop: Arc<AtomicBool>,
}

impl Asking for Typed {
    fn poll(&mut self) -> Option<bool> {
        *self.answer.lock().ok()?
    }
}

impl Drop for Typed {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
    }
}

/// Whether ``line`` is the one word that allows: the typed `yes`, nothing else (Enter, `y`, anything longer is a refusal).
pub fn is_yes(line: &[u8]) -> bool {
    String::from_utf8_lossy(line).trim().eq_ignore_ascii_case("yes")
}

impl Presence for Terminal {
    fn kind(&self) -> &'static str {
        "terminal"
    }

    fn begin(&mut self, what: &str, terminal: &str) -> Result<Box<dyn Asking>> {
        #[cfg(unix)]
        {
            let fd = unix::open(terminal)?;
            let (answer, stop) = (Arc::new(Mutex::new(None)), Arc::new(AtomicBool::new(false)));
            let (done, quit, text) = (answer.clone(), stop.clone(), format!("\r\n{}\r\nType yes to allow it, anything else to refuse: ", plain(what)));
            std::thread::spawn(move || {
                let said = unix::ask(fd, &text, &quit);
                if let (Some(yes), Ok(mut slot)) = (said, done.lock()) {
                    *slot = Some(yes);
                }
            });
            Ok(Box::new(Typed { answer, stop }))
        }
        #[cfg(not(unix))]
        {
            let _ = (what, terminal);
            Err(Error::Denied("This computer reads no terminal for the node, so the first key or phone cannot be added here yet.".into()))
        }
    }
}

#[cfg(unix)]
mod unix {
    use std::ffi::CString;
    use std::os::unix::fs::{FileTypeExt, MetadataExt};
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::time::{Duration, Instant};

    use crate::error::{Error, Result};

    const WAIT: Duration = Duration::from_secs(120);
    const MOST_LINE: usize = 256;

    /// The terminal device named ``path``, opened to read and write: a terminal under `/dev` that the account of the node owns, never
    /// the node's own controlling terminal.
    pub fn open(path: &str) -> Result<i32> {
        let refused = || Error::Denied("The question needs a terminal of yours to be asked on: name the terminal this command runs in.".into());
        let real = std::fs::canonicalize(path).map_err(|_| refused())?;
        let meta = std::fs::metadata(&real).map_err(|_| refused())?;
        // SAFETY: geteuid takes no arguments and cannot fail.
        if !real.starts_with("/dev") || !meta.file_type().is_char_device() || meta.uid() != unsafe { libc::geteuid() } {
            return Err(refused());
        }
        let name = CString::new(real.to_string_lossy().as_bytes()).map_err(|_| refused())?;
        // SAFETY: name is a valid NUL-terminated path; the descriptor is owned by the thread that asks.
        let fd = unsafe { libc::open(name.as_ptr(), libc::O_RDWR | libc::O_NOCTTY | libc::O_NONBLOCK | libc::O_CLOEXEC) };
        // SAFETY: isatty only reads the descriptor.
        if fd < 0 || unsafe { libc::isatty(fd) } != 1 {
            if fd >= 0 {
                // SAFETY: fd was just opened here.
                unsafe { libc::close(fd) };
            }
            return Err(refused());
        }
        Ok(fd)
    }

    /// Put ``text`` on the terminal and wait for a line typed after it: Some(whether it was `yes`), or None when nobody answered in time
    /// or the node dropped the question. The descriptor is closed.
    pub fn ask(fd: i32, text: &str, quit: &AtomicBool) -> Option<bool> {
        // SAFETY: tcflush and write act on the descriptor this thread owns; text is a valid buffer of its length.
        unsafe {
            libc::tcflush(fd, libc::TCIFLUSH);
            libc::write(fd, text.as_ptr().cast(), text.len());
        }
        let (end, mut line) = (Instant::now() + WAIT, Vec::new());
        let said = loop {
            if quit.load(Ordering::SeqCst) || Instant::now() > end {
                break None;
            }
            let mut watch = libc::pollfd { fd, events: libc::POLLIN, revents: 0 };
            // SAFETY: one valid pollfd and a length of one.
            if unsafe { libc::poll(&mut watch, 1, 200) } <= 0 {
                continue;
            }
            let mut buf = [0u8; 64];
            // SAFETY: buf is a valid buffer of its length.
            let n = unsafe { libc::read(fd, buf.as_mut_ptr().cast(), buf.len()) };
            if n == 0 || (n < 0 && watch.revents & libc::POLLHUP != 0) {
                break Some(false);
            }
            line.extend_from_slice(&buf[..usize::try_from(n).unwrap_or(0)]);
            if line.contains(&b'\n') || line.contains(&b'\r') || line.len() > MOST_LINE {
                break Some(super::is_yes(&line.split(|b| *b == b'\n' || *b == b'\r').next().unwrap_or_default().to_vec()));
            }
        };
        // SAFETY: closing the descriptor this thread opened, once.
        unsafe { libc::close(fd) };
        said
    }
}
