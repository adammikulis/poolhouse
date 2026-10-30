"""The static code predicate compares exact OS observations and requirement."""
import sys
from pathlib import Path

import pytest

from tests.test_mach_sender import _execute

SOURCE = r'''
#include "NativeStaticImage.h"
#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>

int main(void) {
    CFURLRef path=CFURLCreateWithFileSystemPath(NULL,CFSTR("/usr/bin/ssh"),kCFURLPOSIXPathStyle,false);
    SecStaticCodeRef code=NULL; SecRequirementRef requirement=NULL,wrong=NULL;
    ph_native_identity_observation expected={0};
    if (!path || SecStaticCodeCreateWithPath(path,kSecCSDefaultFlags,&code)!=errSecSuccess || !code ||
        SecCodeCopyDesignatedRequirement(code,kSecCSDefaultFlags,&requirement)!=errSecSuccess || !requirement ||
        !ph_native_identity_observe(code,&expected) || !ph_native_static_image_matches(code,requirement,&expected)) return 1;
    if (ph_native_static_image_matches(NULL,requirement,&expected) || ph_native_static_image_matches(code,NULL,&expected) ||
        ph_native_static_image_matches(code,requirement,NULL)) return 2;
    ph_native_identity_observation changed=expected;
    changed.code_directory[0]^=1; if (ph_native_static_image_matches(code,requirement,&changed)) return 3;
    changed=expected; changed.requirement_digest[0]^=1; if (ph_native_static_image_matches(code,requirement,&changed)) return 4;
    changed=expected; changed.entitlements_digest[0]^=1; if (ph_native_static_image_matches(code,requirement,&changed)) return 5;
    changed=expected; changed.publisher_digest[0]^=1; if (ph_native_static_image_matches(code,requirement,&changed)) return 6;
    changed=expected; changed.publisher_present=false; if (ph_native_static_image_matches(code,requirement,&changed)) return 7;
    changed=expected; changed.signing_flags|=kSecCodeSignatureAdhoc; if (ph_native_static_image_matches(code,requirement,&changed)) return 8;
    changed=expected; changed.signing_flags&=~kSecCodeSignatureRuntime; if (ph_native_static_image_matches(code,requirement,&changed)) return 9;
    if (SecRequirementCreateWithString(CFSTR("identifier \"not.the.installed.image\""),kSecCSDefaultFlags,&wrong)!=errSecSuccess ||
        ph_native_static_image_matches(code,wrong,&expected)) return 10;
    SecCodeRef self=NULL; SecRequirementRef self_requirement=NULL; ph_native_identity_observation local={0};
    if (SecCodeCopySelf(kSecCSDefaultFlags,&self)!=errSecSuccess || !self ||
        SecCodeCopyDesignatedRequirement(self,kSecCSDefaultFlags,&self_requirement)!=errSecSuccess || !self_requirement ||
        !ph_native_identity_observe(self,&local) || local.publisher_present ||
        ph_native_static_image_matches(self,self_requirement,&local)) return 11;
    CFRelease(self_requirement); CFRelease(self);
    CFRelease(wrong); CFRelease(requirement); CFRelease(code); CFRelease(path);
    return 0;
}
'''


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Security SDK")
def test_static_image_matches_exact_os_identity(tmp_path):
    root = Path(__file__).resolve().parents[1]
    fixture, binary = tmp_path / "fixture.c", tmp_path / "fixture"
    fixture.write_text(SOURCE)
    env = {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)}
    compiler = "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang"
    sdk = "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"
    host = root / "app/native-host"
    _execute([compiler, "-isysroot", sdk, "-fno-modules", "-fno-implicit-modules", "-std=c11", "-Wall", "-Wextra",
              "-Werror", "-I", str(host), str(host / "NativeIdentity.c"), str(host / "NativeStaticImage.c"),
              str(fixture), "-framework", "Security", "-framework", "CoreFoundation", "-o", str(binary)],
             tmp_path, "compile", env, (30, 1048576))
    out, err = _execute([str(binary)], tmp_path, "run", env, (30, 65536))
    assert not out and not err
