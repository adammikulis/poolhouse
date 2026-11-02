"""OS-returned code identity bytes have one bounded, domain-separated encoding."""

import hashlib
import sys
from pathlib import Path

import pytest

from tests.test_mach_sender import _execute

SOURCE = r'''
#include "NativeIdentity.h"
#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>
#include <mach-o/loader.h>
#include <mach/machine.h>
#include <stdio.h>
#include <string.h>

static void show(const unsigned char *bytes, size_t size) {
    for (size_t i = 0; i < size; i++) printf("%02x", bytes[i]);
    putchar('\n');
}
int main(void) {
#ifdef TESTING
    FILE *temporary = tmpfile();
    struct mach_header_64 header = {0};
    header.magic=MH_MAGIC_64; header.cputype=CPU_TYPE_ARM64; header.cpusubtype=CPU_SUBTYPE_ARM64_ALL; header.filetype=MH_EXECUTE;
    if (!temporary || fwrite(&header,1,sizeof(header),temporary)!=sizeof(header) || fflush(temporary) ||
        ph_native_thin_arm64_fd(-1)) return 17;
#if defined(__arm64__)
    if (!ph_native_thin_arm64_fd(fileno(temporary))) return 17;
#else
    if (ph_native_thin_arm64_fd(fileno(temporary))) return 17;
#endif
    header.magic=0xcafebabe;
    if (fseek(temporary,0,SEEK_SET) || fwrite(&header,1,sizeof(header),temporary)!=sizeof(header) || fflush(temporary) ||
        ph_native_thin_arm64_fd(fileno(temporary))) return 18;
    fclose(temporary);
    uint8_t absent[32], empty_hash[32];
    CFDataRef empty = CFDataCreate(NULL, NULL, 0);
    if (!empty || !ph_native_identity_test_digest(NULL, false, absent) ||
        !ph_native_identity_test_digest(empty, true, empty_hash) || !memcmp(absent, empty_hash, 32) ||
        ph_native_identity_test_digest((CFDataRef)CFSTR("bad"), true, empty_hash)) return 8;
    CFRelease(empty);
    CFMutableDictionaryRef malformed = CFDictionaryCreateMutable(NULL, 0, &kCFTypeDictionaryKeyCallBacks,
                                                                  &kCFTypeDictionaryValueCallBacks);
    ph_native_identity_observation synthetic = {0}; int32_t flags = 0;
    CFNumberRef number = CFNumberCreate(NULL, kCFNumberSInt32Type, &flags);
    CFDictionarySetValue(malformed, kSecCodeInfoFlags, number); CFRelease(number);
    if (ph_native_identity_test_publisher(malformed, &synthetic)) return 9;
    flags = kSecCodeSignatureAdhoc; number = CFNumberCreate(NULL, kCFNumberSInt32Type, &flags);
    CFDictionarySetValue(malformed, kSecCodeInfoFlags, number); CFRelease(number);
    if (!ph_native_identity_test_publisher(malformed, &synthetic) || synthetic.publisher_present) return 10;
    CFArrayRef invalid_chain = CFArrayCreate(NULL, NULL, 0, &kCFTypeArrayCallBacks);
    CFDictionarySetValue(malformed, kSecCodeInfoCertificates, invalid_chain); CFRelease(invalid_chain);
    if (ph_native_identity_test_publisher(malformed, &synthetic)) return 11;
    CFRelease(malformed);
#endif
    SecCodeRef code = NULL; SecRequirementRef requirement = NULL; CFDataRef bytes = NULL;
    CFDictionaryRef info = NULL; ph_native_identity_observation observed = {0};
    ph_native_identity_observation untouched; memset(&untouched, 0xa5, sizeof(untouched));
    observed = untouched;
    if (ph_native_identity_observe(NULL, &observed) || memcmp(&observed, &untouched, sizeof(observed))) return 3;
    if (SecCodeCopySelf(kSecCSDefaultFlags, &code) != errSecSuccess || !code ||
        !ph_native_identity_observe(code, &observed) ||
        SecCodeCopyDesignatedRequirement(code, kSecCSDefaultFlags, &requirement) != errSecSuccess ||
        SecRequirementCopyData(requirement, kSecCSDefaultFlags, &bytes) != errSecSuccess ||
        SecCodeCopySigningInformation(code, kSecCSSigningInformation, &info) != errSecSuccess) return 1;
    SecRequirementRef decoded = NULL;
    if (!ph_native_requirement_decode(bytes, observed.requirement_digest, &decoded) || !decoded ||
        ph_native_requirement_decode(bytes, observed.requirement_digest, &decoded)) return 13;
    CFRelease(decoded); decoded = NULL;
    uint8_t wrong_digest[32]; memcpy(wrong_digest, observed.requirement_digest, sizeof(wrong_digest));
    wrong_digest[0] ^= 1;
    if (ph_native_requirement_decode(bytes, wrong_digest, &decoded) || decoded) return 14;
    memset(wrong_digest, 0, sizeof(wrong_digest));
    if (ph_native_requirement_decode(bytes, wrong_digest, &decoded) || decoded) return 16;
    static const uint8_t invalid_digest[32] = {172,152,254,179,240,93,158,212,84,8,38,167,17,74,156,186,
                                                203,234,63,45,33,6,169,30,112,84,122,97,213,124,19,155};
    CFDataRef invalid = CFDataCreate(NULL, (const UInt8 *)"bad", 3);
    if (!invalid || ph_native_requirement_decode(invalid, invalid_digest, &decoded) || decoded) return 15;
    CFRelease(invalid);
    CFDataRef unique = (CFDataRef)CFDictionaryGetValue(info, kSecCodeInfoUnique);
    if (!unique || CFDataGetLength(unique) != 20 || memcmp(CFDataGetBytePtr(unique), observed.code_directory, 20)) return 12;
    show(CFDataGetBytePtr(bytes), (size_t)CFDataGetLength(bytes));
    show(observed.requirement_digest, sizeof(observed.requirement_digest));
    CFDataRef entitlements = (CFDataRef)CFDictionaryGetValue(info, kSecCodeInfoEntitlements);
    if (!!entitlements != observed.entitlements_present) return 2;
    printf("%d\n", observed.entitlements_present);
    if (entitlements) show(CFDataGetBytePtr(entitlements), (size_t)CFDataGetLength(entitlements)); else puts("");
    show(observed.entitlements_digest, sizeof(observed.entitlements_digest));
    printf("%d\n", observed.publisher_present);
    CFRelease(info); CFRelease(bytes); CFRelease(requirement); CFRelease(code);
    CFURLRef path = CFURLCreateWithFileSystemPath(NULL, CFSTR("/usr/bin/true"), kCFURLPOSIXPathStyle, false);
    SecStaticCodeRef system = NULL; CFDictionaryRef system_info = NULL;
    if (!path || SecStaticCodeCreateWithPath(path, kSecCSDefaultFlags, &system) != errSecSuccess ||
        !ph_native_identity_observe(system, &observed) || !observed.publisher_present ||
        SecCodeCopySigningInformation(system, kSecCSSigningInformation, &system_info) != errSecSuccess) return 4;
    CFArrayRef chain = (CFArrayRef)CFDictionaryGetValue(system_info, kSecCodeInfoCertificates);
    if (!chain || CFArrayGetCount(chain) < 1) return 5;
    SecKeyRef key = SecCertificateCopyKey((SecCertificateRef)CFArrayGetValueAtIndex(chain, 0));
    CFDictionaryRef attributes = key ? SecKeyCopyAttributes(key) : NULL;
    CFDataRef pub = key ? SecKeyCopyExternalRepresentation(key, NULL) : NULL;
    if (!attributes || !pub) return 6;
    CFTypeRef type = CFDictionaryGetValue(attributes, kSecAttrKeyType);
    if (CFEqual(type, kSecAttrKeyTypeRSA)) puts("rsa");
    else if (CFEqual(type, kSecAttrKeyTypeECSECPrimeRandom)) puts("ec");
    else return 7;
    show(CFDataGetBytePtr(pub), (size_t)CFDataGetLength(pub));
    show(observed.publisher_digest, sizeof(observed.publisher_digest));
    CFRelease(pub); CFRelease(attributes); CFRelease(key); CFRelease(system_info); CFRelease(system); CFRelease(path);
    return 0;
}
'''


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Security SDK")
def test_identity_encoding_uses_exact_os_observations(tmp_path):
    root = Path(__file__).resolve().parents[1]
    fixture, binary = tmp_path / "fixture.c", tmp_path / "fixture"
    fixture.write_text(SOURCE)
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)}
    compiler = "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang"
    sdk = "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"
    _execute([compiler, "-isysroot", sdk, "-fno-modules", "-fno-implicit-modules", "-std=c11", "-Wall", "-Wextra",
              "-Werror", "-DTESTING", "-I", str(root / "app/native-host"), str(root / "app/native-host/NativeIdentity.c"),
              str(fixture), "-framework", "Security", "-framework", "CoreFoundation", "-o", str(binary)],
             tmp_path, "compile", environment, (30, 1048576))
    out, err = _execute([str(binary)], tmp_path, "run", environment, (15, 65536))
    production = tmp_path / "production"
    _execute([compiler, "-isysroot", sdk, "-fno-modules", "-fno-implicit-modules", "-std=c11", "-Wall", "-Wextra",
              "-Werror", "-I", str(root / "app/native-host"), str(root / "app/native-host/NativeIdentity.c"),
              str(fixture), "-framework", "Security", "-framework", "CoreFoundation", "-o", str(production)],
             tmp_path, "compile-production", environment, (30, 1048576))
    production_out, production_err = _execute([str(production)], tmp_path, "run-production", environment, (15, 65536))
    assert not production_err
    def expected(domain, tag, blob):
        return hashlib.sha256(domain + bytes([tag]) + len(blob).to_bytes(8, "big") + blob).hexdigest()
    assert not err
    for result in (out, production_out):
        requirement, req_digest, present, entitlements, ent_digest, publisher, key_type, key, key_digest = result.splitlines()
        assert present in {"0", "1"} and publisher == "0"  # The test executables are ad-hoc signed.
        assert req_digest == expected(b"poolhouse/native-code/requirement/v1\0", 1, bytes.fromhex(requirement))
        assert ent_digest == expected(b"poolhouse/native-code/entitlements/v1\0", int(present), bytes.fromhex(entitlements))
        assert key_type in {"rsa", "ec"}
        assert key_digest == expected(f"poolhouse/native-code/publisher-{key_type}/v1\0".encode(), 1, bytes.fromhex(key))
