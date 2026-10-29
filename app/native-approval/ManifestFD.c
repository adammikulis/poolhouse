#include "NativeSender.h"
#include <sys/acl.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>

static bool acl_safe(int fd) {
    filesec_t security = filesec_init();
    if (!security) return false;
    struct stat value;
    int present = 0;
    acl_t acl = NULL;
    bool safe = fstatx_np(fd, &value, security) == 0 && filesec_query_property(security, FILESEC_ACL, &present) == 0;
    if (safe && present) safe = filesec_get_property(security, FILESEC_ACL, &acl) == 0 && acl && acl_valid(acl) == 0;
    filesec_free(security);
    if (!safe || !present) { if (acl) acl_free(acl); return safe; }
    for (int index = 0; index <= 169; index++) {
        acl_entry_t entry = NULL;
        errno = 0;
        if (acl_get_entry(acl, index, &entry)) { safe = errno == EINVAL; break; }
        if (index == 169 || !entry) { safe = false; break; }
        acl_tag_t tag;
        acl_permset_mask_t mask;
        if (acl_get_tag_type(entry, &tag) || acl_get_permset_mask_np(entry, &mask)) { safe = false; break; }
        const acl_permset_mask_t readonly = ACL_READ_DATA | ACL_EXECUTE | ACL_READ_ATTRIBUTES
            | ACL_READ_EXTATTRIBUTES | ACL_READ_SECURITY | ACL_SYNCHRONIZE;
        if (tag == ACL_EXTENDED_ALLOW && (mask & ~readonly)) { safe = false; break; }
        if (tag != ACL_EXTENDED_ALLOW && tag != ACL_EXTENDED_DENY) { safe = false; break; }
    }
    acl_free(acl);
    return safe;
}

static bool checked(int fd, uid_t owner, bool directory, mode_t exact_mode) {
    struct stat value;
    if (fstat(fd, &value) || value.st_uid != owner || (value.st_mode & 0022) || !acl_safe(fd)) return false;
    if (exact_mode && (value.st_mode & 07777) != exact_mode) return false;
    if (directory) return S_ISDIR(value.st_mode);
    return S_ISREG(value.st_mode) && value.st_nlink == 1
        && value.st_size > 0 && value.st_size <= 16384;
}

static bool read_fixed(int parent, const char *name, uid_t owner, mode_t mode, uint8_t *bytes, size_t capacity, size_t *length) {
    int fd = openat(parent, name, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0) return false;
    struct stat before, after, entry;
    bool okay = checked(fd, owner, false, mode) && !fstat(fd, &before) && (size_t)before.st_size <= capacity;
    size_t used = 0;
    while (okay && used < (size_t)before.st_size) {
        ssize_t count = read(fd, bytes + used, (size_t)before.st_size - used);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) { okay = false; break; }
        used += (size_t)count;
    }
    uint8_t extra;
    ssize_t trailing = 0;
    if (okay) { do { trailing = read(fd, &extra, 1); } while (trailing < 0 && errno == EINTR); }
    if (okay && (trailing != 0 || fstat(fd, &after) || !checked(fd, owner, false, mode)
        || fstatat(parent, name, &entry, AT_SYMLINK_NOFOLLOW))) okay = false;
    if (okay && (before.st_dev != after.st_dev || before.st_ino != after.st_ino || before.st_gen != after.st_gen
        || before.st_size != after.st_size || before.st_mtimespec.tv_sec != after.st_mtimespec.tv_sec
        || before.st_mtimespec.tv_nsec != after.st_mtimespec.tv_nsec || before.st_ctimespec.tv_sec != after.st_ctimespec.tv_sec
        || before.st_ctimespec.tv_nsec != after.st_ctimespec.tv_nsec
        || before.st_dev != entry.st_dev || before.st_ino != entry.st_ino || before.st_gen != entry.st_gen)) okay = false;
    close(fd);
    if (okay) *length = used;
    return okay;
}

static bool read_manifest(int parent, uid_t owner, uint8_t *bytes, size_t capacity, size_t *length) {
    return read_fixed(parent, "manifest.json", owner, 0644, bytes, capacity, length);
}

#ifdef TESTING
static void (*before_requirement_verify)(void);
void ph_requirements_test_hook(void (*hook)(void)) { before_requirement_verify = hook; }
#endif

bool ph_manifest_read(uint8_t *bytes, size_t capacity, size_t *length) {
    const char *parts[] = { "Library", "Application Support", "Poolhouse", "Approval" };
    int parent = open("/", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (parent < 0) return false;
    bool okay = checked(parent, 0, true, 0);
    for (size_t index = 0; okay && index < 4; index++) {
        int child = openat(parent, parts[index], O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        okay = child >= 0 && checked(child, 0, true, index >= 2 ? 0755 : 0);
        close(parent);
        parent = child;
    }
    if (okay) okay = read_manifest(parent, 0, bytes, capacity, length);
    if (parent >= 0) close(parent);
    return okay;
}

static bool requirements_at(int generations, uid_t owner, uint64_t generation, ph_protected_requirements *output) {
    if (!generation || !output || !checked(generations, owner, true, 0700)) return false;
    char name[21];
    int count = snprintf(name, sizeof(name), "%llu", (unsigned long long)generation);
    if (count <= 0 || (size_t)count >= sizeof(name)) return false;
    int directory = openat(generations, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (directory < 0) return false;
    struct stat before, after, entry;
    ph_protected_requirements result = {0};
    bool okay = checked(directory, owner, true, 0700) && !fstat(directory, &before);
    const char *files[] = {"helper.requirement", "gui.requirement", "node.requirement"};
    for (size_t index = 0; okay && index < 3; index++)
        okay = read_fixed(directory, files[index], owner, 0600, result.bytes[index], sizeof(result.bytes[index]), &result.length[index]);
#ifdef TESTING
    if (okay && before_requirement_verify) before_requirement_verify();
#endif
    if (okay && (fstat(directory, &after) || !checked(directory, owner, true, 0700)
        || fstatat(generations, name, &entry, AT_SYMLINK_NOFOLLOW))) okay = false;
    if (okay && (before.st_dev != after.st_dev || before.st_ino != after.st_ino || before.st_gen != after.st_gen
        || before.st_mtimespec.tv_sec != after.st_mtimespec.tv_sec || before.st_mtimespec.tv_nsec != after.st_mtimespec.tv_nsec
        || before.st_ctimespec.tv_sec != after.st_ctimespec.tv_sec || before.st_ctimespec.tv_nsec != after.st_ctimespec.tv_nsec
        || before.st_dev != entry.st_dev || before.st_ino != entry.st_ino || before.st_gen != entry.st_gen)) okay = false;
    close(directory);
    if (okay) *output = result;
    return okay;
}

bool ph_requirements_read(uint64_t generation, ph_protected_requirements *output) {
    if (!generation || !output) return false;
    const char *parts[] = {"Library", "Application Support", "Poolhouse", "Authority", "generations"};
    int parent = open("/", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (parent < 0) return false;
    bool okay = checked(parent, 0, true, 0);
    for (size_t index = 0; okay && index < 5; index++) {
        int child = openat(parent, parts[index], O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        mode_t mode = index < 2 ? 0 : index == 2 ? 0755 : 0700;
        okay = child >= 0 && checked(child, 0, true, mode);
        close(parent);
        parent = child;
    }
    if (okay) okay = requirements_at(parent, 0, generation, output);
    if (parent >= 0) close(parent);
    return okay;
}

#ifdef TESTING
bool ph_manifest_fixture_acl(int fd, bool mutation) {
    acl_t acl = acl_init(1);
    if (!acl) return false;
    acl_entry_t entry;
    unsigned char invented[16];
    memset(invented, 17, sizeof(invented));
    bool okay = acl_create_entry(&acl, &entry) == 0 && acl_set_tag_type(entry, ACL_EXTENDED_ALLOW) == 0
        && acl_set_qualifier(entry, invented) == 0
        && acl_set_permset_mask_np(entry, mutation ? ACL_WRITE_DATA : ACL_READ_DATA) == 0
        && acl_set_fd_np(fd, acl, ACL_TYPE_EXTENDED) == 0;
    acl_free(acl);
    return okay;
}

bool ph_manifest_fixture(int directory, uint32_t owner, uint8_t *bytes, size_t capacity, size_t *length) {
    return checked(directory, owner, true, 0755) && read_manifest(directory, owner, bytes, capacity, length);
}
bool ph_requirements_fixture(int directory, uint32_t owner, uint64_t generation, ph_protected_requirements *output) {
    return requirements_at(directory, owner, generation, output);
}
#endif
