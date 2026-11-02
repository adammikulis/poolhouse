"""Exercise the one protected requirement reader on isolated directories."""

import sys
from pathlib import Path

import pytest

from tests.test_mach_sender import _execute

SOURCE = r'''
#include "NativeSender.h"
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int swap_root = -1, swap_result = -1;
static int descriptors(void) {
    int count = 0;
    for (int fd = 0; fd < 256; fd++) if (fcntl(fd, F_GETFD) >= 0) count++;
    return count;
}
static void replace_generation(void) {
    swap_result = renameat(swap_root, "1", swap_root, "old") || renameat(swap_root, "replacement", swap_root, "1");
}
static int write_file(int dir, const char *name, const char *contents) {
    int fd = openat(dir, name, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (fd < 0) return -1;
    size_t n = strlen(contents); int result = write(fd, contents, n) == (ssize_t)n ? 0 : -1;
    if (close(fd)) result = -1;
    return result;
}
static bool read_generation(int root, uid_t owner, uint64_t generation, ph_protected_requirements *result) {
    ph_policy_files *files = NULL; uint8_t manifest[16384]; size_t length = 0;
    bool okay = ph_policy_files_fixture_begin(root, owner, &files, manifest, sizeof(manifest), &length)
        && ph_policy_files_requirements(files, generation, result) && ph_policy_files_validate(files);
    ph_policy_files_destroy(&files);
    return okay;
}
int main(int argc, char **argv) {
    if (argc != 2) return 1;
    int root = open(argv[1], O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    int authority = root < 0 ? -1 : openat(root, "Authority", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    int generations = authority < 0 ? -1 : openat(authority, "generations", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (generations < 0 || mkdirat(generations, "1", 0700)) return 2;
    uint8_t invalid_manifest[16384]; size_t invalid_length = 0;
    if (ph_policy_files_validate(NULL) || ph_policy_files_fixture_begin(root, getuid(), NULL,
        invalid_manifest, sizeof(invalid_manifest), &invalid_length)) return 32;
    ph_policy_files_destroy(NULL);
    int gen = openat(generations, "1", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (gen < 0 || write_file(gen, "helper.requirement", "helper") ||
        write_file(gen, "gui.requirement", "gui") || write_file(gen, "node.requirement", "node")) return 3;
    ph_protected_requirements result, sentinel;
    memset(&result, 0xa5, sizeof(result)); sentinel = result;
    if (!read_generation(root, getuid(), 1, &result) || result.length[0] != 6 ||
        result.length[1] != 3 || result.length[2] != 4 || memcmp(result.bytes[0], "helper", 6) ||
        memcmp(result.bytes[1], "gui", 3) || memcmp(result.bytes[2], "node", 4)) return 4;
    int before_descriptors = descriptors();
    for (unsigned i = 0; i < 32; i++) if (!read_generation(root, getuid(), 1, &result)) return 30;
    if (descriptors() != before_descriptors) return 31;
    ph_policy_files *held = NULL; uint8_t manifest[16384]; size_t manifest_length = 0;
    if (!ph_policy_files_fixture_begin(root, getuid(), &held, manifest, sizeof(manifest), &manifest_length) ||
        !ph_policy_files_requirements(held, 1, &result) || fchmodat(root, "Approval", 0775, 0) ||
        ph_policy_files_validate(held) || fchmodat(root, "Approval", 0755, 0) || ph_policy_files_validate(held)) return 33;
    ph_policy_files_destroy(&held);
    if (!ph_policy_files_fixture_begin(root, getuid(), &held, manifest, sizeof(manifest), &manifest_length) ||
        !ph_policy_files_requirements(held, 1, &result) || !ph_policy_files_validate(held)) return 25;
    int approval = openat(root, "Approval", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    int changed = approval < 0 ? -1 : openat(approval, "manifest.json", O_WRONLY | O_TRUNC | O_CLOEXEC);
    if (changed < 0 || write(changed, "changed!", 8) != 8 || close(changed) || ph_policy_files_validate(held)) return 26;
    ph_policy_files_destroy(&held);
    if (unlinkat(approval, "manifest.json", 0) || write_file(approval, "manifest.json", "manifest") ||
        fchmodat(approval, "manifest.json", 0644, 0)) return 27;
    if (!ph_policy_files_fixture_begin(root, getuid(), &held, manifest, sizeof(manifest), &manifest_length) ||
        renameat(root, "Approval", root, "old-approval") || mkdirat(root, "Approval", 0755) ||
        ph_policy_files_validate(held)) return 28;
    ph_policy_files_destroy(&held);
    if (unlinkat(root, "Approval", AT_REMOVEDIR) || renameat(root, "old-approval", root, "Approval") || close(approval)) return 29;
#define REFUSE() do { result = sentinel; if (read_generation(root, getuid(), 1, &result) || \
    memcmp(&result, &sentinel, sizeof(result))) return __LINE__; } while (0)
    if (fchmodat(gen, "helper.requirement", 0644, 0)) return 5;
    REFUSE();
    if (fchmodat(gen, "helper.requirement", 0600, 0) || linkat(gen, "helper.requirement", gen, "alias", 0)) return 6;
    REFUSE();
    if (unlinkat(gen, "alias", 0) || unlinkat(gen, "gui.requirement", 0) ||
        symlinkat("helper.requirement", gen, "gui.requirement")) return 7;
    REFUSE();
    if (unlinkat(gen, "gui.requirement", 0) || write_file(gen, "gui.requirement", "gui")) return 8;
    int file = openat(gen, "node.requirement", O_WRONLY | O_CLOEXEC);
    if (file < 0 || ftruncate(file, PH_REQUIREMENT_LIMIT + 1) || close(file)) return 9;
    REFUSE();
    if (unlinkat(gen, "node.requirement", 0) || write_file(gen, "node.requirement", "node")) return 10;
    if (unlinkat(gen, "gui.requirement", 0) || write_file(gen, "gui.requirement", "")) return 23;
    REFUSE();
    if (unlinkat(gen, "gui.requirement", 0) || write_file(gen, "gui.requirement", "gui")) return 24;
    file = openat(gen, "node.requirement", O_RDONLY | O_CLOEXEC);
    if (file < 0 || !ph_manifest_fixture_acl(file, false) ||
        !read_generation(root, getuid(), 1, &result) || !ph_manifest_fixture_acl(file, true)) return 14;
    REFUSE();
    if (close(file) || unlinkat(gen, "node.requirement", 0) || write_file(gen, "node.requirement", "node")) return 15;
    if (symlinkat("1", generations, "2") || read_generation(root, getuid(), 2, &result)) return 11;
    if (read_generation(root, getuid() ^ 1, 1, &result) || read_generation(root, getuid(), 0, &result)) return 12;
    if (mkdirat(generations, "replacement", 0700)) return 19;
    int replacement = openat(generations, "replacement", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (replacement < 0 || write_file(replacement, "helper.requirement", "other") ||
        write_file(replacement, "gui.requirement", "other") || write_file(replacement, "node.requirement", "other")) return 20;
    swap_root = generations; ph_policy_files_test_hook(replace_generation);
    REFUSE();
    ph_policy_files_test_hook(NULL);
    if (swap_result || renameat(generations, "1", generations, "replacement") || renameat(generations, "old", generations, "1")) return 21;
    if (unlinkat(replacement, "helper.requirement", 0) || unlinkat(replacement, "gui.requirement", 0) ||
        unlinkat(replacement, "node.requirement", 0) || close(replacement) || unlinkat(generations, "replacement", AT_REMOVEDIR)) return 22;
    const char *max_generation = "18446744073709551615";
    if (mkdirat(generations, max_generation, 0700)) return 16;
    int maxdir = openat(generations, max_generation, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (maxdir < 0 || write_file(maxdir, "helper.requirement", "helper") ||
        write_file(maxdir, "gui.requirement", "gui") || write_file(maxdir, "node.requirement", "node") ||
        !read_generation(root, getuid(), UINT64_MAX, &result)) return 17;
    if (unlinkat(maxdir, "helper.requirement", 0) || unlinkat(maxdir, "gui.requirement", 0) ||
        unlinkat(maxdir, "node.requirement", 0) || close(maxdir) || unlinkat(generations, max_generation, AT_REMOVEDIR)) return 18;
    if (unlinkat(generations, "2", 0) || unlinkat(gen, "helper.requirement", 0) || unlinkat(gen, "gui.requirement", 0) ||
        unlinkat(gen, "node.requirement", 0) || close(gen) || unlinkat(generations, "1", AT_REMOVEDIR) ||
        close(generations) || close(authority) || close(root)) return 13;
    puts("fixed protected requirements reject unsafe entries");
    return 0;
}
'''


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS filesystem ACLs")
def test_fixed_requirement_generation_reader(tmp_path):
    root = Path(__file__).resolve().parents[1]
    fixture, binary = tmp_path / "fixture.c", tmp_path / "fixture"
    fixture.write_text(SOURCE)
    generation_root = tmp_path / "policy"
    generation_root.mkdir(mode=0o755)
    approval = generation_root / "Approval"
    approval.mkdir(mode=0o755)
    (approval / "manifest.json").write_text("manifest")
    (approval / "manifest.json").chmod(0o644)
    authority = generation_root / "Authority"
    authority.mkdir(mode=0o700)
    (authority / "generations").mkdir(mode=0o700)
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)}
    compiler = "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang"
    sdk = "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"
    _execute([compiler, "-isysroot", sdk, "-fno-modules", "-fno-implicit-modules", "-std=c11", "-Wall", "-Wextra",
              "-Werror", "-DTESTING", "-I", str(root / "app/native-approval"), str(root / "app/native-approval/ManifestFD.c"),
              str(fixture), "-o", str(binary)], tmp_path, "compile", environment, (30, 1048576))
    out, err = _execute([str(binary), str(generation_root)], tmp_path, "run", environment, (15, 65536))
    assert out == "fixed protected requirements reject unsafe entries\n" and not err
