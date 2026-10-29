//! One bounded manifest parser shared by the native service and approval UI.
#![deny(unsafe_code)]

pub mod manifest;
#[allow(unsafe_code)]
mod manifest_ffi;
