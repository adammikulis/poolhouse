use std::ffi::CString;
use std::fs::File;
use std::io;
use std::os::fd::{AsRawFd, FromRawFd};

fn name(value: &str) -> io::Result<CString> {
    if value.is_empty() || value.contains('/') || value == "." || value == ".." {
        return Err(io::Error::other("Invalid authority storage name."));
    }
    CString::new(value).map_err(io::Error::other)
}

fn file(result: libc::c_int) -> io::Result<File> {
    if result < 0 {
        return Err(io::Error::last_os_error());
    }
    // open/openat return a new descriptor owned by this File.
    Ok(unsafe { File::from_raw_fd(result) })
}

pub(super) fn effective_uid() -> u32 {
    unsafe { libc::geteuid() }
}

pub(super) fn root() -> io::Result<File> {
    file(unsafe {
        libc::open(
            c"/".as_ptr(),
            libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
        )
    })
}

pub(super) fn open_directory(parent: &File, component: &str) -> io::Result<File> {
    let component = name(component)?;
    file(unsafe {
        libc::openat(
            parent.as_raw_fd(),
            component.as_ptr(),
            libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
        )
    })
}

pub(super) fn open_file(parent: &File, component: &str) -> io::Result<File> {
    let component = name(component)?;
    file(unsafe {
        libc::openat(
            parent.as_raw_fd(),
            component.as_ptr(),
            libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK,
        )
    })
}

pub(super) fn create_file(parent: &File, component: &str) -> io::Result<File> {
    let component = name(component)?;
    file(unsafe {
        libc::openat(
            parent.as_raw_fd(),
            component.as_ptr(),
            libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL | libc::O_NOFOLLOW | libc::O_CLOEXEC,
            0o600,
        )
    })
}

pub(super) fn rename(parent: &File, old: &str, new: &str) -> io::Result<()> {
    let old = name(old)?;
    let new = name(new)?;
    let result = unsafe {
        libc::renameat(
            parent.as_raw_fd(),
            old.as_ptr(),
            parent.as_raw_fd(),
            new.as_ptr(),
        )
    };
    if result != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

pub(super) fn lock(file: &File) -> io::Result<()> {
    flock(file, libc::LOCK_EX)
}
pub(super) fn unlock(file: &File) -> io::Result<()> {
    flock(file, libc::LOCK_UN)
}
fn flock(file: &File, operation: libc::c_int) -> io::Result<()> {
    loop {
        if unsafe { libc::flock(file.as_raw_fd(), operation) } == 0 {
            return Ok(());
        }
        let error = io::Error::last_os_error();
        if error.kind() != io::ErrorKind::Interrupted {
            return Err(error);
        }
    }
}

pub(super) fn unlink_owned(parent: &File, component: &str, owned: &File) -> io::Result<()> {
    use std::os::unix::fs::MetadataExt;
    let component = name(component)?;
    let expected = owned.metadata()?;
    let mut info = std::mem::MaybeUninit::<libc::stat>::uninit();
    if unsafe {
        libc::fstatat(
            parent.as_raw_fd(),
            component.as_ptr(),
            info.as_mut_ptr(),
            libc::AT_SYMLINK_NOFOLLOW,
        )
    } != 0
    {
        return Err(io::Error::last_os_error());
    }
    let info = unsafe { info.assume_init() };
    if info.st_dev as u64 != expected.dev()
        || info.st_ino != expected.ino()
        || info.st_nlink != 1
        || expected.nlink() != 1
        || !expected.is_file()
    {
        return Err(io::Error::other(
            "Authority temporary file identity changed.",
        ));
    }
    if unsafe { libc::unlinkat(parent.as_raw_fd(), component.as_ptr(), 0) } != 0 {
        return Err(io::Error::last_os_error());
    }
    parent.sync_all()
}
