//! The anchored terminal as the one presence step: the node itself writes the question on the person's terminal device and reads the
//! line typed there, so the answer never passes through the process that asked.
//!
//! A program that runs as the person and asks cannot answer with a line of its own, since the node reads the terminal, not the asker. It
//! still can open a pseudo-terminal of its own and type `yes` into it: this is the custody of the typed `yes` in docs/node.md, "Key custody",
//! and is why the operating system's own prompt (`Os`) is what a node uses wherever the system has one. The terminal is for a node that
//! has no such prompt (a server, a container, the pool check's rig, which plays the person at a pseudo-terminal) and is chosen when the
//! node starts (`--presence terminal`), never by a request. On Windows the asker names a console by the id of a process that runs in it and the
//! node attaches to it (`sys::Console`); there too the account's own node directory is refused, and the first key or phone on it is asked for
//! with nothing a program of the person's could type into.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use super::{plain, Asking, Presence};
use crate::error::Result;
#[cfg(not(any(unix, windows)))]
use crate::error::Error;

/// How long the person has to answer before the question is dropped.
const WAIT: std::time::Duration = std::time::Duration::from_secs(120);
/// The most that is read as one line.
const MOST_LINE: usize = 256;

/// Ask on the terminal device the request names.
pub struct Terminal {
    real: bool,
}

impl Terminal {
    /// The terminal presence of the node of ``state``, which is refused outright when that is this account's own node.
    pub fn new(state: &std::path::Path) -> Terminal {
        Terminal { real: super::on_real_home(state) }
    }
}

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
        if self.real {
            return Err(crate::error::Error::Denied("On this computer's own node the first key and phone are asked for with the operating system's prompt only: a terminal can be answered by the program that asks.".into()));
        }
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
        #[cfg(windows)]
        {
            let mut console = crate::sys::Console::attach(terminal.trim().parse().unwrap_or(0))?;
            let (answer, stop) = (Arc::new(Mutex::new(None)), Arc::new(AtomicBool::new(false)));
            let (done, quit, text) = (answer.clone(), stop.clone(), format!("\r\n{}\r\nType yes to allow it, anything else to refuse: ", plain(what)));
            std::thread::spawn(move || {
                let said = ask_line(&mut console, &text, &quit, WAIT);
                // leave the console before the answer is known: whoever closes it after that must not find the node still attached (a console that
                // closes ends the processes attached to it)
                drop(console);
                if let (Some(yes), Ok(mut slot)) = (said, done.lock()) {
                    *slot = Some(yes);
                }
            });
            Ok(Box::new(Typed { answer, stop }))
        }
        #[cfg(not(any(unix, windows)))]
        {
            let _ = (what, terminal);
            Err(Error::Denied("This computer has no terminal the node can ask on.".into()))
        }
    }
}

/// One key from a console, or why there is none yet.
#[cfg(any(windows, test))]
enum Key {
    Char(char),
    Idle,
    Closed,
}

/// A console the node asks on: what is written on it and the keys typed at it.
#[cfg(any(windows, test))]
trait Keys {
    /// Throw away what was typed before the question.
    fn flush_input(&mut self);
    fn write(&mut self, text: &str);
    /// The next key typed, waiting up to ``wait``.
    fn key(&mut self, wait: std::time::Duration) -> Key;
}

#[cfg(windows)]
impl Keys for crate::sys::Console {
    fn flush_input(&mut self) {
        crate::sys::Console::flush_input(self);
    }

    fn write(&mut self, text: &str) {
        crate::sys::Console::write(self, text);
    }

    fn key(&mut self, wait: std::time::Duration) -> Key {
        match self.event(u32::try_from(wait.as_millis()).unwrap_or(u32::MAX)) {
            crate::sys::Event::Unit(unit) => Key::Char(char::from_u32(u32::from(unit)).unwrap_or('?')),
            crate::sys::Event::Idle => Key::Idle,
            crate::sys::Event::Closed => Key::Closed,
        }
    }
}

/// Put ``text`` on the console and wait for a line typed after it: Some(whether it was `yes`), or None when nobody answered in ``wait`` or the node
/// dropped the question. A console that is gone is a refusal. What is typed is shown as it is typed.
#[cfg(any(windows, test))]
fn ask_line(console: &mut dyn Keys, text: &str, quit: &AtomicBool, wait: std::time::Duration) -> Option<bool> {
    console.flush_input();
    console.write(text);
    let (end, mut line) = (std::time::Instant::now() + wait, String::new());
    loop {
        if quit.load(Ordering::SeqCst) || std::time::Instant::now() > end {
            return None;
        }
        match console.key(std::time::Duration::from_millis(200)) {
            Key::Idle => {}
            Key::Closed => return Some(false),
            Key::Char('\r' | '\n') => {
                console.write("\r\n");
                return Some(is_yes(line.as_bytes()));
            }
            Key::Char('\u{8}') => {
                if line.pop().is_some() {
                    console.write("\u{8} \u{8}");
                }
            }
            Key::Char(typed) => {
                if line.len() >= MOST_LINE {
                    return Some(false);
                }
                line.push(typed);
                console.write(&typed.to_string());
            }
        }
    }
}

#[cfg(unix)]
mod unix {
    use std::ffi::CString;
    use std::os::unix::fs::{FileTypeExt, MetadataExt};
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::time::Instant;

    use super::{MOST_LINE, WAIT};
    use crate::error::{Error, Result};

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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_terminal_is_never_the_way_to_ask_on_the_accounts_own_node() {
        let refused = Terminal { real: true }.begin("poolhouse: allow the session demo/a to add your first person key to this computer?", "/dev/tty");
        assert!(refused.err().is_some_and(|e| e.to_string().contains("operating system's prompt only")), "the program that asks could open its own terminal and answer it");
    }

    /// A console that is typed at by a script: what it was told to show, and the keys it gives one by one (then nothing, or it closes).
    struct Fake {
        typed: std::collections::VecDeque<Key>,
        before: Vec<char>,
        shown: String,
        flushed: bool,
    }

    impl Fake {
        fn typing(text: &str) -> Fake {
            Fake { typed: text.chars().map(Key::Char).collect(), before: Vec::new(), shown: String::new(), flushed: false }
        }
    }

    impl Keys for Fake {
        fn flush_input(&mut self) {
            self.before.clear();
            self.flushed = true;
        }

        fn write(&mut self, text: &str) {
            self.shown.push_str(text);
        }

        fn key(&mut self, _wait: std::time::Duration) -> Key {
            self.typed.pop_front().unwrap_or(Key::Idle)
        }
    }

    fn asked(console: &mut Fake) -> Option<bool> {
        ask_line(console, "Type yes: ", &AtomicBool::new(false), std::time::Duration::from_millis(50))
    }

    #[test]
    fn only_a_typed_yes_and_enter_is_a_yes_on_a_console() {
        for (typed, said) in [("yes\r", Some(true)), ("YES\n", Some(true)), ("yex\u{8}s\r", Some(true)), ("y\r", Some(false)), ("\r", Some(false)), ("yes please\r", Some(false)), ("no\r", Some(false))] {
            let mut console = Fake::typing(typed);
            assert_eq!(asked(&mut console), said, "{typed:?}");
            assert!(console.flushed && console.shown.starts_with("Type yes: "), "what was typed before the question is thrown away, then the question is written");
        }
    }

    #[test]
    fn a_console_nobody_types_at_or_that_goes_away_makes_no_yes() {
        let mut quiet = Fake::typing("ye");
        assert_eq!(asked(&mut quiet), None, "an unfinished line is no answer once the time is up");
        let mut gone = Fake::typing("");
        gone.typed.push_back(Key::Closed);
        assert_eq!(asked(&mut gone), Some(false), "a console that closes is a refusal");
        let mut dropped = Fake::typing("yes\r");
        let quit = AtomicBool::new(true);
        assert_eq!(ask_line(&mut dropped, "q", &quit, std::time::Duration::from_secs(5)), None, "a question that was dropped reads nothing");
    }

    #[test]
    fn a_line_that_never_ends_is_refused() {
        let mut console = Fake::typing(&"y".repeat(MOST_LINE + 8));
        assert_eq!(asked(&mut console), Some(false));
    }
}
