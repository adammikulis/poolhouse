#ifndef PH_NATIVE_SENDER_H
#define PH_NATIVE_SENDER_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <xpc/xpc.h>
typedef struct {
    uint8_t cdhash[20], sha256[32], publisher_kind;
    uint8_t publisher_digest[32], requirement_digest[32], entitlements_digest[32];
} ph_manifest_pin;
typedef struct {
    uint32_t owner_uid;
    uint64_t generation;
    uint8_t authority_id[32], installation_id[32], receipt_public_key[32];
    uint8_t policy_digest[32], purpose_policy[32], rotation_policy[32];
    ph_manifest_pin helper, gui, node;
} ph_manifest_values;
_Static_assert(sizeof(ph_manifest_pin) == 149, "manifest pin ABI");
_Static_assert(sizeof(ph_manifest_values) == 656, "manifest result ABI");
_Static_assert(offsetof(ph_manifest_values, generation) == 8, "manifest generation ABI");
_Static_assert(offsetof(ph_manifest_values, helper) == 208, "manifest helper ABI");
_Static_assert(offsetof(ph_manifest_values, gui) == 357, "manifest GUI ABI");
_Static_assert(offsetof(ph_manifest_values, node) == 506, "manifest node ABI");
/* Parse only. The caller must authenticate the protected source separately. */
bool ph_native_manifest_decode(const uint8_t *bytes, size_t length, ph_manifest_values *output);
#define PH_REQUIREMENT_LIMIT 16384
typedef struct {
    uint8_t bytes[3][PH_REQUIREMENT_LIMIT];
    size_t length[3];
} ph_protected_requirements;
/* Fixed order: authority helper, approval GUI, node. A protected directory is
 * custody, not authentication or a rollback-resistant installation witness. */
typedef struct ph_policy_files ph_policy_files;
/* One serialized owner retains the complete descriptor set until destruction.
 * A valid snapshot is file custody only, not an authenticated installation. */
bool ph_policy_files_begin(ph_policy_files **output, uint8_t *manifest, size_t capacity, size_t *length);
bool ph_policy_files_requirements(ph_policy_files *files, uint64_t generation, ph_protected_requirements *output);
bool ph_policy_files_validate(ph_policy_files *files);
void ph_policy_files_destroy(ph_policy_files **files);
bool ph_local_session(uint32_t owner, uint32_t *asid);
bool ph_message_caller(xpc_object_t message, const char *cdhash);
bool ph_self_code(const char *cdhash);
#ifdef TESTING
bool ph_manifest_fixture_acl(int fd, bool mutation);
bool ph_policy_files_fixture_begin(int root, uint32_t owner, ph_policy_files **output,
    uint8_t *manifest, size_t capacity, size_t *length);
void ph_policy_files_test_hook(void (*hook)(void));
#endif
#endif
