"""Compare one real kernel sender with an explicitly supplied test policy."""

import sys
from pathlib import Path

import pytest

from tests.test_mach_sender import _execute

SOURCE = r'''
#include "NativeCaller.h"
#include <bsm/libbsm.h>
#include <mach/task_info.h>
#include <stdio.h>
#include <string.h>

struct frame { mach_msg_header_t header; unsigned char data[8]; };
int main(void) {
    SecCodeRef self = NULL; SecRequirementRef requirement = NULL, wrong = NULL;
    ph_native_caller_policy policy = {0};
    audit_token_t token = {0}; mach_msg_type_number_t words = TASK_AUDIT_TOKEN_COUNT;
    if (SecCodeCopySelf(kSecCSDefaultFlags, &self) != errSecSuccess ||
        SecCodeCopyDesignatedRequirement(self, kSecCSDefaultFlags, &requirement) != errSecSuccess ||
        !ph_native_identity_observe(self, &policy.identity) ||
        task_info(mach_task_self(), TASK_AUDIT_TOKEN, (task_info_t)&token, &words) != KERN_SUCCESS ||
        words != TASK_AUDIT_TOKEN_COUNT) return 1;
    policy.requirement = requirement; policy.euid = audit_token_to_euid(token);
    policy.auid = audit_token_to_auid(token); policy.asid = audit_token_to_asid(token);
    mach_port_t port = MACH_PORT_NULL;
    if (mach_port_allocate(mach_task_self(), MACH_PORT_RIGHT_RECEIVE, &port) != KERN_SUCCESS ||
        mach_port_insert_right(mach_task_self(), port, port, MACH_MSG_TYPE_MAKE_SEND) != KERN_SUCCESS) return 2;
    struct frame request = {0};
    request.header.msgh_bits = MACH_MSGH_BITS(MACH_MSG_TYPE_COPY_SEND, 0);
    request.header.msgh_size = sizeof(request); request.header.msgh_remote_port = port;
    memset(request.data, 0xff, sizeof(request.data));
    if (mach_msg(&request.header, MACH_SEND_MSG | MACH_SEND_TIMEOUT, sizeof(request), 0,
                 MACH_PORT_NULL, 1000, MACH_PORT_NULL) != MACH_MSG_SUCCESS) return 3;
    ph_mach_message *message = NULL;
    if (ph_mach_receive(port, 64, 1000, &message, NULL) != PH_MACH_OBSERVED) return 4;
#ifdef EXPECT_UNHARDENED
    if (ph_native_caller_matches(message, &policy)) return 16;
    ph_mach_message_destroy(&message);
    if (mach_port_deallocate(mach_task_self(), port) != KERN_SUCCESS ||
        mach_port_mod_refs(mach_task_self(), port, MACH_PORT_RIGHT_RECEIVE, -1) != KERN_SUCCESS) return 17;
    CFRelease(requirement); CFRelease(self);
    puts("unhardened sender refused");
    return 0;
#endif
    if (!ph_native_caller_matches(message, &policy)) return 4;
    ph_native_caller_policy changed = policy;
    changed.euid ^= 1; if (ph_native_caller_matches(message, &changed)) return 5;
    changed = policy; changed.auid ^= 1; if (ph_native_caller_matches(message, &changed)) return 6;
    changed = policy; changed.asid ^= 1; if (ph_native_caller_matches(message, &changed)) return 7;
    changed = policy; changed.identity.code_directory[0] ^= 1; if (ph_native_caller_matches(message, &changed)) return 8;
    changed = policy; changed.identity.requirement_digest[0] ^= 1; if (ph_native_caller_matches(message, &changed)) return 9;
    changed = policy; changed.identity.entitlements_digest[0] ^= 1; if (ph_native_caller_matches(message, &changed)) return 10;
    changed = policy; changed.identity.publisher_present = !changed.identity.publisher_present;
    if (ph_native_caller_matches(message, &changed)) return 11;
    changed = policy; changed.identity.signing_flags ^= kSecCodeSignatureRuntime;
    if (ph_native_caller_matches(message, &changed)) return 12;
    if (SecRequirementCreateWithString(CFSTR("identifier \"poolhouse.not-the-sender\""),
                                       kSecCSDefaultFlags, &wrong) != errSecSuccess) return 13;
    changed = policy; changed.requirement = wrong;
    if (ph_native_caller_matches(message, &changed)) return 14;
    ph_mach_message_destroy(&message);
    if (mach_port_deallocate(mach_task_self(), port) != KERN_SUCCESS ||
        mach_port_mod_refs(mach_task_self(), port, MACH_PORT_RIGHT_RECEIVE, -1) != KERN_SUCCESS) return 15;
    CFRelease(wrong); CFRelease(requirement); CFRelease(self);
    puts("actual sender matched test policy; changed bindings refused");
    return 0;
}
'''


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Security SDK")
def test_real_sender_and_explicit_policy_bindings(tmp_path):
    root = Path(__file__).resolve().parents[1]
    fixture, binary = tmp_path / "fixture.c", tmp_path / "fixture"
    fixture.write_text(SOURCE)
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)}
    compiler = "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang"
    sdk = "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"
    sources = [root / "app/native-host" / name for name in ("NativeCaller.c", "NativeIdentity.c", "MachCode.c", "MachSender.c")]
    _execute([compiler, "-isysroot", sdk, "-fno-modules", "-fno-implicit-modules", "-std=c11", "-Wall", "-Wextra",
              "-Werror", "-I", str(root / "app/native-host"), *(str(path) for path in sources), str(fixture),
              "-lbsm", "-framework", "Security", "-framework", "CoreFoundation", "-o", str(binary)],
             tmp_path, "compile", environment, (30, 1048576))
    _execute(["/usr/bin/codesign", "--force", "--sign", "-", "--options", "runtime", str(binary)],
             tmp_path, "adhoc-sign", environment, (30, 65536))
    out, err = _execute([str(binary)], tmp_path, "run", environment, (15, 65536))
    assert out == "actual sender matched test policy; changed bindings refused\n"
    assert not err
    unhardened = tmp_path / "unhardened"
    _execute([compiler, "-isysroot", sdk, "-fno-modules", "-fno-implicit-modules", "-std=c11", "-Wall", "-Wextra",
              "-Werror", "-DEXPECT_UNHARDENED", "-I", str(root / "app/native-host"), *(str(path) for path in sources),
              str(fixture), "-lbsm", "-framework", "Security", "-framework", "CoreFoundation", "-o", str(unhardened)],
             tmp_path, "compile-unhardened", environment, (30, 1048576))
    no_out, no_err = _execute([str(unhardened)], tmp_path, "run-unhardened", environment, (15, 65536))
    assert no_out == "unhardened sender refused\n" and not no_err
