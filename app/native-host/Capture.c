#include "Capture.h"
#include <sys/acl.h>
#include <sys/stat.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static bool clean_acl(int fd) {
    filesec_t security = filesec_init();
    if (!security) return false;
    struct stat st;
    int present = 0;
    acl_t acl = NULL;
    bool okay = fstatx_np(fd, &st, security) == 0 && filesec_query_property(security, FILESEC_ACL, &present) == 0;
    if (okay && present) okay = filesec_get_property(security, FILESEC_ACL, &acl) == 0 && acl && acl_valid(acl) == 0;
    filesec_free(security);
    if (!okay || !present) { if (acl) acl_free(acl); return okay; }
    for (int index = 0; index <= ACL_MAX_ENTRIES; index++) {
        acl_entry_t entry = NULL;
        errno = 0;
        if (acl_get_entry(acl, index, &entry)) { okay = errno == EINVAL; break; }
        if (index == ACL_MAX_ENTRIES || !entry) { okay = false; break; }
        acl_tag_t tag;
        acl_permset_mask_t mask;
        if (acl_get_tag_type(entry, &tag) || acl_get_permset_mask_np(entry, &mask)) { okay = false; break; }
        const acl_permset_mask_t readonly = ACL_READ_DATA | ACL_EXECUTE | ACL_READ_ATTRIBUTES
            | ACL_READ_EXTATTRIBUTES | ACL_READ_SECURITY | ACL_SYNCHRONIZE;
        if ((tag == ACL_EXTENDED_ALLOW && (mask & ~readonly))
            || (tag != ACL_EXTENDED_ALLOW && tag != ACL_EXTENDED_DENY)) { okay = false; break; }
    }
    acl_free(acl); return okay;
}

static bool directory(int fd, bool protected_store) {
    struct stat st;
    return fstat(fd, &st) == 0 && S_ISDIR(st.st_mode) && clean_acl(fd)
        && (!protected_store || (st.st_uid == 0 && !(st.st_mode & 022)));
}

static int descend(int start, const char *path, bool protected_store) {
    int fd = dup(start);
    char *copy = strdup(path);
    if (fd < 0 || !copy) { if (fd >= 0) close(fd); free(copy); return -1; }
    char *part = copy;
    while (*part) {
        char *slash = strchr(part, '/');
        if (slash) *slash = 0;
        if (!*part || !strcmp(part, ".") || !strcmp(part, "..")) { close(fd); free(copy); return -1; }
        int next = openat(fd, part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
        close(fd); fd = next;
        if (fd < 0 || !directory(fd, protected_store)) { if (fd >= 0) close(fd); free(copy); return -1; }
        if (!slash) break;
        part = slash + 1;
        if (!*part) { close(fd); free(copy); return -1; }
    }
    free(copy); return fd;
}

int ph_host_open(const char *path, bool protected_store) {
    if (!path || path[0] != '/' || !path[1]) return -1;
    int root = open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (root < 0 || !directory(root, protected_store)) { if (root >= 0) close(root); return -1; }
    int fd = descend(root, path + 1, protected_store);
    close(root); return fd;
}

static bool same(const struct stat *a, const struct stat *b) {
    return a->st_dev == b->st_dev && a->st_ino == b->st_ino && a->st_size == b->st_size
        && a->st_mode == b->st_mode && a->st_uid == b->st_uid && a->st_gid == b->st_gid && a->st_nlink == b->st_nlink
        && a->st_mtimespec.tv_sec == b->st_mtimespec.tv_sec && a->st_mtimespec.tv_nsec == b->st_mtimespec.tv_nsec
        && a->st_ctimespec.tv_sec == b->st_ctimespec.tv_sec && a->st_ctimespec.tv_nsec == b->st_ctimespec.tv_nsec;
}

bool ph_host_read(int root, const char *path, uint64_t size, uint32_t mode, unsigned char *bytes) {
    if (!path || !*path || size > 64ULL * 1024 * 1024 || !bytes || (mode != 0644 && mode != 0755)) return false;
    char *copy = strdup(path);
    if (!copy) return false;
    char *slash = strrchr(copy, '/');
    int parent = dup(root);
    const char *name = copy;
    if (slash) { *slash = 0; if (parent >= 0) close(parent); parent = descend(root, copy, false); name = slash + 1; }
    if (parent < 0 || !*name || !strcmp(name, ".") || !strcmp(name, "..")) { if (parent >= 0) close(parent); free(copy); return false; }
    int fd = openat(parent, name, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    close(parent); free(copy);
    if (fd < 0) return false;
    struct stat before, after, owner;
    bool okay = fstat(root, &owner) == 0 && fstat(fd, &before) == 0 && S_ISREG(before.st_mode) && before.st_nlink == 1
        && before.st_uid == owner.st_uid && (before.st_mode & 07777) == mode && before.st_size >= 0
        && (uint64_t)before.st_size == size && clean_acl(fd);
    uint64_t offset = 0;
    while (okay && offset < size) {
        ssize_t count = pread(fd, bytes + offset, (size_t)(size - offset), (off_t)offset);
        if (count <= 0) okay = false; else offset += (uint64_t)count;
    }
    unsigned char extra;
    okay = okay && pread(fd, &extra, 1, (off_t)size) == 0 && fstat(fd, &after) == 0 && same(&before, &after) && clean_acl(fd);
    close(fd); return okay;
}

static bool listed(const char *paths, const char *path, bool is_directory) {
    size_t length = strlen(path);
    for (const char *line = paths; *line;) {
        const char *end = strchr(line, '\n');
        if (!end) return false;
        size_t count = (size_t)(end - line);
        if (count >= length && !memcmp(line, path, length)
            && (is_directory ? (count > length && line[length] == '/') : count == length)) return true;
        line = end + 1;
    }
    return false;
}

static bool walk(int root, const char *prefix, const char *paths, bool protected_store, size_t *files) {
    int duplicate = openat(root, ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (duplicate < 0) return false;
    DIR *stream = fdopendir(duplicate);
    if (!stream) { close(duplicate); return false; }
    bool okay = true;
    struct dirent *entry;
    while (okay) {
        errno = 0;
        entry = readdir(stream);
        if (!entry) { okay = errno == 0; break; }
        if (!strcmp(entry->d_name, ".") || !strcmp(entry->d_name, "..")) continue;
        size_t length = strlen(prefix) + strlen(entry->d_name) + 2;
        if (length > 16384) { okay = false; break; }
        char *path = malloc(length);
        if (!path) { okay = false; break; }
        if (*prefix) snprintf(path, length, "%s/%s", prefix, entry->d_name); else snprintf(path, length, "%s", entry->d_name);
        struct stat st;
        okay = fstatat(root, entry->d_name, &st, AT_SYMLINK_NOFOLLOW) == 0;
        if (okay && S_ISDIR(st.st_mode)) {
            okay = listed(paths, path, true);
            int child = okay ? openat(root, entry->d_name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC) : -1;
            okay = child >= 0 && directory(child, protected_store) && walk(child, path, paths, protected_store, files);
            if (child >= 0) close(child);
        } else if (okay) {
            okay = S_ISREG(st.st_mode) && st.st_nlink == 1 && listed(paths, path, false) && ++*files <= 64;
            if (okay && protected_store) {
                int fd = openat(root, entry->d_name, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
                okay = fd >= 0 && fstat(fd, &st) == 0 && st.st_uid == 0 && !(st.st_mode & 022) && clean_acl(fd);
                if (fd >= 0) close(fd);
            }
        }
        free(path);
    }
    closedir(stream); return okay;
}

bool ph_host_tree(int root, const char *paths) {
    size_t files = 0, expected = 0;
    for (const char *p = paths; *p; p++) if (*p == '\n') expected++;
    return expected > 0 && expected <= 64 && walk(root, "", paths, false, &files) && files == expected;
}

bool ph_host_protected_tree(const char *path, const char *paths) {
    int root = ph_host_open(path, true);
    if (root < 0) return false;
    size_t files = 0, expected = 0;
    for (const char *p = paths; *p; p++) if (*p == '\n') expected++;
    bool okay = expected > 0 && expected <= 64 && walk(root, "", paths, true, &files) && files == expected;
    close(root); return okay;
}

int ph_host_inventory(const char *path, unsigned char *bytes) {
    if (!path || path[0] != '/' || !bytes) return -1;
    char *copy = strdup(path);
    if (!copy) return -1;
    char *slash = strrchr(copy, '/');
    if (!slash || !slash[1]) { free(copy); return -1; }
    *slash = 0;
    int parent = *copy ? ph_host_open(copy, false) : open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    int fd = parent >= 0 ? openat(parent, slash + 1, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK) : -1;
    if (parent >= 0) close(parent);
    free(copy);
    if (fd < 0) return -1;
    struct stat before, after;
    bool okay = fstat(fd, &before) == 0 && S_ISREG(before.st_mode) && before.st_nlink == 1
        && before.st_size > 0 && before.st_size <= 16384 && clean_acl(fd);
    size_t offset = 0, size = okay ? (size_t)before.st_size : 0;
    while (okay && offset < size) {
        ssize_t count = pread(fd, bytes + offset, size - offset, (off_t)offset);
        if (count <= 0) okay = false; else offset += (size_t)count;
    }
    unsigned char extra;
    okay = okay && pread(fd, &extra, 1, (off_t)size) == 0 && fstat(fd, &after) == 0 && same(&before, &after);
    close(fd); return okay ? (int)size : -1;
}

bool ph_host_root_custody(const char *path) {
    int fd = ph_host_open(path, true);
    if (fd < 0) return false;
    close(fd); return true;
}
