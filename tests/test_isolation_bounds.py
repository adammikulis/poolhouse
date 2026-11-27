"""The resource bounds of a confinement, passed on purpose (G11; docs/design-isolated-launcher.md 5.2, 13.2 and A22).

The confinement is real: the stage, both bridges, the proof, the Seatbelt profile, the rlimits and the parent's watcher. The program in the
harness's place is a marked script (NOT A MODEL, NOT A HARNESS) that writes one plain source file and then burns CPU, writes past a file bound,
exhausts descriptors, fills the disk, grows in memory or forks without end. A bound that is under test is made small enough to pass in seconds; the
others stay the machine's. A program that is not ended stays `LINGER` seconds and then says so.
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest

import tests.test_isolation_confinement as choke
from poolhouse import isolation
from poolhouse.isolation import bounds
from poolhouse.isolation.serving import IDLE_SECONDS

pytest_plugins = ["tests.sandbox_kit"]
rig = choke.rig
OUTLIVED = "NOT ENDED"
LINGER = 15
BURN = "t = time.time() + 15; all(time.time() < t for _ in itertools.count())"

CPU = {
    "the kernel's signal ends a child": f"subprocess.run([sys.executable, '-I', '-c', 'import itertools, time; {BURN}'])",
    "a process that ignores the signal": f"signal.signal(signal.SIGXCPU, signal.SIG_IGN)\n{BURN}",
}
FILE = {
    "a process that ignores the signal": """
        with open('src/big.bin', 'wb', buffering=0) as out:
            try:
                for _ in range(64):
                    out.write(bytes(65536))
            except OSError as error:
                print('refused', error.errno, flush=True)
        """,
    "a child the kernel ends, whose file is gone at once": """
        child = ("import signal, time; signal.signal(signal.SIGXFSZ, signal.SIG_DFL); time.sleep(0.3); "
                 "out = open('src/big.bin', 'wb', 0); [out.write(bytes(65536)) for _ in range(64)]")
        subprocess.run([sys.executable, '-I', '-c', child])
        os.unlink('src/big.bin')
        """,
}
DESCRIPTORS = """
    held = []
    try:
        while True:
            held.append(os.dup(1))
    except OSError as error:
        print('refused', error.errno, flush=True)
    """
DISK = """
    for index in range(64):
        with open(f'src/fill{index}.bin', 'wb') as out:
            out.write(bytes(1 << 20))
        time.sleep(0.05)
    """
MEMORY = """
    held = []
    for _ in range(48):
        held.append(os.urandom(16 << 20))
        time.sleep(0.05)
    """
FLOOD = """
    children = []
    try:
        for _ in range(300):
            pid = os.fork()
            if not pid:
                time.sleep(10)
                os._exit(0)
            children.append(pid)
    except OSError as error:
        print('refused', error.errno, 'after', len(children), flush=True)
    for pid in children:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    sys.exit(0)
    """
TOLD = """
    print('told', *(resource.getrlimit(which)[0] for which in (resource.RLIMIT_CPU, resource.RLIMIT_FSIZE, resource.RLIMIT_NOFILE)), flush=True)
    sys.exit(0)
    """


def program(tmp_path: Path, body: str) -> Path:
    """The script in the harness's place: it writes one plain source file, then does ``body``. NOT A MODEL, NOT A HARNESS."""
    script = tmp_path / "bound-NOT-A-HARNESS.sh"
    script.write_text("#!/bin/sh\nprintf 'def kept():\\n    return 1\\n' > src/kept.py\n"
                      f"exec {os.path.realpath(sys.executable)} -I - <<'PY'\nimport itertools, os, resource, signal, subprocess, sys, time\n"
                      f"{textwrap.dedent(body)}\ntime.sleep({LINGER})\nprint('{OUTLIVED}', flush=True)\nPY\n")
    script.chmod(0o755)
    return script


def launched(rig, tmp_path: Path, body: str, **small: int):
    """One session of the program under the machine's bounds with ``small`` in place of those it names, run to its end."""
    made = replace(bounds.derive(str(tmp_path)), **small)
    with isolation.prepare(replace(choke.plan(rig, program(tmp_path, body)), bounds=made)) as confinement:
        process = confinement.popen()
        out, err = process.communicate(timeout=60)
    return confinement, process.returncode, out.decode() + err.decode()


def assert_ended(rig, run, why: str) -> None:
    """The bound ended the session at once: a plain reason in the result and to the lead, nothing published and the patch kept."""
    confinement, status, said = run
    reason = confinement.passed()
    assert reason.startswith(bounds.PASSED) and why in reason, (reason, status, said)
    assert status != 0 and OUTLIVED not in said, said
    turn = confinement.turn
    assert turn.applied == () and not (rig.project / "src" / "kept.py").exists()
    assert turn.patch is not None and b"def kept" in Path(turn.patch.path).read_bytes()
    assert reason in turn.text and confinement.said() == [turn.text]
    to, _kind, subject, body = rig.sink.posts[-1]
    assert (to, subject) == ("lead", "isolation.report") and reason in body, body


@pytest.mark.parametrize("how", sorted(CPU))
def test_a_cpu_burn_ends_the_session(rig, tmp_path, how):
    assert_ended(rig, launched(rig, tmp_path, CPU[how], cpu_seconds=2), "CPU time bound of 2 seconds")


@pytest.mark.parametrize("how", sorted(FILE))
def test_a_file_past_the_size_limit_ends_the_session(rig, tmp_path, how):
    assert_ended(rig, launched(rig, tmp_path, FILE[how], file_bytes=1 << 20), "size bound of 1048576 bytes")


def test_descriptor_exhaustion_is_refused_by_the_kernel_and_ends_the_session(rig, tmp_path):
    run = launched(rig, tmp_path, DESCRIPTORS, open_files=64)
    assert "refused 24" in run[2], run[2]
    assert_ended(rig, run, "open descriptors, and the bound is 64")


def test_a_fork_flood_is_refused_by_the_kernel_and_a_disk_fill_and_memory_growth_end_the_session(rig, tmp_path, monkeypatch):
    assert_ended(rig, launched(rig, tmp_path, DISK, disk_bytes=24 << 20), "past the disk bound of 25165824 bytes")
    assert_ended(rig, launched(rig, tmp_path, MEMORY, memory_bytes=256 << 20), "bytes of memory, past the bound of 268435456 bytes")
    monkeypatch.setattr(bounds, "fair_share", lambda: 40)
    confinement, status, said = launched(rig, tmp_path, FLOOD)
    assert "refused 35 after" in said and status == 0, said
    assert confinement.passed() == "" and "src/kept.py" in confinement.turn.applied, "a session inside its bounds still publishes"


def test_each_bound_is_the_machines_figure_and_the_kernel_is_told_it(rig, tmp_path):
    with isolation.prepare(choke.plan(rig, program(tmp_path, TOLD))) as confinement:
        out = confinement.popen().communicate(timeout=60)[0].decode()
    told, watched = confinement.profile.policy.limits, confinement.made.watch.bounds
    assert f"told {told.cpu_seconds} {told.file_bytes} {told.open_files}" in out, out
    assert told.cpu_seconds == int(IDLE_SECONDS) * os.cpu_count() // 2
    assert told.file_bytes == watched.disk_bytes <= shutil.disk_usage(tmp_path).total // (bounds.COPIES * 2)
    assert 0 < told.open_files <= min(bounds._sysctl("kern.maxfilesperproc"), bounds._sysctl("kern.maxfiles") // 2)
    assert 0 < watched.memory_bytes <= os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 2
