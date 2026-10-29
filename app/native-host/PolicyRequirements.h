#ifndef PH_POLICY_REQUIREMENTS_H
#define PH_POLICY_REQUIREMENTS_H
#include "../native-approval/NativeSender.h"
#include <Security/Security.h>

/* The caller must independently authenticate the installation, policy and
 * generation. Fixed order: helper, GUI, node. Output entries must start NULL;
 * successful calls transfer three retained requirements to the caller. */
bool ph_policy_requirements_load(uint64_t generation, const uint8_t expected[3][32], SecRequirementRef output[3]);
#ifdef TESTING
bool ph_policy_requirements_test_decode(const ph_protected_requirements *raw, const uint8_t expected[3][32],
    SecRequirementRef output[3]);
#endif
#endif
