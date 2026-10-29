#include "NativeSender.h"
#include <sys/acl.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
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
    return S_ISREG(value.st_mode) && value.st_nlink == 1 && value.st_size > 0 && value.st_size <= 16384;
}

struct held {
    int fd, parent;
    mode_t mode;
    bool directory;
    char name[32];
    struct stat original;
};
struct ph_policy_files {
    uid_t owner;
    struct held dirs[8], files[4];
    bool requirements, poisoned;
};

static bool same_inode(const struct stat *left, const struct stat *right) {
    return left->st_dev == right->st_dev && left->st_ino == right->st_ino && left->st_gen == right->st_gen;
}
static bool same_file(const struct stat *left, const struct stat *right) {
    return same_inode(left, right) && left->st_size == right->st_size
        && left->st_mtimespec.tv_sec == right->st_mtimespec.tv_sec && left->st_mtimespec.tv_nsec == right->st_mtimespec.tv_nsec
        && left->st_ctimespec.tv_sec == right->st_ctimespec.tv_sec && left->st_ctimespec.tv_nsec == right->st_ctimespec.tv_nsec;
}
static bool valid_held(const ph_policy_files *files, const struct held *held, bool content) {
    struct stat current, entry;
    if (held->fd < 0 || !checked(held->fd, files->owner, held->directory, held->mode)
        || fstat(held->fd, &current) || !same_inode(&held->original, &current)) return false;
    if (content && !same_file(&held->original, &current)) return false;
    if (held->parent >= 0 && (fstatat(files->dirs[held->parent].fd, held->name, &entry, AT_SYMLINK_NOFOLLOW)
        || !same_inode(&held->original, &entry))) return false;
    return true;
}
static bool add_dir(ph_policy_files *files, unsigned index, int parent, const char *name, mode_t mode) {
    struct held *held = &files->dirs[index];
    int fd = openat(files->dirs[parent].fd, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return false;
    held->fd = fd; held->parent = parent; held->mode = mode; held->directory = true;
    if (strlen(name) >= sizeof(held->name)) return false;
    memcpy(held->name, name, strlen(name) + 1);
    return checked(fd, files->owner, true, mode) && !fstat(fd, &held->original) && valid_held(files, held, false);
}
static bool add_file(ph_policy_files *files, unsigned index, int parent, const char *name, mode_t mode,
    uint8_t *bytes, size_t capacity, size_t *length) {
    struct held *held = &files->files[index];
    int fd = openat(files->dirs[parent].fd, name, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0) return false;
    held->fd = fd; held->parent = parent; held->mode = mode; held->directory = false;
    if (strlen(name) >= sizeof(held->name)) return false;
    memcpy(held->name, name, strlen(name) + 1);
    if (!checked(fd, files->owner, false, mode) || fstat(fd, &held->original)
        || (size_t)held->original.st_size > capacity) return false;
    size_t used = 0;
    while (used < (size_t)held->original.st_size) {
        ssize_t n = read(fd, bytes + used, (size_t)held->original.st_size - used);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return false;
        used += (size_t)n;
    }
    uint8_t extra; ssize_t trailing;
    do { trailing = read(fd, &extra, 1); } while (trailing < 0 && errno == EINTR);
    if (trailing != 0 || !valid_held(files, held, true)) return false;
    *length = used;
    return true;
}

static bool valid_snapshot(const ph_policy_files *files) {
    for (unsigned i = 0; i < 5; i++) if (files->dirs[i].fd < 0) return false;
    if (files->requirements) for (unsigned i = 5; i < 8; i++) if (files->dirs[i].fd < 0) return false;
    for (unsigned i = 0; i < 8; i++) {
        if (files->dirs[i].fd < 0) continue;
        if (!valid_held(files, &files->dirs[i], files->requirements && i == 7)) return false;
    }
    for (unsigned i = 0; i < 4; i++) {
        if (files->files[i].fd < 0) continue;
        if (!valid_held(files, &files->files[i], true)) return false;
    }
    return files->files[0].fd >= 0 && (!files->requirements ||
        (files->files[1].fd >= 0 && files->files[2].fd >= 0 && files->files[3].fd >= 0));
}
bool ph_policy_files_validate(ph_policy_files *files) {
    if (!files || files->poisoned) return false;
    if (valid_snapshot(files)) return true;
    files->poisoned = true;
    return false;
}
void ph_policy_files_destroy(ph_policy_files **files) {
    if (!files || !*files) return;
    ph_policy_files *owned = *files; *files = NULL;
    for (unsigned i = 0; i < 4; i++) if (owned->files[i].fd >= 0) close(owned->files[i].fd);
    for (unsigned i = 0; i < 8; i++) if (owned->dirs[i].fd >= 0) close(owned->dirs[i].fd);
    free(owned);
}
static ph_policy_files *make_files(int root, uid_t owner) {
    ph_policy_files *files = calloc(1, sizeof(*files));
    if (!files) { close(root); return NULL; }
    for (unsigned i = 0; i < 8; i++) files->dirs[i].fd = -1;
    for (unsigned i = 0; i < 4; i++) files->files[i].fd = -1;
    files->owner = owner; files->dirs[0].fd = root; files->dirs[0].parent = -1; files->dirs[0].directory = true;
    if (!checked(root, owner, true, 0) || fstat(root, &files->dirs[0].original)) { ph_policy_files_destroy(&files); return NULL; }
    return files;
}
static bool begin_at(ph_policy_files *files, ph_policy_files **output, uint8_t *manifest, size_t capacity, size_t *length) {
    uint8_t bytes[16384]; size_t count = 0;
    if (!add_dir(files, 4, 3, "Approval", 0755)
        || !add_file(files, 0, 4, "manifest.json", 0644, bytes, sizeof(bytes), &count)
        || count > capacity || !ph_policy_files_validate(files)) { ph_policy_files_destroy(&files); return false; }
    memcpy(manifest, bytes, count); *length = count; *output = files;
    return true;
}
bool ph_policy_files_begin(ph_policy_files **output, uint8_t *manifest, size_t capacity, size_t *length) {
    if (!output || *output || !manifest || !length) return false;
    int root = open("/", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (root < 0) return false;
    ph_policy_files *files = make_files(root, 0);
    if (!files) return false;
    if (!add_dir(files, 1, 0, "Library", 0) || !add_dir(files, 2, 1, "Application Support", 0)
        || !add_dir(files, 3, 2, "Poolhouse", 0755)) { ph_policy_files_destroy(&files); return false; }
    return begin_at(files, output, manifest, capacity, length);
}
#ifdef TESTING
static void (*before_policy_verify)(void);
void ph_policy_files_test_hook(void (*hook)(void)) { before_policy_verify = hook; }
#endif
bool ph_policy_files_requirements(ph_policy_files *files, uint64_t generation, ph_protected_requirements *output) {
    if (!files || files->poisoned || files->requirements || !generation || !output) return false;
    char name[21];
    int count = snprintf(name, sizeof(name), "%llu", (unsigned long long)generation);
    if (count <= 0 || (size_t)count >= sizeof(name)) return false;
    ph_protected_requirements result = {0};
    bool okay = add_dir(files, 5, 3, "Authority", 0700) && add_dir(files, 6, 5, "generations", 0700)
        && add_dir(files, 7, 6, name, 0700);
    const char *names[] = {"helper.requirement", "gui.requirement", "node.requirement"};
    for (unsigned i = 0; okay && i < 3; i++)
        okay = add_file(files, i + 1, 7, names[i], 0600, result.bytes[i], sizeof(result.bytes[i]), &result.length[i]);
    files->requirements = okay;
#ifdef TESTING
    if (okay && before_policy_verify) before_policy_verify();
#endif
    if (!okay || !ph_policy_files_validate(files)) { files->poisoned = true; return false; }
    *output = result;
    return true;
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
bool ph_policy_files_fixture_begin(int root, uint32_t owner, ph_policy_files **output,
    uint8_t *manifest, size_t capacity, size_t *length) {
    if (root < 0 || !output || *output || !manifest || !length) return false;
    int copy = fcntl(root, F_DUPFD_CLOEXEC, 0);
    if (copy < 0) return false;
    ph_policy_files *files = make_files(copy, owner);
    if (!files) return false;
    for (unsigned i = 1; i <= 3; i++) {
        files->dirs[i].fd = fcntl(copy, F_DUPFD_CLOEXEC, 0);
        files->dirs[i].parent = -1; files->dirs[i].directory = true;
        if (files->dirs[i].fd < 0 || fstat(files->dirs[i].fd, &files->dirs[i].original)) {
            ph_policy_files_destroy(&files); return false;
        }
    }
    return begin_at(files, output, manifest, capacity, length);
}
#endif
