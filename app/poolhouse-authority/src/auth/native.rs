use std::ffi::{c_char, c_void};

use super::{Api, ExternalForm};
use crate::Error;

mod value;
const RIGHT: &std::ffi::CStr = c"app.poolhouse.authority.approve";

#[repr(C)]
struct Item {
    name: *const c_char,
    length: usize,
    value: *mut c_void,
    flags: u32,
}
#[repr(C)]
struct Rights {
    count: u32,
    items: *mut Item,
}
#[repr(C)]
struct Form {
    bytes: [u8; 32],
}

#[link(name = "Security", kind = "framework")]
extern "C" {
    fn AuthorizationCreate(
        rights: *const Rights,
        environment: *const Rights,
        flags: u32,
        output: *mut *mut c_void,
    ) -> i32;
    fn AuthorizationMakeExternalForm(reference: *mut c_void, form: *mut Form) -> i32;
    fn AuthorizationCreateFromExternalForm(form: *const Form, output: *mut *mut c_void) -> i32;
    fn AuthorizationRightGet(name: *const c_char, output: *mut *const c_void) -> i32;
    fn AuthorizationCopyRights(
        reference: *mut c_void,
        rights: *const Rights,
        environment: *const Rights,
        flags: u32,
        output: *mut *mut Rights,
    ) -> i32;
    fn AuthorizationFree(reference: *mut c_void, flags: u32) -> i32;
}

pub(super) fn wipe(bytes: &mut [u8]) {
    for byte in bytes {
        unsafe {
            std::ptr::write_volatile(byte, 0);
        }
    }
}
fn success(status: i32) -> Result<(), Error> {
    if status != 0 {
        return Err(Error::Unauthorized);
    }
    Ok(())
}

pub(super) struct Native {
    identity: super::policy::RuleIdentity,
}
pub(super) struct Reference(*mut c_void);

impl Api for Native {
    type Reference = Reference;
    fn create_fresh(&self) -> Result<Reference, Error> {
        let mut pointer = std::ptr::null_mut();
        success(unsafe {
            AuthorizationCreate(std::ptr::null(), std::ptr::null(), 0, &mut pointer)
        })?;
        if pointer.is_null() {
            return Err(Error::Unavailable);
        }
        Ok(Reference(pointer))
    }
    fn externalize(&self, reference: &Reference) -> Result<ExternalForm, Error> {
        let mut form = Form { bytes: [0; 32] };
        let result = success(unsafe { AuthorizationMakeExternalForm(reference.0, &mut form) });
        if let Err(error) = result {
            wipe(&mut form.bytes);
            return Err(error);
        }
        let output = ExternalForm(Box::new(form.bytes));
        wipe(&mut form.bytes);
        Ok(output)
    }
    fn import(&self, form: &ExternalForm) -> Result<Reference, Error> {
        let mut pointer = std::ptr::null_mut();
        let form_pointer = form.0.as_ptr().cast::<Form>();
        success(unsafe { AuthorizationCreateFromExternalForm(form_pointer, &mut pointer) })?;
        if pointer.is_null() {
            return Err(Error::Unavailable);
        }
        Ok(Reference(pointer))
    }
    fn identity(&self) -> &super::policy::RuleIdentity {
        &self.identity
    }
    fn policy(&self) -> Result<serde_json::Value, Error> {
        let mut pointer = std::ptr::null();
        let status = unsafe { AuthorizationRightGet(RIGHT.as_ptr(), &mut pointer) };
        if status != 0 || pointer.is_null() {
            return Err(Error::Unavailable);
        }
        let policy = value::Owned(pointer);
        value::decode(policy.0, 0)
    }
    fn request(&self, reference: &Reference) -> Result<(), Error> {
        let mut item = Item {
            name: RIGHT.as_ptr(),
            length: 0,
            value: std::ptr::null_mut(),
            flags: 0,
        };
        let rights = Rights {
            count: 1,
            items: &mut item,
        };
        success(unsafe {
            AuthorizationCopyRights(
                reference.0,
                &rights,
                std::ptr::null(),
                3,
                std::ptr::null_mut(),
            )
        })
    }
    fn destroy(&self, reference: Reference) -> Result<(), Error> {
        success(unsafe { AuthorizationFree(reference.0, 8) })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn authorization_abi_matches_native_sdk() {
        assert_eq!(
            (std::mem::size_of::<Item>(), std::mem::align_of::<Item>()),
            (32, 8)
        );
        assert_eq!(
            (
                std::mem::size_of::<Rights>(),
                std::mem::align_of::<Rights>()
            ),
            (16, 8)
        );
        assert_eq!(
            (std::mem::size_of::<Form>(), std::mem::align_of::<Form>()),
            (32, 1)
        );
    }
}
