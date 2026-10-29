#include "MachCode.h"

ph_mach_code_result ph_mach_observe_code(const ph_mach_message *message,
    SecRequirementRef requirement, ph_mach_code_observation *output) {
    const ph_mach_sender_observation *observed_sender = ph_mach_sender(message);
    if (!observed_sender || !output || !requirement || CFGetTypeID(requirement) != SecRequirementGetTypeID())
        return PH_MACH_CODE_INVALID_ARGUMENT;
    ph_mach_sender_observation sender = *observed_sender;
    /* Use the complete native kernel token, including PID version. Never rebuild
     * it from independently obtained process/account data or message body fields. */
    CFDataRef token = CFDataCreate(kCFAllocatorDefault, (const UInt8 *)&sender.audit,
        sizeof(sender.audit));
    if (!token) return PH_MACH_CODE_UNKNOWN;
    const void *key = kSecGuestAttributeAudit, *value = token;
    CFDictionaryRef attributes = CFDictionaryCreate(kCFAllocatorDefault, &key, &value, 1,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CFRelease(token);
    if (!attributes) return PH_MACH_CODE_UNKNOWN;
    SecCodeRef code = NULL;
    OSStatus status = SecCodeCopyGuestWithAttributes(NULL, attributes, kSecCSDefaultFlags, &code);
    CFRelease(attributes);
    if (status != errSecSuccess || !code) {
        if (code) CFRelease(code);
        return PH_MACH_CODE_UNKNOWN;
    }
    status = SecCodeCheckValidity(code, kSecCSNoNetworkAccess, requirement);
    if (status != errSecSuccess) {
        CFRelease(code);
        return PH_MACH_CODE_UNKNOWN;
    }
    ph_mach_code_observation observed = {.sender = sender, .code = code};
    *output = observed;
    return PH_MACH_CODE_OBSERVED;
}
