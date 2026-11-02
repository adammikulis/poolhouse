"""Process control is not optional: a missing psutil is an import error, never a no-op."""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _blocked(module: str, body: str) -> subprocess.CompletedProcess:
    program = (
        "import sys\n"
        "sys.modules['psutil'] = None\n"
        f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
        f"import {module}\n"
        f"{body}\n"
    )
    return subprocess.run([sys.executable, "-c", program], capture_output=True, text=True)


def test_process_control_refuses_to_import_without_psutil():
    done = _blocked("poolhouse.serve.process", "print('imported')")
    assert done.returncode != 0 and "psutil" in done.stderr
    assert "imported" not in done.stdout


def test_port_scanning_refuses_to_run_without_psutil():
    done = _blocked("poolhouse.serve.ports", "print(poolhouse.serve.ports.server_pids_on_port(1))")
    assert done.returncode != 0 and "psutil" in done.stderr
    assert done.stdout.strip() != "[]"


def test_a_live_child_is_killed_with_its_own_children():
    parent = subprocess.Popen([sys.executable, "-c",
                               "import subprocess,sys,time;"
                               "subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
                               "time.sleep(60)"])
    try:
        import time

        import psutil

        from poolhouse.serve.process import kill_process_tree
        deadline = time.monotonic() + 10
        while not psutil.Process(parent.pid).children() and time.monotonic() < deadline:
            time.sleep(0.05)
        acted = kill_process_tree(parent.pid, grace_s=5)
        assert parent.pid in acted and len(acted) == 2
        parent.wait(timeout=10)
    finally:
        if parent.poll() is None:
            parent.kill()


def _owned_family(receipt):
    import time

    child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
    receipt.write_text(str(child.pid))
    time.sleep(60)


def test_multiprocessing_owner_reaps_its_root_and_descendants_are_stopped(tmp_path, monkeypatch):
    import multiprocessing
    import time

    import psutil

    from poolhouse.serve.process import kill_process_tree, pid_exists

    receipt = tmp_path / 'child.pid'
    owner = multiprocessing.get_context('spawn').Process(target=_owned_family, args=(receipt,))
    owner.start()
    try:
        deadline = time.monotonic() + 10
        while not receipt.exists():
            assert time.monotonic() < deadline
            time.sleep(.02)
        child_pid = int(receipt.read_text())
        birth = psutil.Process(owner.pid).create_time()
        original = psutil.wait_procs
        waited = []

        def wait_children(processes, **options):
            assert owner.pid not in [p.pid for p in processes], 'the multiprocessing owner must reap its root'
            waited.extend(p.pid for p in processes)
            return original(processes, **options)

        monkeypatch.setattr(psutil, 'wait_procs', wait_children)
        acted = kill_process_tree(owner.pid, grace_s=.1, reap_parent=False, expect_start=birth)
        owner.join(timeout=3)
        assert owner.exitcode is not None and not owner.is_alive()
        assert set(acted) == {owner.pid, child_pid} and waited == [child_pid]
        assert not pid_exists(child_pid)
    finally:
        if owner.is_alive():
            owner.kill()
        owner.join(timeout=3)


def test_wrong_owned_process_birth_never_signals(tmp_path, monkeypatch):
    import multiprocessing
    import time

    import psutil
    import pytest

    from poolhouse.serve.process import kill_process_tree

    owner = multiprocessing.get_context('spawn').Process(target=time.sleep, args=(60,))
    owner.start()
    try:
        birth = psutil.Process(owner.pid).create_time()
        monkeypatch.setattr(psutil.Process, 'terminate', lambda _: pytest.fail('mismatched identity must not signal'))
        for wrong in (None, 0, float('nan'), float('inf'), birth + 1):
            with pytest.raises(ProcessLookupError, match='creation identity'):
                kill_process_tree(owner.pid, reap_parent=False, expect_start=wrong)
        assert owner.is_alive()
    finally:
        owner.kill()
        owner.join(timeout=3)


class _Process:
    """A process as `psutil` hands one over: its attributes read on demand, or an error for each."""

    def __init__(self, pid, attributes=None, error=None):
        self.pid, self.attributes, self.error = pid, attributes, error

    def as_dict(self, attrs=None, ad_value=None):
        if self.error:
            raise self.error
        return {name: self.attributes.get(name, ad_value) for name in attrs}


def _unreadable_among_readable(monkeypatch):
    import psutil

    from poolhouse.serve import process

    rows = [_Process(5, error=PermissionError("sysctl KERN_PROCARGS2")), _Process(6, error=SystemError("proc_cmdline set an exception")),
            _Process(7, error=psutil.NoSuchProcess(7)),
            _Process(8, {"pid": 8, "create_time": 12.5, "cmdline": ["python", "-m", "poolhouse.x"], "exe": "/opt/x/bin/python", "cwd": "/opt/x/run"}),
            _Process(9, {"pid": 9, "create_time": 13.5, "cmdline": [], "exe": None, "cwd": None})]

    def process_iter(attrs=None, ad_value=None):
        if attrs:
            raise PermissionError("sysctl KERN_PROCARGS2")
        yield from rows

    monkeypatch.setattr(process.psutil, "process_iter", process_iter)
    return process


def test_a_process_that_cannot_be_read_is_left_out_of_the_command_lines(monkeypatch):
    process = _unreadable_among_readable(monkeypatch)
    assert process.command_lines() == [(8, 12.5, ("python", "-m", "poolhouse.x"))]


def test_a_process_that_cannot_be_read_is_not_among_those_running_within_a_folder(monkeypatch):
    process = _unreadable_among_readable(monkeypatch)
    assert process.running_within(Path("/opt/x")) == [8] and process.running_within(Path("/opt/y")) == []


def test_unknown_process_attributes_remain_visible_to_conservative_idle_checks(monkeypatch):
    import psutil

    from poolhouse.serve import process

    class Unreadable:
        def __init__(self, error):
            self.error = error
        def as_dict(self, attrs):
            raise self.error
    attrs = ['pid', 'cmdline', 'username']
    for error in (psutil.AccessDenied(123), SystemError('unavailable'), OSError('unavailable')):
        current = Unreadable(error)
        monkeypatch.setattr(psutil, 'process_iter', lambda current=current: [current])
        assert process.readable(attrs) == []
        assert process.readable(attrs, uncertain=True) == [(current, dict.fromkeys(attrs))]
    monkeypatch.setattr(psutil, 'process_iter', lambda: [Unreadable(psutil.NoSuchProcess(123))])
    assert process.readable(attrs, uncertain=True) == []
