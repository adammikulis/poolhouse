//! Bounded C bridge for the single manifest parser; values do not confer trust.
use crate::manifest::{CodePin, Manifest};

#[repr(C)]
struct Pin {
    cdhash: [u8; 20], sha256: [u8; 32], publisher_kind: u8,
    publisher_digest: [u8; 32], requirement_digest: [u8; 32], entitlements_digest: [u8; 32],
}
impl From<CodePin> for Pin {
    fn from(value: CodePin) -> Self {
        Self { cdhash: value.cdhash, sha256: value.sha256, publisher_kind: value.publisher_kind,
            publisher_digest: value.publisher_digest, requirement_digest: value.requirement_digest,
            entitlements_digest: value.entitlements_digest }
    }
}

#[repr(C)]
struct Output {
    owner_uid: u32, generation: u64,
    authority_id: [u8; 32], installation_id: [u8; 32], receipt_public_key: [u8; 32],
    policy_digest: [u8; 32], purpose_policy: [u8; 32], rotation_policy: [u8; 32],
    helper: Pin, gui: Pin, node: Pin,
}
impl From<Manifest> for Output {
    fn from(value: Manifest) -> Self {
        Self { owner_uid: value.owner_uid, generation: value.generation, authority_id: value.authority_id,
            installation_id: value.installation_id, receipt_public_key: value.receipt_public_key,
            policy_digest: value.policy_digest, purpose_policy: value.purpose_policy, rotation_policy: value.rotation_policy,
            helper: value.helper.into(), gui: value.gui.into(), node: value.node.into() }
    }
}

/// The caller owns a valid readable buffer and an aligned writable output;
/// arbitrary IPC addresses must never cross this in-process boundary.
#[no_mangle]
unsafe extern "C" fn ph_native_manifest_decode(bytes: *const u8, length: usize, output: *mut Output) -> bool {
    if bytes.is_null() || output.is_null() || length == 0 || length > 16 * 1024
        || (output as usize) % std::mem::align_of::<Output>() != 0 { return false; }
    // Copy before parsing or writing output so mutable caller memory cannot be
    // retained as policy and overlapping input/output does not alias references.
    let owned = unsafe { std::slice::from_raw_parts(bytes, length) }.to_vec();
    let Ok(Some(value)) = std::panic::catch_unwind(|| Manifest::parse(&owned)) else { return false; };
    unsafe { output.write(value.into()); }
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn c_layout_is_exact() {
        assert_eq!(std::mem::size_of::<Pin>(), 149);
        assert_eq!(std::mem::size_of::<Output>(), 656);
        assert_eq!(std::mem::offset_of!(Output, generation), 8);
        assert_eq!(std::mem::offset_of!(Output, helper), 208);
        assert_eq!(std::mem::offset_of!(Output, gui), 357);
        assert_eq!(std::mem::offset_of!(Output, node), 506);
    }

    #[test]
    fn invalid_inputs_leave_output_untouched() {
        let mut storage = std::mem::MaybeUninit::<Output>::uninit();
        let output = storage.as_mut_ptr();
        let size = std::mem::size_of::<Output>();
        unsafe {
            std::ptr::write_bytes(output.cast::<u8>(), 0xa5, size);
            let malformed = b"{}";
            assert!(!ph_native_manifest_decode(std::ptr::null(), 2, output));
            assert!(!ph_native_manifest_decode(malformed.as_ptr(), 0, output));
            assert!(!ph_native_manifest_decode(malformed.as_ptr(), 16 * 1024 + 1, output));
            assert!(!ph_native_manifest_decode(malformed.as_ptr(), malformed.len(), std::ptr::null_mut()));
            assert!(!ph_native_manifest_decode(malformed.as_ptr(), malformed.len(), output.cast::<u8>().add(1).cast()));
            assert!(!ph_native_manifest_decode(malformed.as_ptr(), malformed.len(), output));
            assert!(std::slice::from_raw_parts(output.cast::<u8>(), size).iter().all(|byte| *byte == 0xa5));
        }
    }
}
