"""Public Security comparison over one actual private-port kernel receive; no admission."""
import sys
from pathlib import Path

import pytest
from test_mach_sender import _execute

SOURCE = r'''

#include "MachCode.h"
#include <bsm/libbsm.h>
#include <mach/task_info.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

struct message { mach_msg_header_t header; uint32_t forged_pid; audit_token_t forged_token; };
union allocation { mach_msg_header_t align; unsigned char bytes[sizeof(struct message) + sizeof(mach_msg_max_trailer_t) + sizeof(natural_t)]; };
static pid_t child = -1;
static mach_port_t port = MACH_PORT_NULL, saved_bootstrap = MACH_PORT_NULL;
static bool bootstrap_changed = false;
static int pipes[2] = {-1, -1}, ack[2] = {-1, -1};
#define CHECK(x) do { if (!(x)) { result = __LINE__; goto cleanup; } } while (0)

int main(void) {
    int result = 0, status = 0;
    SecCodeRef own_code = NULL;
    SecRequirementRef requirement = NULL, wrong = NULL;
    CFDictionaryRef info = NULL;
    CFStringRef rule = NULL;
    ph_mach_code_observation observed = {0};
    CHECK(SecCodeCopySelf(kSecCSDefaultFlags, &own_code) == errSecSuccess);
    CHECK(SecCodeCopySigningInformation(own_code, kSecCSSigningInformation, &info) == errSecSuccess);
    CFDataRef hash = CFDictionaryGetValue(info, kSecCodeInfoUnique);
    CHECK(hash && CFGetTypeID(hash) == CFDataGetTypeID() && CFDataGetLength(hash) == 20);
    char hex[41]; const char *alphabet = "0123456789abcdef";
    const UInt8 *bytes = CFDataGetBytePtr(hash);
    for (unsigned i = 0; i < 20; i++) { hex[i*2] = alphabet[bytes[i] >> 4]; hex[i*2+1] = alphabet[bytes[i] & 15]; }
    hex[40] = 0;
    rule = CFStringCreateWithFormat(NULL, NULL, CFSTR("cdhash H\"%s\""), hex);
    CHECK(rule && SecRequirementCreateWithString(rule, kSecCSDefaultFlags, &requirement) == errSecSuccess);
    CHECK(SecRequirementCreateWithString(CFSTR("identifier \"poolhouse-test-definitely-wrong\""), kSecCSDefaultFlags, &wrong) == errSecSuccess);
    CHECK(mach_port_allocate(mach_task_self(), MACH_PORT_RIGHT_RECEIVE, &port) == KERN_SUCCESS);
    CHECK(mach_port_insert_right(mach_task_self(), port, port, MACH_MSG_TYPE_MAKE_SEND) == KERN_SUCCESS);
    CHECK(pipe(pipes) == 0 && pipe(ack) == 0);
    CHECK(task_get_special_port(mach_task_self(), TASK_BOOTSTRAP_PORT, &saved_bootstrap) == KERN_SUCCESS);
    CHECK(task_set_special_port(mach_task_self(), TASK_BOOTSTRAP_PORT, port) == KERN_SUCCESS);
    bootstrap_changed = true;
    child = fork();
    if (child != 0) {
        /* Restore this inert fixture's original special port immediately, even
         * when fork fails. No product process or global namespace is changed. */
        CHECK(task_set_special_port(mach_task_self(), TASK_BOOTSTRAP_PORT, saved_bootstrap) == KERN_SUCCESS);
        bootstrap_changed = false;
    }
    CHECK(child >= 0);
    if (child == 0) {
        close(pipes[0]); close(ack[1]);
        mach_port_t destination = MACH_PORT_NULL;
        if (task_get_special_port(mach_task_self(), TASK_BOOTSTRAP_PORT, &destination) != KERN_SUCCESS
            || !MACH_PORT_VALID(destination)) _exit(31);
        if (task_set_special_port(mach_task_self(), TASK_BOOTSTRAP_PORT, MACH_PORT_NULL) != KERN_SUCCESS) _exit(33);
        /* Only a private kernel-inherited right is used. No bootstrap request,
         * lookup, registration, exec or framework/service call occurs here. */
        audit_token_t own = {0}; mach_msg_type_number_t words = TASK_AUDIT_TOKEN_COUNT;
        if (task_info(mach_task_self(), TASK_AUDIT_TOKEN, (task_info_t)&own, &words) != KERN_SUCCESS
            || words != TASK_AUDIT_TOKEN_COUNT) _exit(34);
        struct message message = {0};
        message.header.msgh_bits = MACH_MSGH_BITS(MACH_MSG_TYPE_COPY_SEND, 0);
        message.header.msgh_size = sizeof(message); message.header.msgh_remote_port = destination;
        message.forged_pid = UINT32_C(0xfedcba98);
        for (unsigned i = 0; i < 8; i++) message.forged_token.val[i] = UINT32_C(0x11223344) + i;
        for (unsigned sent = 0; sent < 3; sent++) if (mach_msg(&message.header, MACH_SEND_MSG | MACH_SEND_TIMEOUT, sizeof(message), 0,
            MACH_PORT_NULL, 1000, MACH_PORT_NULL) != MACH_MSG_SUCCESS) _exit(35);
        if (mach_port_deallocate(mach_task_self(), destination) != KERN_SUCCESS) _exit(36);
        /* Parent starts its finite receive only after a successful queued send. */
        if (write(pipes[1], &own, sizeof(own)) != sizeof(own)) _exit(34);
        close(pipes[1]);
        char byte; if (read(ack[0], &byte, 1) != 1) _exit(37);
        close(ack[0]); _exit(0);
    }
    mach_port_t restored = MACH_PORT_NULL;
    CHECK(task_get_special_port(mach_task_self(), TASK_BOOTSTRAP_PORT, &restored) == KERN_SUCCESS);
    CHECK(restored == saved_bootstrap);
    if (MACH_PORT_VALID(restored)) CHECK(mach_port_deallocate(mach_task_self(), restored) == KERN_SUCCESS);
    close(pipes[1]); pipes[1] = -1; close(ack[0]); ack[0] = -1;
    audit_token_t actual; CHECK(read(pipes[0], &actual, sizeof(actual)) == sizeof(actual));
    union allocation storage = {0};
    CHECK(ph_mach_observe_code(port, &storage, sizeof(storage), 1000, requirement, &observed) == PH_MACH_CODE_OBSERVED);
    CHECK(observed.code && observed.sender.pid == (uint32_t)child
        && !memcmp(&observed.sender.audit, &actual, sizeof(actual)));
    struct message *body = (struct message *)storage.bytes;
    CHECK(observed.sender.pid != body->forged_pid && memcmp(&observed.sender.audit, &body->forged_token, sizeof(actual)));
    CFRelease(observed.code); observed.code = NULL;
    ph_mach_code_observation before; memcpy(&before, &observed, sizeof(before));
    CHECK(ph_mach_observe_code(port, &storage, sizeof(storage), 1000, NULL, &observed) == PH_MACH_CODE_INVALID_ARGUMENT);
    CHECK(!memcmp(&before, &observed, sizeof(before)));
    CHECK(ph_mach_observe_code(port, &storage, sizeof(storage), 1000, wrong, &observed) == PH_MACH_CODE_UNKNOWN);
    CHECK(!memcmp(&before, &observed, sizeof(before)));
    CHECK(write(ack[1], "x", 1) == 1);
    CHECK(waitpid(child, &status, 0) == child); child = -1;
    CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    ph_mach_code_result exited = ph_mach_observe_code(port, &storage, sizeof(storage), 1000, requirement, &observed);
    CHECK(exited == PH_MACH_CODE_UNKNOWN && !memcmp(&before, &observed, sizeof(before)));
    CHECK(ph_mach_observe_code(port, &storage, sizeof(storage), 1, requirement, &observed) == PH_MACH_CODE_RECEIVE_FAILED);
    CHECK(!memcmp(&before, &observed, sizeof(before)));
cleanup:
    if (observed.code) CFRelease(observed.code);
    if (rule) CFRelease(rule);
    if (info) CFRelease(info);
    if (own_code) CFRelease(own_code);
    if (requirement) CFRelease(requirement);
    if (wrong) CFRelease(wrong);
    if (child > 0) {
        pid_t exited = waitpid(child, &status, WNOHANG);
        if (exited == child) fprintf(stderr, "child completed status=%u\n", (unsigned)status);
        else { kill(child, SIGKILL); waitpid(child, NULL, 0); }
    }
    for (unsigned i = 0; i < 2; i++) { if (pipes[i] >= 0) close(pipes[i]); if (ack[i] >= 0) close(ack[i]); }
    if (bootstrap_changed && task_set_special_port(mach_task_self(), TASK_BOOTSTRAP_PORT, saved_bootstrap) != KERN_SUCCESS) result = 91;
    if (MACH_PORT_VALID(saved_bootstrap) && mach_port_deallocate(mach_task_self(), saved_bootstrap) != KERN_SUCCESS) result = 92;
    if (MACH_PORT_VALID(port)) { if (mach_port_deallocate(mach_task_self(), port) != KERN_SUCCESS
        || mach_port_mod_refs(mach_task_self(), port, MACH_PORT_RIGHT_RECEIVE, -1) != KERN_SUCCESS) result = 90; }
    if (result) { fprintf(stderr, "FAIL: fixture stage %d\n", result); return 1; }
    puts("PASS: historical live-child code observed; forged body ignored; wrong requirement and reaped sender unknown; owned cleanup; no admission");
    return 0;
}

'''


@pytest.mark.skipif(sys.platform != 'darwin', reason='public macOS Security and Mach SDK')
def test_actual_child_code_comparison_and_exited_sender(tmp_path):
    root = Path(__file__).resolve().parents[1]
    fixture = tmp_path / 'fixture.c'
    fixture.write_text(SOURCE)
    binary = tmp_path / 'fixture'
    environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'HOME': str(tmp_path), 'TMPDIR': str(tmp_path)}
    compiler = '/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang'
    sdk = '/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk'
    _execute([compiler, '-isysroot', sdk, '-fno-modules', '-fno-implicit-modules', '-std=c11',
              '-Wall', '-Wextra', '-Werror', '-I', str(root / 'app/native-host'),
              str(root / 'app/native-host/MachSender.c'), str(root / 'app/native-host/MachCode.c'),
              str(fixture), '-lbsm', '-framework', 'Security', '-framework', 'CoreFoundation', '-o', str(binary)],
             tmp_path, 'compile', environment, (30, 1048576))
    out, err = _execute([str(binary)], tmp_path, 'run', environment, (15, 65536))
    assert out == ('PASS: historical live-child code observed; forged body ignored; '
                   'wrong requirement and reaped sender unknown; owned cleanup; no admission\n')
    assert not err
