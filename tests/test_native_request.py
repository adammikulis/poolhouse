"""Exercise the owned native request using private local Mach ports."""

import sys
from pathlib import Path

import pytest

from tests.test_mach_sender import _execute

SOURCE = r'''
#include "NativeRequest.h"
#include <mach/mach.h>
#include <stdio.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

struct frame { mach_msg_header_t header; unsigned char body[8]; };
static int run(mach_msg_return_t inject) {
    mach_port_t service = MACH_PORT_NULL, reply = MACH_PORT_NULL;
    if (mach_port_allocate(mach_task_self(), MACH_PORT_RIGHT_RECEIVE, &service) ||
        mach_port_insert_right(mach_task_self(), service, service, MACH_MSG_TYPE_MAKE_SEND) ||
        mach_port_allocate(mach_task_self(), MACH_PORT_RIGHT_RECEIVE, &reply)) return 1;
    struct frame request = {0};
    request.header.msgh_bits = MACH_MSGH_BITS(MACH_MSG_TYPE_COPY_SEND, 0);
    request.header.msgh_size = sizeof(request); request.header.msgh_remote_port = service;
    if (mach_msg(&request.header, MACH_SEND_MSG | MACH_SEND_TIMEOUT, sizeof(request), 0,
                 MACH_PORT_NULL, 1000, MACH_PORT_NULL)) return 12;
    ph_mach_message *message = NULL;
    mach_msg_return_t error = 0;
    if (ph_mach_receive_request(service, 32, 1000, &message, &error) != PH_MACH_INVALID_MESSAGE || message) return 13;
    request.header.msgh_bits = MACH_MSGH_BITS(MACH_MSG_TYPE_COPY_SEND, MACH_MSG_TYPE_MAKE_SEND_ONCE);
    request.header.msgh_size = sizeof(request);
    request.header.msgh_remote_port = service; request.header.msgh_local_port = reply;
    memcpy(request.body, "request", 8);
    if (mach_msg(&request.header, MACH_SEND_MSG | MACH_SEND_TIMEOUT, sizeof(request), 0,
                 MACH_PORT_NULL, 1000, MACH_PORT_NULL)) return 2;
    if (ph_mach_receive_request(service, 32, 1000, &message, &error) != PH_MACH_OBSERVED) return 3;
    size_t len = 0;
    const unsigned char *body = ph_mach_body(message, &len);
    if (!body || len != 8 || memcmp(body, "request", 8) ||
        !ph_mach_sender(message) || ph_mach_sender(message)->pid != (uint32_t)getpid()) return 4;
    if (ph_mach_reply_public(&message, (const uint8_t *)"", 0) != PH_PUBLIC_REPLY_NOT_SENT || !message) return 5;
    if (inject) ph_mach_test_fail_public_reply(inject);
    if (ph_mach_reply_public(&message, (const uint8_t *)"response", 8) != PH_PUBLIC_REPLY_SENT_UNCONFIRMED || message) return 6;
    if (ph_mach_reply_public(&message, (const uint8_t *)"response", 8) != PH_PUBLIC_REPLY_NOT_SENT) return 7;
    union { mach_msg_header_t header; unsigned char data[256]; } response = {0};
    if (mach_msg(&response.header, MACH_RCV_MSG | MACH_RCV_TIMEOUT, 0, sizeof(response), reply,
                 1000, MACH_PORT_NULL)) return 8;
    const unsigned char *p = response.data + sizeof(mach_msg_header_t);
    if (memcmp(p, "\0\0\0\10response", 12)) return 9;
    mach_msg_destroy(&response.header);
    mach_port_deallocate(mach_task_self(), service);
    mach_port_mod_refs(mach_task_self(), service, MACH_PORT_RIGHT_RECEIVE, -1);
    mach_port_mod_refs(mach_task_self(), reply, MACH_PORT_RIGHT_RECEIVE, -1);
    return 0;
}
int main(void) {
    int result = run(0);
    if (result) return result;
    mach_msg_return_t errors[] = { MACH_SEND_INVALID_DATA, MACH_SEND_INVALID_DEST, MACH_SEND_TIMED_OUT, MACH_SEND_INTERRUPTED };
    for (size_t i = 0; i < sizeof(errors) / sizeof(errors[0]); i++) {
        pid_t child = fork();
        if (child < 0) return 10;
        if (!child) _exit(run(errors[i]));
        int status = 0;
        if (waitpid(child, &status, 0) != child || !WIFEXITED(status) || WEXITSTATUS(status) != 70) return 11;
    }
    puts("owned request and unconfirmed reply");
    return 0;
}
'''


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Mach SDK")
def test_owned_request_and_ambiguous_send(tmp_path):
    fixture, binary = tmp_path / "fixture.c", tmp_path / "fixture"
    fixture.write_text(SOURCE)
    root = Path(__file__).resolve().parents[1]
    native = root / "app" / "native-host"
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)}
    compiler = "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang"
    sdk = "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"
    _execute([compiler, "-isysroot", sdk, "-fno-modules", "-fno-implicit-modules", "-std=c11",
              "-Wall", "-Wextra", "-Werror", "-DTESTING", "-I", str(native), str(fixture),
              str(native / "MachSender.c"), "-lbsm", "-o", str(binary)], tmp_path,
             "compile", environment, (30, 1048576))
    out, err = _execute([str(binary)], tmp_path, "run", environment, (15, 65536))
    assert out == "owned request and unconfirmed reply\n"
    assert not err
