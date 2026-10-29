#include "NativeCaller.h"
#include "MachCode.h"
#include <bsm/libbsm.h>
#include <string.h>

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
    ph_native_identity_observation current = {0};
    bool okay = ph_native_identity_observe(verified.code, &current)
        && (current.signing_flags & kSecCodeSignatureRuntime) != 0
        && current.signing_flags == policy->identity.signing_flags
        && current.entitlements_present == policy->identity.entitlements_present
        && current.publisher_present == policy->identity.publisher_present
        && !memcmp(current.code_directory, policy->identity.code_directory, sizeof(current.code_directory))
        && !memcmp(current.requirement_digest, policy->identity.requirement_digest, sizeof(current.requirement_digest))
        && !memcmp(current.entitlements_digest, policy->identity.entitlements_digest, sizeof(current.entitlements_digest))
        && !memcmp(current.publisher_digest, policy->identity.publisher_digest, sizeof(current.publisher_digest));
    CFRelease(verified.code);
    return okay;
}
