#include "NativeSender.h"
#include <Security/Security.h>
#include <bsm/audit.h>
#include <unistd.h>
#include <string.h>

bool ph_local_session(uint32_t owner, uint32_t *asid) {
    au_id_t auid;
    auditinfo_addr_t info;
    memset(&info, 0, sizeof(info));
    if (!owner || getuid() != owner || geteuid() != owner || getauid(&auid) || auid != owner
        || getaudit_addr(&info, sizeof(info)) || info.ai_auid != owner || info.ai_asid == AU_DEFAUDITSID
        || !(info.ai_flags & AU_SESSION_FLAG_HAS_GRAPHIC_ACCESS) || (info.ai_flags & AU_SESSION_FLAG_IS_REMOTE)) return false;
    *asid = info.ai_asid;
    return true;
}

static bool code_matches(SecCodeRef code, const char *pin) {
    if (!code || strlen(pin) != 40) return false;
    for (size_t i = 0; i < 40; i++) if (!((pin[i] >= '0' && pin[i] <= '9') || (pin[i] >= 'a' && pin[i] <= 'f'))) return false;
    CFStringRef rule = CFStringCreateWithFormat(NULL, NULL, CFSTR("cdhash H\"%s\""), pin);
    SecRequirementRef requirement = NULL;
    OSStatus status = SecRequirementCreateWithString(rule, kSecCSDefaultFlags, &requirement);
    CFRelease(rule);
    if (status || !requirement) return false;
    status = SecCodeCheckValidity(code, kSecCSDefaultFlags, requirement);
    CFRelease(requirement);
    if (status) return false;
    CFDictionaryRef information = NULL;
    if (SecCodeCopySigningInformation(code, kSecCSSigningInformation, &information) || !information) return false;
    CFTypeRef unique = CFDictionaryGetValue(information, kSecCodeInfoUnique);
    bool okay = unique && CFGetTypeID(unique) == CFDataGetTypeID() && CFDataGetLength(unique) == 20;
    const UInt8 *bytes = okay ? CFDataGetBytePtr(unique) : NULL;
    const char alphabet[] = "0123456789abcdef";
    for (size_t index = 0; okay && index < 20; index++)
        okay = pin[index * 2] == alphabet[bytes[index] >> 4] && pin[index * 2 + 1] == alphabet[bytes[index] & 15];
    CFRelease(information);
    return okay;
}

bool ph_message_peer(xpc_object_t message, const char *cdhash) {
    if (xpc_get_type(message) != XPC_TYPE_DICTIONARY) return false;
    xpc_connection_t peer = xpc_dictionary_get_remote_connection(message);
    if (!peer || xpc_connection_get_euid(peer) != 0) return false;
    SecCodeRef code = NULL;
    if (SecCodeCreateWithXPCMessage(message, kSecCSDefaultFlags, &code) || !code) return false;
    bool okay = code_matches(code, cdhash) && xpc_connection_get_euid(peer) == 0;
    CFRelease(code);
    return okay;
}

bool ph_self_code(const char *cdhash) {
    SecCodeRef code = NULL;
    if (SecCodeCopySelf(kSecCSDefaultFlags, &code) || !code) return false;
    bool okay = code_matches(code, cdhash);
    CFRelease(code);
    return okay;
}
