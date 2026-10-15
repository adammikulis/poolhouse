#ifndef PH_HOST_CAPTURE_H
#define PH_HOST_CAPTURE_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
int ph_host_open(const char *path, bool protected_store);
bool ph_host_tree(int root, const char *paths);
bool ph_host_protected_tree(const char *path, const char *paths);
int ph_host_inventory(const char *path, unsigned char *bytes);
bool ph_host_read(int root, const char *path, uint64_t size, uint32_t mode, unsigned char *bytes);
bool ph_host_root_custody(const char *path);
bool ph_host_protected_code(const char *directory, const char *paths, const char *helper_pin, const char *gui_pin);
#ifdef TESTING
bool ph_host_test_header(uint32_t magic, int32_t cpu, int32_t subtype, uint32_t type);
bool ph_host_test_policy(uint32_t flags, const char *entitlement);
#endif
#endif
