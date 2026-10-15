#include "NativeSender.h"
#include <sys/acl.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
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

static bool checked(int fd, uid_t owner, bool directory, bool exact) {
    struct stat value;
    if (fstat(fd, &value) || value.st_uid != owner || (value.st_mode & 0022) || !acl_safe(fd)) return false;
    if (directory) return S_ISDIR(value.st_mode) && (!exact || (value.st_mode & 07777) == 0755);
    return S_ISREG(value.st_mode) && value.st_nlink == 1 && (value.st_mode & 07777) == 0644
        && value.st_size > 0 && value.st_size <= 16384;
}

static bool read_manifest(int parent, uid_t owner, uint8_t *bytes, size_t capacity, size_t *length) {
    int fd = openat(parent, "manifest.json", O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0) return false;
    struct stat before, after;
    bool okay = checked(fd, owner, false, true) && !fstat(fd, &before) && (size_t)before.st_size <= capacity;
    size_t used = 0;
    while (okay && used < (size_t)before.st_size) {
        ssize_t count = read(fd, bytes + used, (size_t)before.st_size - used);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) { okay = false; break; }
        used += (size_t)count;
    }
    uint8_t extra;
    if (okay && (read(fd, &extra, 1) != 0 || fstat(fd, &after) || !checked(fd, owner, false, true))) okay = false;
    if (okay && (before.st_dev != after.st_dev || before.st_ino != after.st_ino || before.st_gen != after.st_gen
        || before.st_size != after.st_size || before.st_mtimespec.tv_sec != after.st_mtimespec.tv_sec
        || before.st_mtimespec.tv_nsec != after.st_mtimespec.tv_nsec || before.st_ctimespec.tv_sec != after.st_ctimespec.tv_sec
        || before.st_ctimespec.tv_nsec != after.st_ctimespec.tv_nsec)) okay = false;
    close(fd);
    if (okay) *length = used;
    return okay;
}

bool ph_manifest_read(uint8_t *bytes, size_t capacity, size_t *length) {
    const char *parts[] = { "Library", "Application Support", "Poolhouse", "Approval" };
    int parent = open("/", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (parent < 0) return false;
    bool okay = checked(parent, 0, true, false);
    for (size_t index = 0; okay && index < 4; index++) {
        int child = openat(parent, parts[index], O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        okay = child >= 0 && checked(child, 0, true, index >= 2);
        close(parent);
        parent = child;
    }
    if (okay) okay = read_manifest(parent, 0, bytes, capacity, length);
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
    return checked(directory, owner, true, true) && read_manifest(directory, owner, bytes, capacity, length);
}
#endif
