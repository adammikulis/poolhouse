use std::ffi::c_void;
use std::fs::File;
use std::io;
use std::os::fd::AsRawFd;

const ACL_PROPERTY: i32 = 5;
const ALLOW: i32 = 1;
const DENY: i32 = 2;
const READ_ONLY: u64 = (1 << 1) | (1 << 3) | (1 << 7) | (1 << 9) | (1 << 11) | (1 << 20);

extern "C" {
    fn filesec_init() -> *mut c_void;
    fn filesec_free(value: *mut c_void);
    #[cfg_attr(target_arch = "x86_64", link_name = "fstatx_np$INODE64")]
    fn fstatx_np(fd: i32, stat: *mut libc::stat, security: *mut c_void) -> i32;
    fn filesec_query_property(value: *mut c_void, property: i32, present: *mut i32) -> i32;
    fn filesec_get_property(value: *mut c_void, property: i32, output: *mut c_void) -> i32;
    fn acl_free(value: *mut c_void) -> i32;
    fn acl_valid(value: *mut c_void) -> i32;
    fn acl_get_entry(value: *mut c_void, index: i32, entry: *mut *mut c_void) -> i32;
    fn acl_get_tag_type(entry: *mut c_void, tag: *mut i32) -> i32;
    fn acl_get_permset_mask_np(entry: *mut c_void, mask: *mut u64) -> i32;
}
struct Security(*mut c_void);
impl Drop for Security {
    fn drop(&mut self) {
        unsafe { filesec_free(self.0) }
    }
}
struct Acl(*mut c_void);
impl Drop for Acl {
    fn drop(&mut self) {
        unsafe {
            acl_free(self.0);
        }
    }
}
fn success(result: i32) -> io::Result<()> {
    if result != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

pub(super) fn check(file: &File, private: bool) -> io::Result<()> {
    let security = Security(unsafe { filesec_init() });
    if security.0.is_null() {
        return Err(io::Error::last_os_error());
    }
    let mut stat = std::mem::MaybeUninit::<libc::stat>::uninit();
    success(unsafe { fstatx_np(file.as_raw_fd(), stat.as_mut_ptr(), security.0) })?;
    let mut present = 0;
    success(unsafe { filesec_query_property(security.0, ACL_PROPERTY, &mut present) })?;
    if present == 0 {
        return Ok(());
    }
    let mut pointer = std::ptr::null_mut();
    success(unsafe {
        filesec_get_property(
            security.0,
            ACL_PROPERTY,
            (&mut pointer as *mut *mut c_void).cast(),
        )
    })?;
    let acl = Acl(pointer);
    if acl.0.is_null() {
        return Err(super::refusal());
    }
    success(unsafe { acl_valid(acl.0) })?;
    // Darwin returns EINVAL after the final entry of a valid ACL.
    for index in 0..=169 {
        let mut entry = std::ptr::null_mut();
        let result = unsafe { acl_get_entry(acl.0, index, &mut entry) };
        if result != 0 {
            let error = io::Error::last_os_error();
            if error.raw_os_error() == Some(libc::EINVAL) {
                return Ok(());
            }
            return Err(error);
        }
        if index == 169 || entry.is_null() {
            return Err(super::refusal());
        }
        let mut tag = 0;
        let mut mask = 0;
        success(unsafe { acl_get_tag_type(entry, &mut tag) })?;
        success(unsafe { acl_get_permset_mask_np(entry, &mut mask) })?;
        if tag == DENY {
            continue;
        }
        if tag != ALLOW || (private && mask != 0) || mask & !READ_ONLY != 0 {
            return Err(super::refusal());
        }
    }
    Err(super::refusal())
}

#[cfg(test)]
pub(super) fn set_fixture(file: &File, tag: i32, mask: u64) -> io::Result<()> {
    extern "C" {
        fn acl_init(count: i32) -> *mut c_void;
        fn acl_create_entry(acl: *mut *mut c_void, entry: *mut *mut c_void) -> i32;
        fn acl_set_tag_type(entry: *mut c_void, tag: i32) -> i32;
        fn acl_set_qualifier(entry: *mut c_void, qualifier: *const c_void) -> i32;
        fn acl_set_permset_mask_np(entry: *mut c_void, mask: u64) -> i32;
        fn acl_set_fd_np(fd: i32, acl: *mut c_void, kind: i32) -> i32;
    }
    let mut acl = Acl(unsafe { acl_init(1) });
    if acl.0.is_null() {
        return Err(io::Error::last_os_error());
    }
    let mut entry = std::ptr::null_mut();
    success(unsafe { acl_create_entry(&mut acl.0, &mut entry) })?;
    success(unsafe { acl_set_tag_type(entry, tag) })?;
    let invented = [17u8; 16];
    success(unsafe { acl_set_qualifier(entry, invented.as_ptr().cast()) })?;
    success(unsafe { acl_set_permset_mask_np(entry, mask) })?;
    success(unsafe { acl_set_fd_np(file.as_raw_fd(), acl.0, 0x100) })
}
