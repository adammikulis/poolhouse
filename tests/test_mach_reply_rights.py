"""Exercise send-once reply ownership against the local Mach kernel."""

import sys

import pytest

from tests.test_mach_sender import _execute

SOURCE = r'''
#include <mach/mach.h>
#include <stdio.h>
#include <string.h>

struct frame { mach_msg_header_t header; unsigned char data[8]; };
int main(void) {
    mach_port_t service = MACH_PORT_NULL, reply = MACH_PORT_NULL;
    if (mach_port_allocate(mach_task_self(), MACH_PORT_RIGHT_RECEIVE, &service) != KERN_SUCCESS ||
        mach_port_insert_right(mach_task_self(), service, service, MACH_MSG_TYPE_MAKE_SEND) != KERN_SUCCESS ||
        mach_port_allocate(mach_task_self(), MACH_PORT_RIGHT_RECEIVE, &reply) != KERN_SUCCESS) return 1;
    struct frame request = {0};
    request.header.msgh_bits = MACH_MSGH_BITS(MACH_MSG_TYPE_COPY_SEND, MACH_MSG_TYPE_MAKE_SEND_ONCE);
    request.header.msgh_size = sizeof(request); request.header.msgh_remote_port = service;
    request.header.msgh_local_port = reply; memcpy(request.data, "request", 8);
    if (mach_msg(&request.header, MACH_SEND_MSG | MACH_SEND_TIMEOUT, sizeof(request), 0,
                 MACH_PORT_NULL, 1000, MACH_PORT_NULL) != MACH_MSG_SUCCESS) return 2;
    union { struct frame frame; unsigned char bytes[sizeof(struct frame) + sizeof(mach_msg_max_trailer_t)]; } got = {0};
    if (mach_msg(&got.frame.header, MACH_RCV_MSG | MACH_RCV_TIMEOUT, 0, sizeof(got), service,
                 1000, MACH_PORT_NULL) != MACH_MSG_SUCCESS ||
        MACH_MSGH_BITS_REMOTE(got.frame.header.msgh_bits) != MACH_MSG_TYPE_PORT_SEND_ONCE ||
        memcmp(got.frame.data, "request", 8)) return 3;
    struct frame response = {0};
    response.header.msgh_bits = MACH_MSGH_BITS(MACH_MSG_TYPE_MOVE_SEND_ONCE, 0);
    response.header.msgh_size = sizeof(response);
    response.header.msgh_remote_port = got.frame.header.msgh_remote_port;
    got.frame.header.msgh_remote_port = MACH_PORT_NULL;
    mach_msg_destroy(&got.frame.header);
    memcpy(response.data, "response", 8);
    if (mach_msg(&response.header, MACH_SEND_MSG | MACH_SEND_TIMEOUT, sizeof(response), 0,
                 MACH_PORT_NULL, 1000, MACH_PORT_NULL) != MACH_MSG_SUCCESS) return 4;
    if (mach_msg(&response.header, MACH_SEND_MSG | MACH_SEND_TIMEOUT, sizeof(response), 0,
                 MACH_PORT_NULL, 1000, MACH_PORT_NULL) == MACH_MSG_SUCCESS) return 7;
    memset(&got, 0, sizeof(got));
    if (mach_msg(&got.frame.header, MACH_RCV_MSG | MACH_RCV_TIMEOUT, 0, sizeof(got), reply,
                 1000, MACH_PORT_NULL) != MACH_MSG_SUCCESS || memcmp(got.frame.data, "response", 8)) return 5;
    mach_msg_destroy(&got.frame.header);
    request.header.msgh_bits = MACH_MSGH_BITS(MACH_MSG_TYPE_COPY_SEND, MACH_MSG_TYPE_MAKE_SEND_ONCE);
    request.header.msgh_remote_port = service; request.header.msgh_local_port = reply;
    if (mach_msg(&request.header, MACH_SEND_MSG | MACH_SEND_TIMEOUT, sizeof(request), 0,
                 MACH_PORT_NULL, 1000, MACH_PORT_NULL) != MACH_MSG_SUCCESS) return 8;
    memset(&got, 0, sizeof(got));
    if (mach_msg(&got.frame.header, MACH_RCV_MSG | MACH_RCV_TIMEOUT, 0, sizeof(got), service,
                 1000, MACH_PORT_NULL) != MACH_MSG_SUCCESS) return 9;
    response.header.msgh_bits = MACH_MSGH_BITS(MACH_MSG_TYPE_MOVE_SEND_ONCE, 0);
    response.header.msgh_remote_port = got.frame.header.msgh_remote_port;
    got.frame.header.msgh_remote_port = MACH_PORT_NULL;
    mach_msg_destroy(&got.frame.header);
    if (mach_port_mod_refs(mach_task_self(), reply, MACH_PORT_RIGHT_RECEIVE, -1) != KERN_SUCCESS) return 10;
    mach_msg_return_t dead = mach_msg(&response.header, MACH_SEND_MSG | MACH_SEND_TIMEOUT, sizeof(response), 0,
                                     MACH_PORT_NULL, 1000, MACH_PORT_NULL);
    /* This target may consume a send-once reply successfully even after the
     * receiver disappears. A successful send is not proof of delivery. */
    if (dead == MACH_SEND_INVALID_DEST) mach_msg_destroy(&response.header);
    else if (dead != MACH_MSG_SUCCESS) { fprintf(stderr, "dead reply result: 0x%x\n", dead); return 11; }
    if (mach_port_deallocate(mach_task_self(), service) != KERN_SUCCESS ||
        mach_port_mod_refs(mach_task_self(), service, MACH_PORT_RIGHT_RECEIVE, -1) != KERN_SUCCESS) return 6;
    puts("send-once reply transfers exactly once");
    return 0;
}
'''


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Mach SDK")
def test_kernel_send_once_reply_transfer(tmp_path):
    fixture, binary = tmp_path / "fixture.c", tmp_path / "fixture"
    fixture.write_text(SOURCE)
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)}
    compiler = "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang"
    sdk = "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"
    _execute([compiler, "-isysroot", sdk, "-fno-modules", "-fno-implicit-modules", "-std=c11", "-Wall", "-Wextra",
              "-Werror", str(fixture), "-o", str(binary)], tmp_path, "compile", environment, (30, 1048576))
    out, err = _execute([str(binary)], tmp_path, "run", environment, (15, 65536))
    assert out == "send-once reply transfers exactly once\n"
    assert not err
