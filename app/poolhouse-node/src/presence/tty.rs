//! The terminal of a process, found by the node and never named by the process: where a computer with no prompt of the system's own asks the person, so
//! a program cannot ask on a terminal of its own that it also answers.

use std::path::PathBuf;

/// The terminal of the process ``pid``: on Mac and Linux the device that is its controlling terminal (found among the devices under `/dev`); on Windows
/// the process itself, whose console the node attaches to. None when it has none, is gone or this platform does not say.
pub fn of(pid: u32) -> Option<PathBuf> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::{FileTypeExt, MetadataExt};
        let tdev = crate::lease::live::inspect(pid).filter(|p| !p.zombie)?.tdev;
        if tdev == 0 {
            return None;
        }
        ["/dev", "/dev/pts"].into_iter().filter_map(|dir| std::fs::read_dir(dir).ok()).flatten().flatten().find_map(|entry| {
            let meta = entry.metadata().ok()?;
            (meta.file_type().is_char_device() && meta.rdev() == tdev).then(|| entry.path())
        })
    }
    #[cfg(windows)]
    {
        crate::lease::live::inspect(pid).filter(|p| !p.zombie).map(|_| PathBuf::from(pid.to_string()))
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = pid;
        None
    }
}
