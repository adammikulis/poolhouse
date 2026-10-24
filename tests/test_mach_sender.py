"""Unconfined private-port kernel observation; no service, keys or admission."""
import contextlib
import ctypes
import os
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

SOURCE = r'''
#include "MachSender.h"
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
static mach_port_t port = MACH_PORT_NULL;
static int pipes[2] = {-1, -1}, ack[2] = {-1, -1};
#define CHECK(x) do { if (!(x)) { result = __LINE__; goto cleanup; } } while (0)

static bool parser_attacks(void) {
    union allocation storage = {0};
    struct message message = {0};
    message.header.msgh_size = sizeof(message);
    memcpy(storage.bytes, &message, sizeof(message));
    size_t offset = (sizeof(message) + sizeof(natural_t) - 1) & ~(sizeof(natural_t) - 1);
    mach_msg_audit_trailer_t trailer = {0};
    trailer.msgh_trailer_type = MACH_MSG_TRAILER_FORMAT_0;
    trailer.msgh_trailer_size = sizeof(trailer);
    for (unsigned i = 0; i < 8; i++) trailer.msgh_audit.val[i] = UINT32_C(0x81234567) + i;
    memcpy(storage.bytes + offset, &trailer, sizeof(trailer));
    ph_mach_sender_observation observed = {0};
    if (!ph_mach_test_extract(storage.bytes, sizeof(storage), &observed)
        || memcmp(&observed.audit, &trailer.msgh_audit, sizeof(audit_token_t))
        || observed.pidversion != trailer.msgh_audit.val[7]) return false;
    unsigned char good[sizeof(storage)]; memcpy(good, storage.bytes, sizeof(good));
    /* Rejected inputs leave the entire caller output unchanged. */
    ph_mach_sender_observation sentinel; memset(&sentinel, 0xa5, sizeof(sentinel));
#define REFUSE(cap) do { observed = sentinel; if (ph_mach_test_extract(storage.bytes, (cap), &observed) || memcmp(&sentinel, &observed, sizeof(observed))) return false; memcpy(storage.bytes, good, sizeof(good)); } while (0)
    storage.align.msgh_size = 0; REFUSE(sizeof(storage));
    storage.align.msgh_size = UINT32_MAX; REFUSE(sizeof(storage));
    storage.align.msgh_size = sizeof(storage); REFUSE(sizeof(storage));
    storage.align.msgh_size = sizeof(message) + 1; REFUSE(sizeof(storage));
    storage.align.msgh_bits |= MACH_MSGH_BITS_COMPLEX; REFUSE(sizeof(storage));
    REFUSE(sizeof(mach_msg_header_t) - 1);
    REFUSE(offset + sizeof(mach_msg_trailer_t) - 1);
    REFUSE(offset + sizeof(trailer) - 1);
    mach_msg_trailer_t bad = {99, sizeof(trailer)};
    memcpy(storage.bytes + offset, &bad, sizeof(bad)); REFUSE(sizeof(storage));
    bad.msgh_trailer_type = MACH_MSG_TRAILER_FORMAT_0; bad.msgh_trailer_size = sizeof(mach_msg_trailer_t);
    memcpy(storage.bytes + offset, &bad, sizeof(bad)); REFUSE(sizeof(storage));
    bad.msgh_trailer_size = UINT32_MAX;
    memcpy(storage.bytes + offset, &bad, sizeof(bad)); REFUSE(sizeof(storage));
#undef REFUSE
    return true;
}

int main(void) {
    int result = 0, status = 0;
    CHECK(parser_attacks());
    CHECK(mach_port_allocate(mach_task_self(), MACH_PORT_RIGHT_RECEIVE, &port) == KERN_SUCCESS);
    CHECK(mach_port_insert_right(mach_task_self(), port, port, MACH_MSG_TYPE_MAKE_SEND) == KERN_SUCCESS);
    CHECK(mach_ports_register(mach_task_self(), &port, 1) == KERN_SUCCESS);
    CHECK(pipe(pipes) == 0 && pipe(ack) == 0);
    child = fork(); CHECK(child >= 0);
    if (child == 0) {
        close(pipes[0]); close(ack[1]);
        /* Fresh single-threaded inert fixture only: inherited registered right is
         * a kernel capability, never a globally looked-up name or numeric argument. */
        mach_port_array_t ports = NULL; mach_msg_type_number_t count = 0;
        if (mach_ports_lookup(mach_task_self(), &ports, &count) != KERN_SUCCESS || count < 1) _exit(31);
        for (unsigned i = 1; i < count; i++) if (MACH_PORT_VALID(ports[i])) _exit(31);
        mach_port_t destination = ports[0];
        if (vm_deallocate(mach_task_self(), (vm_address_t)ports, count * sizeof(*ports)) != KERN_SUCCESS) _exit(32);
        if (mach_ports_register(mach_task_self(), NULL, 0) != KERN_SUCCESS) _exit(33);
        audit_token_t own = {0}; mach_msg_type_number_t words = TASK_AUDIT_TOKEN_COUNT;
        if (task_info(mach_task_self(), TASK_AUDIT_TOKEN, (task_info_t)&own, &words) != KERN_SUCCESS
            || words != TASK_AUDIT_TOKEN_COUNT || write(pipes[1], &own, sizeof(own)) != sizeof(own)) _exit(34);
        close(pipes[1]);
        struct message message = {0};
        message.header.msgh_bits = MACH_MSGH_BITS(MACH_MSG_TYPE_COPY_SEND, 0);
        message.header.msgh_size = sizeof(message); message.header.msgh_remote_port = destination;
        message.forged_pid = UINT32_C(0xfedcba98);
        for (unsigned i = 0; i < 8; i++) message.forged_token.val[i] = UINT32_C(0x11223344) + i;
        if (mach_msg(&message.header, MACH_SEND_MSG | MACH_SEND_TIMEOUT, sizeof(message), 0,
            MACH_PORT_NULL, 1000, MACH_PORT_NULL) != MACH_MSG_SUCCESS) _exit(35);
        if (mach_port_deallocate(mach_task_self(), destination) != KERN_SUCCESS) _exit(36);
        char byte; if (read(ack[0], &byte, 1) != 1) _exit(37);
        close(ack[0]); _exit(0);
    }
    CHECK(mach_ports_register(mach_task_self(), NULL, 0) == KERN_SUCCESS);
    close(pipes[1]); pipes[1] = -1; close(ack[0]); ack[0] = -1;
    audit_token_t actual; CHECK(read(pipes[0], &actual, sizeof(actual)) == sizeof(actual));
    union allocation storage = {0}; ph_mach_sender_observation observed = {0}; mach_msg_return_t error;
    CHECK(ph_mach_observe_sender(port, &storage, sizeof(storage), 1000, &observed, &error) == PH_MACH_OBSERVED);
    CHECK(error == MACH_MSG_SUCCESS && observed.pid == (uint32_t)child
        && !memcmp(&observed.audit, &actual, sizeof(actual)));
    struct message *body = (struct message *)storage.bytes;
    CHECK(observed.pid != body->forged_pid && memcmp(&observed.audit, &body->forged_token, sizeof(actual)));
    CHECK(ph_mach_observe_sender(port, &storage, sizeof(storage), 1, &observed, &error) == PH_MACH_RECEIVE_FAILED
        && error == MACH_RCV_TIMED_OUT);
    struct { mach_msg_header_t header; mach_msg_body_t body; } complex = {0};
    complex.header.msgh_bits = MACH_MSGH_BITS(MACH_MSG_TYPE_COPY_SEND, 0) | MACH_MSGH_BITS_COMPLEX;
    complex.header.msgh_size = sizeof(complex); complex.header.msgh_remote_port = port;
    CHECK(mach_msg(&complex.header, MACH_SEND_MSG | MACH_SEND_TIMEOUT, sizeof(complex), 0,
        MACH_PORT_NULL, 1000, MACH_PORT_NULL) == MACH_MSG_SUCCESS);
    CHECK(ph_mach_observe_sender(port, &storage, sizeof(storage), 1000, &observed, &error) == PH_MACH_INVALID_MESSAGE);
    struct { mach_msg_header_t header; unsigned char body[sizeof(union allocation)]; } large = {0};
    large.header.msgh_bits = MACH_MSGH_BITS(MACH_MSG_TYPE_COPY_SEND, 0);
    large.header.msgh_size = sizeof(large); large.header.msgh_remote_port = port;
    CHECK(mach_msg(&large.header, MACH_SEND_MSG | MACH_SEND_TIMEOUT, sizeof(large), 0,
        MACH_PORT_NULL, 1000, MACH_PORT_NULL) == MACH_MSG_SUCCESS);
    CHECK(ph_mach_observe_sender(port, &storage, sizeof(storage), 1000, &observed, &error) == PH_MACH_RECEIVE_FAILED
        && error == MACH_RCV_TOO_LARGE);
    CHECK(write(ack[1], "x", 1) == 1);
    CHECK(waitpid(child, &status, 0) == child); child = -1;
    CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    /* Exited sender observation remains historical data, never current liveness. */
cleanup:
    if (child > 0) { kill(child, SIGKILL); waitpid(child, NULL, 0); }
    for (unsigned i = 0; i < 2; i++) { if (pipes[i] >= 0) close(pipes[i]); if (ack[i] >= 0) close(ack[i]); }
    if (MACH_PORT_VALID(port)) { mach_ports_register(mach_task_self(), NULL, 0); if (mach_port_deallocate(mach_task_self(), port) != KERN_SUCCESS
        || mach_port_mod_refs(mach_task_self(), port, MACH_PORT_RIGHT_RECEIVE, -1) != KERN_SUCCESS) result = 90; }
    if (result) { fprintf(stderr, "FAIL: fixture stage %d\n", result); return 1; }
    puts("PASS: UNCONFINED kernel-only actual-child sender; forged body ignored; hostile bounds refused; timeout and owned cleanup; no admission");
    return 0;
}
'''


def _only_unreaped_leader(group):
    # Same bounded own-group check used by maintained prepare_candidate._tool.
    library = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    library.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
    library.proc_listpids.restype = ctypes.c_int
    members = (ctypes.c_int * 128)()
    count = library.proc_listpids(2, group, members, ctypes.sizeof(members))
    return (0 < count < ctypes.sizeof(members) and count % ctypes.sizeof(ctypes.c_int) == 0
            and set(members[:count // ctypes.sizeof(ctypes.c_int)]) <= {0, group})


def _execute(command, tmp_path, label, environment, limits):
    seconds, file_limit = limits
    def bounds():
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    stdout, stderr = tmp_path / (label + '.stdout'), tmp_path / (label + '.stderr')
    with stdout.open('xb') as out, stderr.open('xb') as err:
        stdout.chmod(0o600)
        stderr.chmod(0o600)
        process = subprocess.Popen(command, env=environment, cwd=tmp_path, stdin=subprocess.DEVNULL,
                                   stdout=out, stderr=err, close_fds=True,
                                   start_new_session=True, preexec_fn=bounds)
        try:
            deadline = time.monotonic() + seconds
            completed = None
            while completed is None and time.monotonic() < deadline:
                completed = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
                if completed is None:
                    time.sleep(.02)
            # Keep the owned group leader unreaped until cleanup, preventing PID
            # reuse while settling any child left by a failed fixture.
            with contextlib.suppress(ProcessLookupError):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except PermissionError:
                    # Darwin refuses signaling an already-exited zombie-only group.
                    if completed is None or not _only_unreaped_leader(process.pid):
                        raise
            code = process.wait(timeout=3)
            assert completed is not None, 'owned fixture deadline exceeded'
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
    assert code == 0, stderr.read_text()
    return stdout.read_text(), stderr.read_text()


@pytest.mark.skipif(sys.platform != 'darwin', reason='public macOS Mach SDK')
def test_actual_child_kernel_observation_and_hostile_bounds(tmp_path):
    root = Path(__file__).resolve().parents[1]
    fixture = tmp_path / 'fixture.c'
    fixture.write_text(SOURCE)
    binary = tmp_path / 'fixture'
    environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'HOME': str(tmp_path), 'TMPDIR': str(tmp_path)}
    compiler = '/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang'
    sdk = '/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk'
    _execute([compiler, '-isysroot', sdk, '-fno-modules', '-fno-implicit-modules', '-std=c11',
              '-Wall', '-Wextra', '-Werror', '-DTESTING', '-I', str(root / 'app/native-host'),
              str(root / 'app/native-host/MachSender.c'), str(fixture), '-lbsm', '-o', str(binary)],
             tmp_path, 'compile', environment, (30, 1048576))
    out, err = _execute([str(binary)], tmp_path, 'run', environment, (15, 65536))
    assert out == ('PASS: UNCONFINED kernel-only actual-child sender; forged body ignored; hostile bounds refused; '
                   'timeout and owned cleanup; no admission\n')
    assert not err
