"""Linux node memory protection is established before keys or state are read."""
from __future__ import annotations

import contextlib
import errno
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from poolhouse.board.client import Client
from poolhouse.errors import Error

if sys.platform == "linux":
    import resource

pytest_plugins = ["node_kit"]

# This fixture applies a real kernel syscall filter before exec, never a product bypass.
DENY_SYSCALL = r'''
import ctypes, os, resource, sys
class Filter(ctypes.Structure):
    _fields_ = [('code', ctypes.c_ushort), ('jt', ctypes.c_ubyte), ('jf', ctypes.c_ubyte), ('k', ctypes.c_uint)]
class Program(ctypes.Structure):
    _fields_ = [('length', ctypes.c_ushort), ('filters', ctypes.POINTER(Filter))]
mode = sys.argv[1]
if mode == 'core-get':
    core = resource.RLIMIT_CORE
    instructions = [(0x20,0,0,0), (0x15,0,8,302), (0x20,0,0,24), (0x15,0,5,core),
                    (0x20,0,0,32), (0x15,0,3,0), (0x20,0,0,36), (0x15,0,1,0),
                    (0x06,0,0,0x50001), (0x06,0,0,0x7fff0000),
                    (0x20,0,0,0), (0x15,0,3,97), (0x20,0,0,16),
                    (0x15,0,1,core), (0x06,0,0,0x50001), (0x06,0,0,0x7fff0000)]
elif mode == 'core':
    core = resource.RLIMIT_CORE
    instructions = [(0x20,0,0,0), (0x15,0,3,302), (0x20,0,0,24),
                    (0x15,0,1,core), (0x06,0,0,0x50001),
                    (0x20,0,0,0), (0x15,0,3,160), (0x20,0,0,16),
                    (0x15,0,1,core), (0x06,0,0,0x50001), (0x06,0,0,0x7fff0000)]
else:
    operation = 4 if mode == 'set-dumpable' else 3
    instructions = [(0x20,0,0,0), (0x15,0,3,157), (0x20,0,0,16),
                    (0x15,0,1,operation), (0x06,0,0,0x50001), (0x06,0,0,0x7fff0000)]
filters = (Filter * len(instructions))(*(Filter(*row) for row in instructions))
program = Program(len(filters), filters)
libc = ctypes.CDLL(None, use_errno=True)
libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
libc.prctl.restype = ctypes.c_int
if libc.prctl(38, 1, 0, 0, 0) or libc.prctl(22, 2, ctypes.byref(program), 0, 0):
    raise OSError(ctypes.get_errno(), 'test syscall filter refused')
os.execv(sys.argv[2], sys.argv[2:])
'''


@contextlib.contextmanager
def child(argv):
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def eventually(check, process):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(f"node exited before protection was observed: {process.stderr.read()!r}")
        if check():
            return
        time.sleep(0.01)
    pytest.fail("node did not establish memory protection before reading its key")


def memory_denied(pid):
    try:
        descriptor = os.open(f"/proc/{pid}/mem", os.O_RDONLY)
    except PermissionError:
        return True
    os.close(descriptor)
    return False


@pytest.mark.skipif(sys.platform != "linux", reason="Linux kernel memory protection")
def test_direct_parent_control_can_open_its_same_uid_child_memory():
    with child([sys.executable, "-I", "-c", "import sys; print('ready', flush=True); sys.stdin.read()"] ) as process:
        assert process.stdout.readline() == b"ready\n"
        assert not memory_denied(process.pid), "a negative node probe is meaningless if Yama already denies the control"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux kernel memory protection")
@pytest.mark.parametrize("key_on_stdin", [False, True])
def test_node_denies_parent_memory_reads_and_cannot_restore_core_dumps(node_binary, key_on_stdin):
    with tempfile.TemporaryDirectory(prefix="pn-memory-") as folder:
        state = Path(folder) / "state"
        argv = [str(node_binary), "run", "--state", str(state)]
        if key_on_stdin:
            argv.append("--key-stdin")
        with child(argv) as process:
            eventually(lambda: memory_denied(process.pid) and
                       resource.prlimit(process.pid, resource.RLIMIT_CORE) == (0, 0), process)
            assert resource.prlimit(process.pid, resource.RLIMIT_CORE) == (0, 0)
            with pytest.raises(PermissionError) as refused:
                resource.prlimit(process.pid, resource.RLIMIT_CORE, (1, 1))
            assert refused.value.errno == errno.EPERM
            if key_on_stdin:
                assert not state.exists(), "protection precedes key input and state creation"
            else:
                client = Client(state, autostart=False, timeout=1)
                def healthy():
                    try:
                        return bool(client.call("status"))
                    except (OSError, Error):
                        return False
                eventually(healthy, process)


@pytest.mark.skipif(sys.platform != "linux" or platform.machine() != "x86_64", reason="x86_64 Linux syscall filter")
@pytest.mark.parametrize("mode", ["core", "core-get", "set-dumpable", "get-dumpable"])
def test_denied_memory_protection_exits_before_reading_stdin_or_creating_state(node_binary, mode):
    with tempfile.TemporaryDirectory(prefix="pn-memory-") as folder:
        state = Path(folder) / "state"
        argv = [sys.executable, "-I", "-c", DENY_SYSCALL, mode,
                str(node_binary), "run", "--state", str(state), "--key-stdin"]
        with child(argv) as process:
            assert process.wait(timeout=10) != 0, "a failed protection syscall must stop startup"
            assert b"poolhouse-node: cannot protect process memory:" in process.stderr.read()
            assert not state.exists()
