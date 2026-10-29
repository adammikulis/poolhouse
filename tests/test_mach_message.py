"""The service receives body and kernel sender evidence in one owned message."""

import sys
from pathlib import Path

import pytest

from tests.test_mach_sender import _execute

SOURCE = r'''
#include "MachSender.h"
#include <bsm/libbsm.h>
#include <mach/mach.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

struct payload { mach_msg_header_t header; uint8_t body[8]; };
int main(void) {
    mach_port_t port = MACH_PORT_NULL;
    if (mach_port_allocate(mach_task_self(), MACH_PORT_RIGHT_RECEIVE, &port) != KERN_SUCCESS ||
        mach_port_insert_right(mach_task_self(), port, port, MACH_MSG_TYPE_MAKE_SEND) != KERN_SUCCESS) return 1;
    struct payload sent = {0};
    sent.header.msgh_bits = MACH_MSGH_BITS(MACH_MSG_TYPE_COPY_SEND, 0);
    sent.header.msgh_size = sizeof(sent); sent.header.msgh_remote_port = port;
    memcpy(sent.body, "verified", sizeof(sent.body));
    if (mach_msg(&sent.header, MACH_SEND_MSG | MACH_SEND_TIMEOUT, sizeof(sent), 0, MACH_PORT_NULL, 1000,
                 MACH_PORT_NULL) != MACH_MSG_SUCCESS) return 2;
    ph_mach_message *message = NULL;
    if (ph_mach_receive(port, 4096, 1000, &message, NULL) != PH_MACH_OBSERVED || !message) return 3;
    size_t size = 0;
    const void *body = ph_mach_body(message, &size);
    const ph_mach_sender_observation *sender = ph_mach_sender(message);
    if (!body || size != sizeof(sent.body) || memcmp(body, sent.body, size) || !sender ||
        audit_token_to_pid(sender->audit) != getpid() || audit_token_to_euid(sender->audit) != geteuid()) return 4;
    ph_mach_message_destroy(&message);
    if (message) return 5;
    ph_mach_message_destroy(&message);
    if (mach_port_deallocate(mach_task_self(), port) != KERN_SUCCESS ||
        mach_port_mod_refs(mach_task_self(), port, MACH_PORT_RIGHT_RECEIVE, -1) != KERN_SUCCESS) return 6;
    puts("owned kernel message retains body and sender");
    return 0;
}
'''


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Mach SDK")
def test_received_body_and_kernel_sender_share_one_owned_message(tmp_path):
    root = Path(__file__).resolve().parents[1]
    fixture, binary = tmp_path / "fixture.c", tmp_path / "fixture"
    fixture.write_text(SOURCE)
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)}
    compiler = "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang"
    sdk = "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"
    _execute([compiler, "-isysroot", sdk, "-fno-modules", "-fno-implicit-modules", "-std=c11", "-Wall", "-Wextra", "-Werror",
              "-I", str(root / "app/native-host"), str(root / "app/native-host/MachSender.c"), str(fixture), "-lbsm", "-o", str(binary)],
             tmp_path, "compile", environment, (30, 1048576))
    out, err = _execute([str(binary)], tmp_path, "run", environment, (15, 65536))
    assert out == "owned kernel message retains body and sender\n"
    assert not err
