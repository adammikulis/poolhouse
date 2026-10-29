"""Capture one protected policy candidate without treating it as authority."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import testnode

from tests.test_mach_sender import _execute

ROOT = Path(__file__).resolve().parents[1]
SOURCE = r'''
#include "NativePolicy.h"
#include <CommonCrypto/CommonDigest.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int manifest_dir = -1;
static void change_manifest(void) {
    int fd = openat(manifest_dir, "manifest.json", O_WRONLY | O_TRUNC | O_CLOEXEC);
    if (fd >= 0) { (void)write(fd, "{}", 2); close(fd); }
}
static int write_file(int dir, const char *name, const void *bytes, size_t count, mode_t mode) {
    int fd = openat(dir, name, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, mode);
    if (fd < 0) return -1;
    int result = write(fd, bytes, count) == (ssize_t)count ? 0 : -1;
    if (close(fd)) result = -1;
    return result;
}
static void hex(const uint8_t *bytes, size_t n, char *out) {
    const char alphabet[] = "0123456789abcdef";
    for (size_t i = 0; i < n; i++) { out[i*2] = alphabet[bytes[i] >> 4]; out[i*2+1] = alphabet[bytes[i] & 15]; }
    out[n*2] = 0;
}
int main(int argc, char **argv) {
    if (argc != 2) return 1;
    int root = open(argv[1], O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    manifest_dir = root < 0 ? -1 : openat(root, "Approval", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    int authority = root < 0 ? -1 : openat(root, "Authority", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    int generations = authority < 0 ? -1 : openat(authority, "generations", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (manifest_dir < 0 || generations < 0 || mkdirat(generations, "1", 0700)) return 2;
    int gen = openat(generations, "1", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    SecRequirementRef requirement = NULL; CFDataRef bytes = NULL;
    if (gen < 0 || SecRequirementCreateWithString(CFSTR("identifier \"policy.fixture\""),
            kSecCSDefaultFlags, &requirement) != errSecSuccess ||
        SecRequirementCopyData(requirement, kSecCSDefaultFlags, &bytes) != errSecSuccess || !bytes) return 3;
    size_t n = (size_t)CFDataGetLength(bytes); const UInt8 *data = CFDataGetBytePtr(bytes);
    if (!n || n > PH_REQUIREMENT_LIMIT || write_file(gen, "helper.requirement", data, n, 0600) ||
        write_file(gen, "gui.requirement", data, n, 0600) || write_file(gen, "node.requirement", data, n, 0600)) return 4;
    static const char req_domain[] = "poolhouse/native-code/requirement/v1";
    static const char policy_domain[] = "poolhouse.native-authority.code-policy.v2\0app.poolhouse.authority";
    uint8_t size[8], present = 1, req_hash[32], policy_hash[32], cd[20], sha[32], pub[32], ent[32], purpose[32], rotate[32];
    for (unsigned i = 0; i < 8; i++) size[7-i] = (uint8_t)((uint64_t)n >> (i*8));
    memset(cd, 0x11, 20); memset(sha, 0x22, 32); memset(pub, 0x99, 32); memset(ent, 0x66, 32);
    memset(purpose, 0x77, 32); memset(rotate, 0x88, 32);
    CC_SHA256_CTX hash;
    if (!CC_SHA256_Init(&hash) || !CC_SHA256_Update(&hash, req_domain, sizeof(req_domain)) ||
        !CC_SHA256_Update(&hash, &present, 1) || !CC_SHA256_Update(&hash, size, 8) ||
        !CC_SHA256_Update(&hash, data, (CC_LONG)n) || !CC_SHA256_Final(req_hash, &hash)) return 5;
    if (!CC_SHA256_Init(&hash) || !CC_SHA256_Update(&hash, policy_domain, sizeof(policy_domain))) return 6;
    for (unsigned i = 0; i < 3; i++) if (!CC_SHA256_Update(&hash, cd, 20) || !CC_SHA256_Update(&hash, sha, 32) ||
        !CC_SHA256_Update(&hash, &present, 1) || !CC_SHA256_Update(&hash, pub, 32) ||
        !CC_SHA256_Update(&hash, req_hash, 32) || !CC_SHA256_Update(&hash, ent, 32)) return 7;
    if (!CC_SHA256_Update(&hash, purpose, 32) || !CC_SHA256_Update(&hash, rotate, 32) ||
        !CC_SHA256_Final(policy_hash, &hash)) return 8;
    char hcd[41], hsha[65], hpub[65], hent[65], hreq[65], hp[65], hr[65], hpolicy[65], pin[700], manifest[4096];
    hex(cd, 20, hcd); hex(sha, 32, hsha); hex(pub, 32, hpub); hex(ent, 32, hent); hex(req_hash, 32, hreq);
    hex(purpose, 32, hp); hex(rotate, 32, hr); hex(policy_hash, 32, hpolicy);
    int used = snprintf(pin, sizeof(pin), "\"cdhash\":\"%s\",\"sha256\":\"%s\",\"publisher_kind\":1,"
        "\"publisher_digest\":\"%s\",\"requirement_digest\":\"%s\",\"entitlements_digest\":\"%s\"",
        hcd, hsha, hpub, hreq, hent);
    if (used <= 0 || (size_t)used >= sizeof(pin)) return 9;
    used = snprintf(manifest, sizeof(manifest), "{\"version\":2,\"owner_uid\":501,\"authority_id\":\"%s\","
        "\"installation_id\":\"%s\",\"generation\":1,\"receipt_algorithm\":\"ed25519\","
        "\"receipt_public_key\":\"%s\",\"policy_digest\":\"%s\",\"purpose_policy\":\"%s\","
        "\"rotation_policy\":\"%s\",\"helper\":{\"service\":\"app.poolhouse.authority\",%s},"
        "\"gui\":{%s},\"node\":{%s}}", hsha, hpub, hent, hpolicy, hp, hr, pin, pin, pin);
    if (used <= 0 || (size_t)used >= sizeof(manifest) || write_file(manifest_dir, "manifest.json", manifest, (size_t)used, 0644)) return 10;
    ph_native_policy_candidate *candidate = NULL;
    if (!ph_native_policy_fixture(root, getuid(), &candidate) || !candidate || !ph_native_policy_validate(candidate) ||
        !ph_native_policy_manifest(candidate) || ph_native_policy_manifest(candidate)->generation != 1 ||
        !ph_native_policy_requirement(candidate, PH_POLICY_HELPER) || !ph_native_policy_requirement(candidate, PH_POLICY_GUI) ||
        !ph_native_policy_requirement(candidate, PH_POLICY_NODE) || ph_native_policy_requirement(candidate, 3) ||
        ph_native_policy_fixture(root, getuid(), &candidate)) return 11;
    if (fchmodat(root, "Approval", 0775, 0) || ph_native_policy_validate(candidate) ||
        fchmodat(root, "Approval", 0755, 0) || ph_native_policy_validate(candidate)) return 16;
    ph_native_policy_destroy(&candidate);
    if (!ph_native_policy_fixture(root, getuid(), &candidate)) return 17;
    if (write_file(gen, "node.requirement", "bad", 3, 0600) || ph_native_policy_validate(candidate) ||
        write_file(gen, "node.requirement", data, n, 0600) || ph_native_policy_validate(candidate)) return 14;
    ph_native_policy_destroy(&candidate); ph_native_policy_destroy(&candidate);
    if (ph_native_policy_fixture(root, getuid() ^ 1, &candidate) || candidate) return 15;
    ph_policy_files_test_hook(change_manifest);
    if (candidate || ph_native_policy_fixture(root, getuid(), &candidate) || candidate) return 12;
    ph_policy_files_test_hook(NULL);
    if (write_file(manifest_dir, "manifest.json", manifest, (size_t)used, 0644) ||
        write_file(gen, "node.requirement", "bad", 3, 0600) ||
        ph_native_policy_fixture(root, getuid(), &candidate) || candidate) return 13;
    CFRelease(bytes); CFRelease(requirement); close(gen); close(generations); close(authority); close(manifest_dir); close(root);
    puts("captured policy candidate rejects replacement and mismatch");
    return 0;
}
'''


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Security SDK")
@pytest.mark.timeout(600)
def test_candidate_capture_binds_one_manifest_and_requirement_set(tmp_path):
    build = testnode.shared_build(ROOT / "app")
    environment = {**os.environ, "HOME": str(tmp_path), "TMPDIR": str(tmp_path),
                   "RUSTUP_HOME": str(testnode.account() / ".rustup"), "CARGO_HOME": str(testnode.account() / ".cargo"),
                   "CARGO_TARGET_DIR": str(build.target), "CARGO_BUILD_JOBS": "1"}
    result = subprocess.run([testnode.tool("cargo"), "build", "--offline", "--locked", "-p", "poolhouse-manifest", "--lib"],
                            cwd=build.staged, env=environment, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    policy = tmp_path / "policy"
    policy.mkdir(mode=0o755)
    (policy / "Approval").mkdir(mode=0o755)
    authority = policy / "Authority"
    authority.mkdir(mode=0o700)
    (authority / "generations").mkdir(mode=0o700)
    fixture, binary = tmp_path / "fixture.c", tmp_path / "fixture"
    fixture.write_text(SOURCE)
    compiler = "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang"
    sdk = "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"
    sources = [ROOT / "app/native-host/NativePolicy.c", ROOT / "app/native-host/PolicyRequirements.c",
               ROOT / "app/native-host/NativeIdentity.c", ROOT / "app/native-approval/ManifestFD.c"]
    _execute([compiler, "-isysroot", sdk, "-fno-modules", "-fno-implicit-modules", "-std=c11", "-Wall", "-Wextra",
              "-Werror", "-DTESTING", "-I", str(ROOT / "app/native-host"), *(str(path) for path in sources),
              str(fixture), str(build.target / "debug/libpoolhouse_manifest.a"), "-framework", "Security",
              "-framework", "CoreFoundation", "-lresolv", "-o", str(binary)],
             tmp_path, "compile", environment, (30, 32 * 1024 * 1024))
    out, err = _execute([str(binary), str(policy)], tmp_path, "run", environment, (15, 65536))
    assert out == "captured policy candidate rejects replacement and mismatch\n" and not err
