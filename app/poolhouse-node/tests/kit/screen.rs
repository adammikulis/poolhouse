//! The person's screen and keyboard, for the tests that play a person typing `yes` to the node's own question.
#![cfg(unix)]

use std::io::{Read, Write};
use std::os::fd::FromRawFd;
use std::time::{Duration, Instant};

/// A pseudo-terminal the test plays the person at: the name of its device and the side a person's keyboard and screen are.
pub struct Screen {
    pub name: String,
    person: std::fs::File,
    _device: std::fs::File,
}

pub fn screen() -> Screen {
    let (mut master, mut slave) = (0, 0);
    let mut name = [0 as libc::c_char; 128];
    // SAFETY: openpty fills the two descriptors and the name buffer, which is large enough for a device name.
    let made = unsafe { libc::openpty(&mut master, &mut slave, name.as_mut_ptr(), std::ptr::null_mut(), std::ptr::null_mut()) };
    assert_eq!(made, 0, "a pseudo-terminal");
    // SAFETY: both descriptors were just made here and each is owned by one File.
    let (person, device) = unsafe { (std::fs::File::from_raw_fd(master), std::fs::File::from_raw_fd(slave)) };
    // SAFETY: openpty wrote a NUL-terminated name.
    let name = unsafe { std::ffi::CStr::from_ptr(name.as_ptr()) }.to_string_lossy().into_owned();
    // SAFETY: a plain flag change on a descriptor this test owns.
    unsafe { libc::fcntl(master, libc::F_SETFL, libc::O_NONBLOCK) };
    Screen { name, person, _device: device }
}

impl Screen {
    /// Read what the node put on the screen until ``text`` appears.
    pub fn sees(&mut self, text: &str) -> String {
        let (end, mut shown) = (Instant::now() + Duration::from_secs(20), Vec::new());
        let mut reader = &self.person;
        while Instant::now() < end && !String::from_utf8_lossy(&shown).contains(text) {
            let mut buf = [0u8; 512];
            match reader.read(&mut buf) {
                Ok(n) if n > 0 => shown.extend_from_slice(&buf[..n]),
                _ => std::thread::sleep(Duration::from_millis(20)),
            }
        }
        String::from_utf8_lossy(&shown).into_owned()
    }

    pub fn types(&mut self, line: &str) {
        self.person.write_all(line.as_bytes()).unwrap();
    }
}
