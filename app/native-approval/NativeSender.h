#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <xpc/xpc.h>
bool ph_manifest_read(uint8_t *bytes, size_t capacity, size_t *length);
bool ph_local_session(uint32_t owner, uint32_t *asid);
bool ph_message_caller(xpc_object_t message, const char *cdhash);
bool ph_self_code(const char *cdhash);
#ifdef TESTING
bool ph_manifest_fixture_acl(int fd, bool mutation);
bool ph_manifest_fixture(int directory, uint32_t owner, uint8_t *bytes, size_t capacity, size_t *length);
#endif
