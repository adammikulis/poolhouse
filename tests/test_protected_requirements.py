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
int main(int argc, char **argv) {
    if (argc != 2) return 1;
    int root = open(argv[1], O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (root < 0 || mkdirat(root, "1", 0700)) return 2;
    int gen = openat(root, "1", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (gen < 0 || write_file(gen, "helper.requirement", "helper") ||
        write_file(gen, "gui.requirement", "gui") || write_file(gen, "node.requirement", "node")) return 3;
    ph_protected_requirements result, sentinel;
    memset(&result, 0xa5, sizeof(result)); sentinel = result;
    if (!ph_requirements_fixture(root, getuid(), 1, &result) || result.length[0] != 6 ||
        result.length[1] != 3 || result.length[2] != 4 || memcmp(result.bytes[0], "helper", 6) ||
        memcmp(result.bytes[1], "gui", 3) || memcmp(result.bytes[2], "node", 4)) return 4;
#define REFUSE() do { result = sentinel; if (ph_requirements_fixture(root, getuid(), 1, &result) || \
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
        !ph_requirements_fixture(root, getuid(), 1, &result) || !ph_manifest_fixture_acl(file, true)) return 14;
    REFUSE();
    if (close(file) || unlinkat(gen, "node.requirement", 0) || write_file(gen, "node.requirement", "node")) return 15;
    if (symlinkat("1", root, "2") || ph_requirements_fixture(root, getuid(), 2, &result)) return 11;
    if (ph_requirements_fixture(root, getuid() ^ 1, 1, &result) || ph_requirements_fixture(root, getuid(), 0, &result)) return 12;
    if (mkdirat(root, "replacement", 0700)) return 19;
    int replacement = openat(root, "replacement", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (replacement < 0 || write_file(replacement, "helper.requirement", "other") ||
        write_file(replacement, "gui.requirement", "other") || write_file(replacement, "node.requirement", "other")) return 20;
    swap_root = root; ph_requirements_test_hook(replace_generation);
    REFUSE();
    ph_requirements_test_hook(NULL);
    if (swap_result || renameat(root, "1", root, "replacement") || renameat(root, "old", root, "1")) return 21;
    if (unlinkat(replacement, "helper.requirement", 0) || unlinkat(replacement, "gui.requirement", 0) ||
        unlinkat(replacement, "node.requirement", 0) || close(replacement) || unlinkat(root, "replacement", AT_REMOVEDIR)) return 22;
    const char *max_generation = "18446744073709551615";
    if (mkdirat(root, max_generation, 0700)) return 16;
    int maxdir = openat(root, max_generation, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (maxdir < 0 || write_file(maxdir, "helper.requirement", "helper") ||
        write_file(maxdir, "gui.requirement", "gui") || write_file(maxdir, "node.requirement", "node") ||
        !ph_requirements_fixture(root, getuid(), UINT64_MAX, &result)) return 17;
    if (unlinkat(maxdir, "helper.requirement", 0) || unlinkat(maxdir, "gui.requirement", 0) ||
        unlinkat(maxdir, "node.requirement", 0) || close(maxdir) || unlinkat(root, max_generation, AT_REMOVEDIR)) return 18;
    if (unlinkat(root, "2", 0) || unlinkat(gen, "helper.requirement", 0) || unlinkat(gen, "gui.requirement", 0) ||
        unlinkat(gen, "node.requirement", 0) || close(gen) || unlinkat(root, "1", AT_REMOVEDIR) || close(root)) return 13;
    puts("fixed protected requirements reject unsafe entries");
    return 0;
}
'''


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS filesystem ACLs")
def test_fixed_requirement_generation_reader(tmp_path):
    root = Path(__file__).resolve().parents[1]
    fixture, binary = tmp_path / "fixture.c", tmp_path / "fixture"
    fixture.write_text(SOURCE)
    generation_root = tmp_path / "generations"
    generation_root.mkdir(mode=0o700)
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)}
    compiler = "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang"
    sdk = "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"
    _execute([compiler, "-isysroot", sdk, "-fno-modules", "-fno-implicit-modules", "-std=c11", "-Wall", "-Wextra",
              "-Werror", "-DTESTING", "-I", str(root / "app/native-approval"), str(root / "app/native-approval/ManifestFD.c"),
              str(fixture), "-o", str(binary)], tmp_path, "compile", environment, (30, 1048576))
    out, err = _execute([str(binary), str(generation_root)], tmp_path, "run", environment, (15, 65536))
    assert out == "fixed protected requirements reject unsafe entries\n" and not err
