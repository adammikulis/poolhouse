"""Observe private bidirectional XPC remotes and their kernel-backed message identity."""

import sys
from pathlib import Path

import pytest

from tests.test_mach_sender import _execute

SOURCE = r'''
#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>
#include "NativeCaller.h"
#include <bsm/libbsm.h>
#include <mach/task_info.h>
#include <dispatch/dispatch.h>
#include <xpc/xpc.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int valid_message(xpc_object_t message, xpc_connection_t expected, xpc_connection_t wrong, const ph_native_xpc_caller_policy *policy) {
    if (xpc_get_type(message) != XPC_TYPE_DICTIONARY || xpc_dictionary_get_remote_connection(message) != expected) return 0;
    SecCodeRef sender = NULL;
    if (SecCodeCreateWithXPCMessage(message, kSecCSDefaultFlags, &sender) || !sender) return 0;
    int valid = SecCodeCheckValidity(sender, kSecCSDefaultFlags, policy->requirement) == errSecSuccess &&
        ph_native_xpc_caller_matches(message,expected,policy);
    CFRelease(sender);
    ph_native_xpc_caller_policy changed = *policy;
    changed.euid ^= 1;
    if (ph_native_xpc_caller_matches(message,expected,&changed)) return 0;
    changed = *policy; changed.asid ^= 1;
    if (ph_native_xpc_caller_matches(message,expected,&changed)) return 0;
    changed = *policy; changed.identity.code_directory[0] ^= 1;
    if (ph_native_xpc_caller_matches(message,expected,&changed)) return 0;
    if (ph_native_xpc_caller_matches(message,wrong,policy)) return 0;
    return valid;
}
int main(int argc, char **argv) {
    int reject = argc == 2 && !strcmp(argv[1],"--reject");
    if (argc != 1 && !reject) return 12;
    SecCodeRef self = NULL; SecRequirementRef requirement = NULL; CFStringRef text = NULL;
    if (SecCodeCopySelf(kSecCSDefaultFlags, &self) || !self ||
        SecCodeCopyDesignatedRequirement(self, kSecCSDefaultFlags, &requirement) || !requirement ||
        SecRequirementCopyString(requirement, kSecCSDefaultFlags, &text) || !text) return 1;
    ph_native_xpc_caller_policy policy = {0};
    audit_token_t token = {0}; mach_msg_type_number_t words = TASK_AUDIT_TOKEN_COUNT;
    if (!ph_native_identity_observe(self,&policy.identity) ||
        task_info(mach_task_self(),TASK_AUDIT_TOKEN,(task_info_t)&token,&words) != KERN_SUCCESS ||
        words != TASK_AUDIT_TOKEN_COUNT) return 14;
    policy.requirement = requirement; policy.euid = audit_token_to_euid(token); policy.asid = audit_token_to_asid(token);
    const ph_native_xpc_caller_policy *policy_ptr = &policy;
    char rule[16384];
    if (!CFStringGetCString(text,rule,sizeof(rule),kCFStringEncodingUTF8)) return 2;
    const char *rule_ptr = rule;
    dispatch_queue_t queue = dispatch_queue_create("private.xpc.fixture", DISPATCH_QUEUE_CONCURRENT);
    dispatch_semaphore_t finished = dispatch_semaphore_create(0);
    __block int result = 3;
    xpc_connection_t listener = xpc_connection_create(NULL, queue);
    if (!listener || xpc_connection_set_peer_code_signing_requirement(listener,rule)) return 4;
    xpc_connection_set_event_handler(listener, ^(xpc_object_t event) {
        if (xpc_get_type(event) != XPC_TYPE_CONNECTION) return;
        xpc_connection_t remote = (xpc_connection_t)event;
        if (xpc_connection_set_peer_code_signing_requirement(remote,rule_ptr)) { result = 5; dispatch_semaphore_signal(finished); return; }
        xpc_connection_set_event_handler(remote, ^(xpc_object_t message) {
            if (!valid_message(message,remote,listener,policy_ptr)) { result = 6; dispatch_semaphore_signal(finished); return; }
            const char *value = xpc_dictionary_get_string(message,"kind");
            if (!value || strcmp(value,"request")) { result = 7; dispatch_semaphore_signal(finished); return; }
            xpc_object_t response = xpc_dictionary_create(NULL,NULL,0);
            xpc_dictionary_set_string(response,"kind","response");
            xpc_connection_send_message(remote,response);
            xpc_release(response);
        });
        xpc_connection_activate(remote);
    });
    xpc_connection_activate(listener);
    xpc_endpoint_t endpoint = xpc_endpoint_create(listener);
    xpc_connection_t client = xpc_connection_create_from_endpoint(endpoint);
    if (!endpoint || !client || xpc_connection_set_peer_code_signing_requirement(client,
        reject ? "identifier \"invalid.poolhouse.fixture\"" : rule)) return 8;
    xpc_connection_set_event_handler(client, ^(xpc_object_t message) {
        if (message == XPC_ERROR_PEER_CODE_SIGNING_REQUIREMENT) {
            result = reject ? 0 : 13;
            dispatch_semaphore_signal(finished);
            return;
        }
        if (!valid_message(message,client,listener,policy_ptr)) { result = 9; dispatch_semaphore_signal(finished); return; }
        const char *value = xpc_dictionary_get_string(message,"kind");
        result = !reject && value && !strcmp(value,"response") ? 0 : 10;
        dispatch_semaphore_signal(finished);
    });
    xpc_connection_activate(client);
    xpc_object_t request = xpc_dictionary_create(NULL,NULL,0);
    xpc_dictionary_set_string(request,"kind","request");
    xpc_connection_send_message(client,request);
    xpc_release(request);
    if (dispatch_semaphore_wait(finished,dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC))) result = 11;
    /* The short-lived fixture keeps callback dependencies alive through exit.
     * Production channel teardown needs a serial owner and explicit drainage. */
    if (result) return result;
    puts(reject ? "wrong XPC remote requirement rejected" : "bidirectional XPC messages identify their held remote");
    return 0;
}
'''


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS XPC SDK")
def test_private_bidirectional_xpc_remote(tmp_path):
    fixture, binary = tmp_path / "fixture.c", tmp_path / "fixture"
    fixture.write_text(SOURCE)
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)}
    compiler = "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang"
    sdk = "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"
    native = Path(__file__).resolve().parents[1] / "app/native-host"
    _execute([compiler, "-isysroot", sdk, "-fno-modules", "-fno-implicit-modules", "-fblocks", "-std=c11",
              "-Wall", "-Wextra", "-Werror", "-I", str(native), str(fixture),
              *(str(native / name) for name in ("NativeCaller.c", "NativeIdentity.c", "MachCode.c", "MachSender.c")),
              "-lbsm", "-framework", "Security", "-framework", "CoreFoundation",
              "-o", str(binary)], tmp_path, "compile", environment, (30, 1048576))
    _execute(["/usr/bin/codesign", "--force", "--sign", "-", "--options", "runtime", str(binary)],
             tmp_path, "sign", environment, (30, 65536))
    out, err = _execute([str(binary)], tmp_path, "run", environment, (15, 65536))
    assert out == "bidirectional XPC messages identify their held remote\n"
    assert not err
    out, err = _execute([str(binary), "--reject"], tmp_path, "reject", environment, (15, 65536))
    assert out == "wrong XPC remote requirement rejected\n"
    assert not err
