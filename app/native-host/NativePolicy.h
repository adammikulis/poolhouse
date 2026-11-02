#ifndef PH_NATIVE_POLICY_H
#define PH_NATIVE_POLICY_H
#include "../native-approval/NativeSender.h"
#include <Security/Security.h>

/* A captured candidate is not authenticated policy or a Store witness. The
 * caller must bind it to the independently authenticated protected installation
 * and current generation before use, and revalidate that binding at commit. */
typedef struct ph_native_policy_candidate ph_native_policy_candidate;
enum { PH_POLICY_HELPER, PH_POLICY_GUI, PH_POLICY_NODE };
bool ph_native_policy_capture(ph_native_policy_candidate **output);
bool ph_native_policy_validate(const ph_native_policy_candidate *candidate);
/* Borrowed values are valid only while the same serialized owner holds the
 * candidate. They are not transferable capabilities or authorization. */
const ph_manifest_values *ph_native_policy_manifest(const ph_native_policy_candidate *candidate);
SecRequirementRef ph_native_policy_requirement(const ph_native_policy_candidate *candidate, unsigned role);
void ph_native_policy_destroy(ph_native_policy_candidate **candidate);
#ifdef TESTING
bool ph_native_policy_fixture(int root, uint32_t owner, ph_native_policy_candidate **output);
#endif
#endif
