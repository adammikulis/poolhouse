#ifndef PH_NATIVE_CALLER_H
#define PH_NATIVE_CALLER_H
#include "MachSender.h"
#include "NativeIdentity.h"

/* These values are meaningful only when independently loaded from an
 * authenticated installation and current owner session. Never accept a policy
 * from the message, or treat a match as an approval or durable capability. */
typedef struct {
    SecRequirementRef requirement;
    ph_native_identity_observation identity;
    uint32_t euid;
    uint32_t auid;
    uint32_t asid;
} ph_native_caller_policy;
bool ph_native_caller_matches(const ph_mach_message *message, const ph_native_caller_policy *policy);
#endif
