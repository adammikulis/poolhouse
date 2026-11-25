"""What a process the Seatbelt engine confines cannot do or learn, tried by real programs under the real ``sandbox-exec``.

The attacker is the code a coding harness runs: it has the user's id and nothing else. Each test starts a real confined process
and tries one thing the isolated launcher promises it cannot do (docs/design-isolated-launcher.md, sections 5.1 and 5.2); what
it can still learn is declared in `seatbelt.EXPOSURES` and pinned here. The identifiers of this machine are read in the test and
searched for in what the confined process printed; no test prints, stores or asserts one.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import poolhouse
from poolhouse import sandbox
from poolhouse.sandbox import Limits, process_tree, run, seatbelt as profiles
from poolhouse.sandbox.seatbelt import Seatbelt
from tests.process_kit import FORKS
from tests.sandbox_kit import policy, running

pytest_plugins = ["tests.sandbox_kit"]

SOURCE = str(Path(poolhouse.__file__).resolve().parent.parent)


def python(code: str, tmp_path: Path, *argv: str, limits: Limits | None = None, **kept):
    """The result of ``code`` run by this interpreter inside the engine's confinement, which reads the library and ``tmp_path``."""
    held = policy(read=[SOURCE, tmp_path], python=True, limits=limits, env={"HOME": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"}, **kept)
    return run([os.path.realpath(sys.executable), "-I", "-c", "import sys; sys.path.insert(0, " + repr(SOURCE) + ")\n" + code, *argv],
               held, diagnose="never")


def test_a_confined_process_knows_it_is_confined_and_an_ordinary_one_does_not(tmp_path, seatbelt):
    assert sandbox.confined() is False
    inside = python("from poolhouse import sandbox; print(sandbox.confined())", tmp_path)
    assert (inside.returncode, inside.stdout.strip()) == (0, "True"), inside.stderr


def test_a_confined_shell_signals_its_own_children_and_no_process_beside_the_session(tmp_path, seatbelt):
    bystander = subprocess.Popen(["/bin/sleep", "60"], start_new_session=True)
    script = ("sleep 8 & child=$!; kill $child; wait $child; echo child=$?; "
              f"kill -0 {bystander.pid} 2>/dev/null; echo bystander=$?; kill -0 {os.getpid()} 2>/dev/null; echo launcher=$?")
    try:
        held = replace(policy(), signal_children=True)
        said = dict(line.split("=") for line in run(["/bin/sh", "-c", script], held).stdout.split())
        assert said["child"] == "143", said
        assert said["bystander"] != "0" and said["launcher"] != "0", said
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait()


def test_a_fork_flood_is_refused_by_the_kernel_at_the_allowance_of_the_run(tmp_path, seatbelt):
    done = python(FORKS, tmp_path, "400", limits=Limits(wall_seconds=60, processes=40))
    ended = json.loads(done.stdout)
    assert ended["refused"] == ended["again"] and ended["forked"] < 200, (ended, done.stderr)


def identifiers() -> dict[str, str]:
    """The serial number, the hardware UUIDs, the provisioning UDID and the network hardware addresses of this machine, by name,
    as the unconfined parent reads them. A test never prints a value: it names the kind that leaked."""
    registry = subprocess.run(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True, text=True, check=True).stdout
    overview = subprocess.run(["/usr/sbin/system_profiler", "SPHardwareDataType"], capture_output=True, text=True, check=True).stdout
    found = {kind: match.group(1) for kind, pattern in (("serial", r'"IOPlatformSerialNumber" = "([^"]+)"'),
                                                          ("platform-uuid", r'"IOPlatformUUID" = "([^"]+)"'),
                                                          ("hardware-uuid", r"Hardware UUID: (\S+)"), ("udid", r"Provisioning UDID: (\S+)"))
             if (match := re.search(pattern, registry + overview))}
    interfaces = subprocess.run(["/sbin/ifconfig", "-a"], capture_output=True, text=True, check=True).stdout
    found.update({f"address-{n}": mac for n, mac in enumerate(sorted(set(re.findall(r"ether ([0-9a-f:]{17})", interfaces))))})
    return found


HOST_UUID = """
import ctypes
buffer = ctypes.create_string_buffer(16)
print(ctypes.CDLL(None, use_errno=True).gethostuuid(buffer, (ctypes.c_long * 2)(1, 0)))
"""


def test_no_hardware_identifier_is_readable(tmp_path, seatbelt):
    known = identifiers()
    assert {"serial", "platform-uuid", "hardware-uuid"} <= set(known), f"this machine reports only {sorted(known)} to look for"
    leaked, working = {}, {}
    for argv in (["/usr/sbin/ioreg", "-c", "IOPlatformExpertDevice"], ["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                 ["/usr/sbin/ioreg", "-l"], ["/usr/sbin/system_profiler", "SPHardwareDataType"]):
        done = run(argv, policy(limits=Limits(wall_seconds=90, output_bytes=64_000_000)), diagnose="never")
        text = (done.stdout + done.stderr).lower()
        leaked[" ".join(argv[1:])] = sorted(kind for kind, value in known.items() if value.lower() in text)
        working[" ".join(argv[1:])] = len(text)
    assert {name: kinds for name, kinds in leaked.items() if kinds} == {}
    assert working["-c IOPlatformExpertDevice"] > 10_000, "ioreg printed no registry at all, so it found no identifier because it ran nothing"
    asked = python(HOST_UUID, tmp_path)
    assert (asked.returncode, asked.stdout.strip()) == (0, "-1"), asked.stderr


def test_a_policy_with_a_gpu_leaves_the_property_reads_a_device_may_need_and_any_other_closes_both_channels(tmp_path):
    closed = ("(deny iokit-get-properties)", "(deny syscall-unix (syscall-number SYS_gethostuuid))")
    held = policy()
    assert all(rule in profiles.profile(held, "/bin/echo", "t") for rule in closed)
    assert not any(rule in profiles.profile(replace(held, gpu=True), "/bin/echo", "t") for rule in closed)


SYSCTLS = """
import ctypes, json, sys
libc = ctypes.CDLL(None, use_errno=True)
size = ctypes.c_size_t()
print(json.dumps([name for name in open(sys.argv[1]).read().split() if libc.sysctlbyname(name.encode(), None, ctypes.byref(size), None, 0) == 0]))
"""

PROCESSES = """
import ctypes, errno, json, os, sys
libc, libproc = ctypes.CDLL(None, use_errno=True), ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
libc.ptrace.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
def sysctl(*ids):
    mib, size = (ctypes.c_int * len(ids))(*ids), ctypes.c_size_t(0)
    if libc.sysctl(mib, len(ids), None, ctypes.byref(size), None, 0):
        return b""
    buffer = ctypes.create_string_buffer(size.value + 4096)
    size = ctypes.c_size_t(len(buffer))
    return b"" if libc.sysctl(mib, len(ids), buffer, ctypes.byref(size), None, 0) else buffer.raw[:size.value]
def refused(call):
    try:
        call()
    except PermissionError:
        return True
    return False
def about(pid):
    return {"listed": len(sysctl(1, 14, 1, pid)) > 0, "named": b"sleep" in sysctl(1, 14, 1, pid),
            "arguments": len(sysctl(1, 49, pid)) > 0, "environment_in_what_is_listed": b"POOLHOUSE_X1_MARKER" in sysctl(1, 14, 1, pid),
            "bsd_info": libproc.proc_pidinfo(pid, 3, 0, ctypes.create_string_buffer(136), 136) > 0,
            "working_directory": libproc.proc_pidinfo(pid, 9, 0, ctypes.create_string_buffer(2352), 2352) > 0,
            "open_files": libproc.proc_pidinfo(pid, 1, 0, ctypes.create_string_buffer(4096), 4096) > 0,
            "signal": not refused(lambda: os.kill(pid, 0)), "trace": libc.ptrace(10, pid, None, 0) != -1}
print(json.dumps({name: about(int(pid)) for name, pid in zip(("launcher", "bystander"), sys.argv[1:])}))
"""


def test_the_system_names_a_process_can_read_are_exactly_the_declared_exposures(tmp_path, seatbelt):
    names = subprocess.run(["/usr/sbin/sysctl", "-aN"], capture_output=True, text=True, check=True).stdout.split()
    listing = tmp_path / "names"
    listing.write_text("\n".join(names))
    open_to_all = set(json.loads(subprocess.run([sys.executable, "-c", SYSCTLS, str(listing)], capture_output=True, text=True, check=True).stdout))
    readable = set(json.loads(python(SYSCTLS, tmp_path, str(listing)).stdout))
    exact = {name for each in profiles.EXPOSURES for name in each.sysctls}
    prefixes = tuple(prefix for each in profiles.EXPOSURES for prefix in each.sysctl_prefixes)
    undeclared = sorted(name for name in readable if name not in exact and not name.startswith(prefixes))
    assert undeclared == [], "a confined process reads system names that no exposure declares"
    assert exact & open_to_all <= readable, "a declared name is not readable, so the declaration is not what the profile grants"
    assert len(readable) < len(open_to_all) / 2, "the profile hides most of what an unconfined process reads"
    assert {each.id for each in profiles.EXPOSURES} == {"X1", "X2"}


def test_signal_ptrace_and_the_reads_of_other_processes_reach_nothing_outside_and_the_list_is_exactly_the_exposure(tmp_path, seatbelt):
    bystander = subprocess.Popen(["/bin/sleep", "60"], env={"POOLHOUSE_X1_MARKER": "found"})
    try:
        seen = json.loads(python(PROCESSES, tmp_path, str(os.getpid()), str(bystander.pid)).stdout)
    finally:
        bystander.kill()
        bystander.wait()
    listed = {other: {key: facts.pop(key) for key in ("listed", "named")} for other, facts in seen.items()}
    for other, facts in seen.items():
        assert listed[other]["listed"], (other, "the name, pid, parent pid and user id are the declared exposure X1, so the read must work")
        assert not any(facts.values()), (other, sorted(key for key, value in facts.items() if value))
    assert listed["bystander"]["named"]
    assert profiles.PROCESS_LIST.sysctl_prefixes == ("kern.proc.pid.",)


class Followed(Seatbelt):
    """The real Seatbelt backend that also keeps the tree of each run it starts, so a test can end it after the run."""

    def __init__(self) -> None:
        self.trees: list[process_tree.Tree] = []

    def started(self, tag: str, pid: int) -> None:
        super().started(tag, pid)
        self.trees.append(process_tree.follow(pid))


DETACH = """
import os, sys, time
if os.fork() == 0:
    os.setsid()
    open(sys.argv[1], "w").write(str(os.getpid()))
    for each in (0, 1, 2):
        os.close(each)
    time.sleep(300)
"""


def test_a_descendant_that_left_the_process_group_outlives_the_run_and_the_end_of_the_tree_ends_it(tmp_path, seatbelt):
    backend, record = Followed(), tmp_path / "pid"
    held = policy(read=[SOURCE, tmp_path], write=[tmp_path], python=True, env={"HOME": str(tmp_path)})
    done = run([os.path.realpath(sys.executable), "-I", "-c", DETACH, str(record)], held, via=backend, diagnose="never")
    deadline = time.monotonic() + 5
    while not record.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    survivor = int(record.read_text())
    try:
        assert done.ok and running(survivor), "the group kill at the end of the run does not reach a process that called setsid"
        ended = process_tree.end(backend.trees[0], stalled=10.0)
        assert ended.clean, ended
        assert not running(survivor)
    finally:
        process_tree.release(backend.trees[0])
        if running(survivor):
            os.kill(survivor, 9)


GROW = """
import json, resource
try:
    resource.setrlimit(resource.RLIMIT_AS, (100 << 20, resource.RLIM_INFINITY))
    limit = "set"
except (ValueError, OSError):
    limit = "refused"
block = bytearray(500 << 20)
for at in range(0, len(block), 4096):
    block[at] = 1
print(json.dumps({"limit": limit, "grew_to_mib": len(block) >> 20}))
"""


def test_a_process_grows_past_an_address_space_limit_so_memory_is_watched_by_the_parent_and_not_enforced_here(tmp_path, seatbelt):
    seen = json.loads(python(GROW, tmp_path).stdout)
    assert seen["grew_to_mib"] == 500, "an address space limit now holds on macOS: the design (5.2) may enforce memory in the engine"
