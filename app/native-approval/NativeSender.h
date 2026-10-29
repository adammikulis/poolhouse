#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <xpc/xpc.h>
bool ph_manifest_read(uint8_t *bytes, size_t capacity, size_t *length);
#define PH_REQUIREMENT_LIMIT 16384
typedef struct {
    uint8_t bytes[3][PH_REQUIREMENT_LIMIT];
    size_t length[3];
} ph_protected_requirements;
/* Fixed order: authority helper, approval GUI, node. A protected directory is
 * custody, not authentication or a rollback-resistant installation witness. */
bool ph_requirements_read(uint64_t generation, ph_protected_requirements *output);
bool ph_local_session(uint32_t owner, uint32_t *asid);
bool ph_message_caller(xpc_object_t message, const char *cdhash);
bool ph_self_code(const char *cdhash);
#ifdef TESTING
bool ph_manifest_fixture_acl(int fd, bool mutation);
bool ph_manifest_fixture(int directory, uint32_t owner, uint8_t *bytes, size_t capacity, size_t *length);
bool ph_requirements_fixture(int directory, uint32_t owner, uint64_t generation, ph_protected_requirements *output);
void ph_requirements_test_hook(void (*hook)(void));
#endif
