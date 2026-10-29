"""Decode the three fixed requirement roles without promoting data to authority."""

import sys
from pathlib import Path

import pytest

from tests.test_mach_sender import _execute

SOURCE = r'''
#include "PolicyRequirements.h"
#include <CommonCrypto/CommonDigest.h>
#include <stdio.h>
#include <string.h>

static bool prepare(ph_protected_requirements *raw, uint8_t expected[3][32]) {
    const CFStringRef rules[] = {CFSTR("identifier \"test.helper\""), CFSTR("identifier \"test.gui\""),
                                 CFSTR("identifier \"test.node\"")};
    static const char domain[] = "poolhouse/native-code/requirement/v1";
    for (unsigned i = 0; i < 3; i++) {
        SecRequirementRef requirement = NULL; CFDataRef bytes = NULL;
        if (SecRequirementCreateWithString(rules[i], kSecCSDefaultFlags, &requirement) != errSecSuccess ||
            SecRequirementCopyData(requirement, kSecCSDefaultFlags, &bytes) != errSecSuccess || !bytes) return false;
        CFIndex n = CFDataGetLength(bytes);
        if (n <= 0 || n > PH_REQUIREMENT_LIMIT) return false;
        raw->length[i] = (size_t)n; memcpy(raw->bytes[i], CFDataGetBytePtr(bytes), (size_t)n);
        uint8_t present = 1, length[8];
        for (unsigned j = 0; j < 8; j++) length[7-j] = (uint8_t)((uint64_t)n >> (j * 8));
        CC_SHA256_CTX hash;
        if (!CC_SHA256_Init(&hash) || !CC_SHA256_Update(&hash, domain, sizeof(domain)) ||
            !CC_SHA256_Update(&hash, &present, 1) || !CC_SHA256_Update(&hash, length, sizeof(length)) ||
            !CC_SHA256_Update(&hash, CFDataGetBytePtr(bytes), (CC_LONG)n) || !CC_SHA256_Final(expected[i], &hash)) return false;
        CFRelease(bytes); CFRelease(requirement);
    }
    return true;
}
int main(void) {
    ph_protected_requirements raw = {0}, changed; uint8_t expected[3][32] = {{0}};
    SecRequirementRef result[3] = {NULL, NULL, NULL};
    if (!prepare(&raw, expected) || !ph_policy_requirements_test_decode(&raw, expected, result)) return 1;
    SecRequirementRef first = result[0];
    if (ph_policy_requirements_test_decode(&raw, expected, result) || result[0] != first) return 8;
    for (unsigned i = 0; i < 3; i++) { if (!result[i]) return 2; CFRelease(result[i]); result[i] = NULL; }
    changed = raw; changed.bytes[1][0] ^= 1;
    if (ph_policy_requirements_test_decode(&changed, expected, result) || result[0] || result[1] || result[2]) return 3;
    changed = raw; changed.length[2]--;
    if (ph_policy_requirements_test_decode(&changed, expected, result) || result[0] || result[1] || result[2]) return 4;
    changed = raw; memcpy(changed.bytes[0], raw.bytes[1], raw.length[1]); changed.length[0] = raw.length[1];
    if (ph_policy_requirements_test_decode(&changed, expected, result) || result[0] || result[1] || result[2]) return 5;
    changed = raw; changed.length[1] = PH_REQUIREMENT_LIMIT + 1;
    if (ph_policy_requirements_test_decode(&changed, expected, result) || result[0] || result[1] || result[2]) return 6;
    expected[2][0] ^= 1;
    if (ph_policy_requirements_test_decode(&raw, expected, result) || result[0] || result[1] || result[2]) return 7;
    puts("three fixed requirements decode atomically");
    return 0;
}
'''


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Security SDK")
def test_policy_requirement_decoding_is_all_or_nothing(tmp_path):
    root = Path(__file__).resolve().parents[1]
    fixture, binary = tmp_path / "fixture.c", tmp_path / "fixture"
    fixture.write_text(SOURCE)
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)}
    compiler = "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang"
    sdk = "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"
    sources = [root / "app/native-host/PolicyRequirements.c", root / "app/native-host/NativeIdentity.c",
               root / "app/native-approval/ManifestFD.c"]
    _execute([compiler, "-isysroot", sdk, "-fno-modules", "-fno-implicit-modules", "-std=c11", "-Wall", "-Wextra",
              "-Werror", "-DTESTING", "-I", str(root / "app/native-host"), *(str(path) for path in sources),
              str(fixture), "-framework", "Security", "-framework", "CoreFoundation", "-o", str(binary)],
             tmp_path, "compile", environment, (30, 1048576))
    out, err = _execute([str(binary)], tmp_path, "run", environment, (15, 65536))
    assert out == "three fixed requirements decode atomically\n" and not err
    production = tmp_path / "production.o"
    _execute([compiler, "-isysroot", sdk, "-fno-modules", "-fno-implicit-modules", "-std=c11", "-Wall", "-Wextra",
              "-Werror", "-I", str(root / "app/native-host"), "-c", str(sources[0]), "-o", str(production)],
             tmp_path, "compile-production", environment, (30, 1048576))
    nm = "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/nm"
    symbols, _ = _execute([nm, "-g", str(production)], tmp_path, "symbols", environment, (15, 65536))
    assert "ph_policy_requirements_test_decode" not in symbols
