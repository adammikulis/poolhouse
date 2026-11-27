//! The account this process runs as, read from the system.

use std::path::PathBuf;

use windows_sys::Win32::Security::TOKEN_QUERY;
use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};
use windows_sys::Win32::UI::Shell::GetUserProfileDirectoryW;

use super::Handle;

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
