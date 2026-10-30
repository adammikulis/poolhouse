#ifndef PH_NATIVE_CALLER_H
#define PH_NATIVE_CALLER_H
#include "MachSender.h"
#include "NativeIdentity.h"
#include <xpc/xpc.h>

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
/* Observation only: euid/asid are the connection-creation values. Requirement
 * and identity must come from independently authenticated protected policy. */
typedef struct {
    SecRequirementRef requirement;
    ph_native_identity_observation identity;
    uint32_t euid;
    uint32_t asid;
} ph_native_xpc_caller_policy;
bool ph_native_xpc_caller_matches(xpc_object_t message, xpc_connection_t held,
    const ph_native_xpc_caller_policy *policy);
#ifdef TESTING
bool ph_native_caller_test_status(CFTypeRef value);
#endif
#endif
