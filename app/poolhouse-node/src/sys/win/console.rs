//! The console a person types at, for the node's terminal presence (`presence::terminal`): attach the node to the console of a process the asker
//! names, write the question on it and read the keys typed there. A node started by its supervisor is detached, so it has no console of its own to
//! read; the asker names one by the id of a process that runs in it (a command prompt, or the pseudo-console the pool check's rig plays the person
//! at). Only the account's own processes may be named, and one question is on a console at a time.
//!
//! It is the Windows counterpart of opening a terminal device under `/dev` on Unix, and carries the same rule: a program that runs as the person can
//! open a console of its own and type into it, which is why `--presence terminal` is refused on the account's own node directory and the real prompt is
//! the only presence there.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};

use windows_sys::Win32::Foundation::{GENERIC_READ, GENERIC_WRITE, WAIT_OBJECT_0, WAIT_TIMEOUT};
use windows_sys::Win32::Storage::FileSystem::{CreateFileW, FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING};
use windows_sys::Win32::Security::TOKEN_QUERY;
use windows_sys::Win32::System::Console::{
    AttachConsole, FlushConsoleInputBuffer, FreeConsole, GetConsoleMode, GetConsoleProcessList, ReadConsoleInputW, SetConsoleMode, WriteConsoleW, INPUT_RECORD, KEY_EVENT,
};
use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken, WaitForSingleObject};
use windows_sys::Win32::UI::Shell::GetUserProfileDirectoryW;

use super::acl::{current_principal, process_principal};
use super::{wide, Handle};
use crate::error::{Error, Result};

/// `AttachConsole` with this id attaches to the console of the parent process.
const ATTACH_PARENT_PROCESS: u32 = u32::MAX;
/// Whether a console is held by this process now: attaching is per process, so one question at a time.
static IN_USE: AtomicBool = AtomicBool::new(false);

/// What the console gave within the time allowed.
pub enum Event {
    /// A key was pressed: its UTF-16 unit.
    Unit(u16),
    /// Nothing that matters yet (a timeout, or an event that is not a key).
    Idle,
    /// The console went away, or can not be read any more.
    Closed,
}

/// The directory of the account this process runs as, from the system and never from the environment (`USERPROFILE` is the program's to set).
pub fn account_home() -> Option<PathBuf> {
    let mut token = std::ptr::null_mut();
    // SAFETY: the pseudo handle of this process needs no closing; `token` receives a handle we own.
    if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) } == 0 {
        return None;
    }
    let token = Handle(token);
    let mut size = 0u32;
    // SAFETY: the first call only asks how many UTF-16 units the path needs.
    unsafe { GetUserProfileDirectoryW(token.0, std::ptr::null_mut(), &mut size) };
    let mut buf = vec![0u16; usize::try_from(size).ok()?.max(1)];
    // SAFETY: `buf` holds `size` units and the token is open for the call.
    if size == 0 || unsafe { GetUserProfileDirectoryW(token.0, buf.as_mut_ptr(), &mut size) } == 0 {
        return None;
    }
    let len = buf.iter().position(|u| *u == 0).unwrap_or(buf.len());
    Some(PathBuf::from(String::from_utf16_lossy(&buf[..len])))
}

/// A console this process is attached to for one question: the keys typed on it and the words written on it. Dropping it detaches the process
/// again (and attaches it to its parent's console when it had one, the way a node run by hand is left).
pub struct Console {
    input: Handle,
    output: Handle,
    mode: u32,
    had_console: bool,
}

fn open(name: &str) -> std::io::Result<Handle> {
    let name = wide(name);
    // SAFETY: `name` is NUL-ended; the handle is ours.
    Handle::checked(unsafe { CreateFileW(name.as_ptr(), GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, std::ptr::null(), OPEN_EXISTING, 0, std::ptr::null_mut()) })
}

impl Console {
    /// Attach to the console of the process ``pid``, which must run as the same user as this node.
    pub fn attach(pid: u32) -> Result<Console> {
        let refused = || Error::Denied("The question needs a console of yours to be asked on: name the process id of a command window of this account.".into());
        if pid == 0 || pid == std::process::id() || process_principal(pid).ok() != current_principal().ok().map(|me| me.0) {
            return Err(refused());
        }
        if IN_USE.swap(true, Ordering::SeqCst) {
            return Err(crate::limits::floor("The last question is still on its console: try again in a moment."));
        }
        let mut list = [0u32; 1];
        // SAFETY: one valid slot and a length of one; the count of attached processes is returned either way.
        let had_console = unsafe { GetConsoleProcessList(list.as_mut_ptr(), 1) } > 0;
        // SAFETY: leaves the console this process has, if any.
        unsafe { FreeConsole() };
        // SAFETY: attaches this process to the console of ``pid``, which the call checks.
        let got = unsafe { AttachConsole(pid) } != 0;
        let opened = if got { open("CONIN$").and_then(|i| open("CONOUT$").map(|o| (i, o))).ok() } else { None };
        let Some((input, output)) = opened else {
            Console::leave(had_console);
            return Err(refused());
        };
        let mut mode = 0u32;
        // SAFETY: the handles are open; the mode is read and set on the input handle only. No line editing and no echo: the keys come one at a time.
        unsafe {
            GetConsoleMode(input.0, &mut mode);
            SetConsoleMode(input.0, 0);
        }
        Ok(Console { input, output, mode, had_console })
    }

    fn leave(had_console: bool) {
        // SAFETY: detaches this process from the console it holds.
        unsafe { FreeConsole() };
        if had_console {
            // SAFETY: back to the console of the parent, as a node run by hand had it.
            unsafe { AttachConsole(ATTACH_PARENT_PROCESS) };
        }
        IN_USE.store(false, Ordering::SeqCst);
    }

    /// Throw away what was typed on the console before now.
    pub fn flush_input(&mut self) {
        // SAFETY: the handle is open.
        unsafe { FlushConsoleInputBuffer(self.input.0) };
    }

    /// Write ``text`` on the console.
    pub fn write(&mut self, text: &str) {
        let units: Vec<u16> = text.encode_utf16().collect();
        let (mut written, count) = (0u32, u32::try_from(units.len()).unwrap_or(0));
        // SAFETY: `units` holds `count` units and the handle is open.
        unsafe { WriteConsoleW(self.output.0, units.as_ptr(), count, &mut written, std::ptr::null()) };
    }

    /// The next key typed within ``wait_ms``.
    pub fn event(&mut self, wait_ms: u32) -> Event {
        // SAFETY: the handle is open.
        match unsafe { WaitForSingleObject(self.input.0, wait_ms) } {
            WAIT_OBJECT_0 => {}
            WAIT_TIMEOUT => return Event::Idle,
            _ => return Event::Closed,
        }
        // SAFETY: an all-zero record is a valid one, and it is filled by the read.
        let mut record: INPUT_RECORD = unsafe { std::mem::zeroed() };
        let mut read = 0u32;
        // SAFETY: one record and the handle is open; the wait said there is input.
        if unsafe { ReadConsoleInputW(self.input.0, &mut record, 1, &mut read) } == 0 {
            return Event::Closed;
        }
        if read == 1 && u32::from(record.EventType) == KEY_EVENT {
            // SAFETY: the type says the key record is the one that was set.
            let key = unsafe { record.Event.KeyEvent };
            // SAFETY: every character a key gives is read as its UTF-16 unit.
            let unit = unsafe { key.uChar.UnicodeChar };
            if key.bKeyDown != 0 && unit != 0 {
                return Event::Unit(unit);
            }
        }
        Event::Idle
    }
}

impl Drop for Console {
    fn drop(&mut self) {
        // SAFETY: the handle is open until this value is gone.
        unsafe { SetConsoleMode(self.input.0, self.mode) };
        // the handles go before the console does (a null handle is not closed again)
        drop(std::mem::replace(&mut self.input, Handle(std::ptr::null_mut())));
        drop(std::mem::replace(&mut self.output, Handle(std::ptr::null_mut())));
        Console::leave(self.had_console);
    }
}
