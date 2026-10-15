use crate::Error;
use serde_json::{Map, Number, Value};
use std::ffi::{c_char, c_void};

#[link(name = "CoreFoundation", kind = "framework")]
extern "C" {
    fn CFRelease(value: *const c_void);
    fn CFGetTypeID(value: *const c_void) -> usize;
    fn CFBooleanGetTypeID() -> usize;
    fn CFBooleanGetValue(value: *const c_void) -> u8;
    fn CFStringGetTypeID() -> usize;
    fn CFStringGetLength(value: *const c_void) -> isize;
    fn CFStringGetCString(
        value: *const c_void,
        buffer: *mut c_char,
        size: isize,
        encoding: u32,
    ) -> u8;
    fn CFNumberGetTypeID() -> usize;
    fn CFNumberIsFloatType(value: *const c_void) -> u8;
    fn CFNumberGetValue(value: *const c_void, kind: isize, output: *mut c_void) -> u8;
    fn CFArrayGetTypeID() -> usize;
    fn CFArrayGetCount(value: *const c_void) -> isize;
    fn CFArrayGetValueAtIndex(value: *const c_void, index: isize) -> *const c_void;
    fn CFDictionaryGetTypeID() -> usize;
    fn CFDictionaryGetCount(value: *const c_void) -> isize;
    fn CFDictionaryGetKeysAndValues(
        value: *const c_void,
        keys: *mut *const c_void,
        values: *mut *const c_void,
    );
}

pub(super) struct Owned(pub(super) *const c_void);
impl Drop for Owned {
    fn drop(&mut self) {
        unsafe {
            CFRelease(self.0);
        }
    }
}

fn string(value: *const c_void) -> Result<String, Error> {
    let length = unsafe { CFStringGetLength(value) };
    if !(0..=4096).contains(&length) {
        return Err(Error::Unavailable);
    }
    let mut bytes = [0u8; 4097];
    if unsafe {
        CFStringGetCString(
            value,
            bytes.as_mut_ptr().cast(),
            bytes.len() as isize,
            0x08000100,
        )
    } == 0
    {
        return Err(Error::Unavailable);
    }
    let size = bytes
        .iter()
        .position(|b| *b == 0)
        .ok_or(Error::Unavailable)?;
    let text = String::from_utf8(bytes[..size].to_vec()).map_err(|_| Error::Unavailable)?;
    if text.encode_utf16().count() != length as usize {
        return Err(Error::Unavailable);
    }
    Ok(text)
}

pub(super) fn decode(value: *const c_void, depth: u8) -> Result<Value, Error> {
    if value.is_null() || depth > 2 {
        return Err(Error::Unavailable);
    }
    let kind = unsafe { CFGetTypeID(value) };
    if kind == unsafe { CFBooleanGetTypeID() } {
        return Ok(Value::Bool(unsafe { CFBooleanGetValue(value) } != 0));
    }
    if kind == unsafe { CFStringGetTypeID() } {
        return Ok(Value::String(string(value)?));
    }
    if kind == unsafe { CFNumberGetTypeID() } {
        if unsafe { CFNumberIsFloatType(value) } != 0 {
            let mut number = 0f64;
            if unsafe { CFNumberGetValue(value, 6, (&mut number as *mut f64).cast()) } == 0 {
                return Err(Error::Unavailable);
            }
            return Number::from_f64(number)
                .map(Value::Number)
                .ok_or(Error::Unavailable);
        }
        let mut number = 0i64;
        if unsafe { CFNumberGetValue(value, 4, (&mut number as *mut i64).cast()) } == 0 {
            return Err(Error::Unavailable);
        }
        return Ok(Value::Number(Number::from(number)));
    }
    if kind == unsafe { CFArrayGetTypeID() } {
        let length = unsafe { CFArrayGetCount(value) };
        if !(0..=16).contains(&length) {
            return Err(Error::Unavailable);
        }
        let mut array = Vec::new();
        for index in 0..length {
            array.push(decode(
                unsafe { CFArrayGetValueAtIndex(value, index) },
                depth + 1,
            )?);
        }
        return Ok(Value::Array(array));
    }
    if kind == unsafe { CFDictionaryGetTypeID() } {
        let length = unsafe { CFDictionaryGetCount(value) };
        if !(0..=32).contains(&length) {
            return Err(Error::Unavailable);
        }
        let mut keys = vec![std::ptr::null(); length as usize];
        let mut values = vec![std::ptr::null(); length as usize];
        unsafe {
            CFDictionaryGetKeysAndValues(value, keys.as_mut_ptr(), values.as_mut_ptr());
        }
        let mut fields = Map::new();
        for (key, value) in keys.into_iter().zip(values) {
            if key.is_null() || unsafe { CFGetTypeID(key) } != unsafe { CFStringGetTypeID() } {
                return Err(Error::Unavailable);
            }
            let key = string(key)?;
            if key.len() > 64 || fields.insert(key, decode(value, depth + 1)?).is_some() {
                return Err(Error::Unavailable);
            }
        }
        return Ok(Value::Object(fields));
    }
    Err(Error::Unavailable)
}

#[cfg(test)]
mod tests {
    use super::*;
    extern "C" {
        fn CFStringCreateWithBytes(
            allocator: *const c_void,
            bytes: *const u8,
            length: isize,
            encoding: u32,
            external: u8,
        ) -> *const c_void;
        fn CFNumberCreate(
            allocator: *const c_void,
            kind: isize,
            value: *const c_void,
        ) -> *const c_void;
        static kCFBooleanTrue: *const c_void;
    }
    #[test]
    fn native_strings_preserve_unicode_and_reject_embedded_nul() {
        for text in ["user", "Approval ✦"] {
            let value = Owned(unsafe {
                CFStringCreateWithBytes(
                    std::ptr::null(),
                    text.as_ptr(),
                    text.len() as isize,
                    0x08000100,
                    0,
                )
            });
            assert_eq!(decode(value.0, 0).unwrap(), Value::String(text.into()));
        }
        let bytes = b"user\0other";
        let value = Owned(unsafe {
            CFStringCreateWithBytes(
                std::ptr::null(),
                bytes.as_ptr(),
                bytes.len() as isize,
                0x08000100,
                0,
            )
        });
        assert_eq!(decode(value.0, 0), Err(Error::Unavailable));
    }
    #[test]
    fn native_booleans_and_integers_keep_distinct_policy_types() {
        assert_eq!(
            decode(unsafe { kCFBooleanTrue }, 0).unwrap(),
            Value::Bool(true)
        );
        let number = 3i64;
        let value =
            Owned(unsafe { CFNumberCreate(std::ptr::null(), 4, (&number as *const i64).cast()) });
        assert_eq!(decode(value.0, 0).unwrap(), Value::Number(Number::from(3)));
        assert_eq!(decode(value.0, 3), Err(Error::Unavailable));
    }
}
