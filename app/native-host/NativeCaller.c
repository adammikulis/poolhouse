#include "NativeCaller.h"
#include "MachCode.h"
#include <bsm/libbsm.h>
#include <string.h>

static bool status_valid(CFTypeRef raw) {
    int64_t value = 0;
    return raw && CFGetTypeID(raw) == CFNumberGetTypeID()
        && CFNumberGetValue((CFNumberRef)raw, kCFNumberSInt64Type, &value)
        && value >= 0 && value <= UINT32_MAX
        && ((uint32_t)value & kSecCodeStatusValid) != 0
        && ((uint32_t)value & kSecCodeStatusDebugged) == 0;
}

static bool dynamic_valid(SecCodeRef code) {
    CFDictionaryRef info = NULL;
    OSStatus status = SecCodeCopySigningInformation(code, kSecCSDynamicInformation, &info);
    if (status != errSecSuccess || !info || CFGetTypeID(info) != CFDictionaryGetTypeID()) {
        if (info) CFRelease(info);
        return false;
    }
    bool okay = status_valid(CFDictionaryGetValue(info, kSecCodeInfoStatus));
    CFRelease(info);
    return okay;
}

static bool identity_matches(SecCodeRef code, const ph_native_identity_observation *expected) {
    ph_native_identity_observation current = {0};
    return dynamic_valid(code) && ph_native_identity_observe(code, &current)
        && (current.signing_flags & kSecCodeSignatureRuntime) != 0
        && current.signing_flags == expected->signing_flags
        && current.entitlements_present == expected->entitlements_present
        && current.publisher_present == expected->publisher_present
        && !memcmp(current.code_directory, expected->code_directory, sizeof(current.code_directory))
        && !memcmp(current.requirement_digest, expected->requirement_digest, sizeof(current.requirement_digest))
        && !memcmp(current.entitlements_digest, expected->entitlements_digest, sizeof(current.entitlements_digest))
        && !memcmp(current.publisher_digest, expected->publisher_digest, sizeof(current.publisher_digest));
}
#ifdef TESTING
bool ph_native_caller_test_status(CFTypeRef value) { return status_valid(value); }
#endif

/* Policy establishment, message lifetime, role/purpose, incarnation and
 * revalidation at effect commit are the protected service's responsibility. */
bool ph_native_caller_matches(const ph_mach_message *message, const ph_native_caller_policy *policy) {
    if (!message || !policy || !policy->requirement) return false;
    const ph_mach_sender_observation *sender = ph_mach_sender(message);
    if (!sender || (uint32_t)audit_token_to_euid(sender->audit) != policy->euid
        || (uint32_t)audit_token_to_auid(sender->audit) != policy->auid
        || (uint32_t)audit_token_to_asid(sender->audit) != policy->asid) return false;
    ph_mach_code_observation verified = {0};
    if (ph_mach_observe_code(message, policy->requirement, &verified) != PH_MACH_CODE_OBSERVED) return false;
    bool okay = identity_matches(verified.code, &policy->identity);
    CFRelease(verified.code);
    return okay;
}

bool ph_native_xpc_caller_matches(xpc_object_t message, xpc_connection_t held,
    const ph_native_xpc_caller_policy *policy) {
    if (!message || !held || !policy || !policy->requirement || xpc_get_type(message) != XPC_TYPE_DICTIONARY
        || xpc_dictionary_get_remote_connection(message) != held
        || (uint32_t)xpc_connection_get_euid(held) != policy->euid
        || (uint32_t)xpc_connection_get_asid(held) != policy->asid) return false;
    SecCodeRef sender = NULL;
    if (SecCodeCreateWithXPCMessage(message, kSecCSDefaultFlags, &sender) || !sender) return false;
    bool okay = SecCodeCheckValidity(sender,kSecCSDefaultFlags,policy->requirement) == errSecSuccess
        && identity_matches(sender,&policy->identity);
    CFRelease(sender);
    return okay;
}
