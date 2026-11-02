"""Maintenance drains live readers without starving or adopting stale marker files."""
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from poolhouse import home, maintenance
from poolhouse.lock import Busy


@pytest.fixture
def barrier(tmp_path, monkeypatch):
    monkeypatch.setattr(home, 'state', lambda *parts: tmp_path.joinpath(*parts))
    return tmp_path


def test_nested_reader_and_writer_verification_are_reentrant(barrier):
    with maintenance.read(), maintenance.read(), maintenance.intent() as writer:
        with pytest.raises(Busy), writer.exclusive():
            pass
        with maintenance.read():
            pass
    with maintenance.intent() as writer, writer.exclusive(), maintenance.read():
        pass


def test_writer_intent_excludes_new_threads_but_preserves_all_existing_readers(barrier):
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    def reader(index):
        with maintenance.read():
            entered[index].set()
            assert release[index].wait(5)
    threads = [threading.Thread(target=reader, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    assert all(event.wait(5) for event in entered)
    later = threading.Event()
    def late():
        with maintenance.read():
            later.set()
    newcomer = threading.Thread(target=late)
    try:
        with maintenance.intent() as writer:
            newcomer.start()
            assert not later.wait(.05)
            with maintenance.read(existing=True):
                pass  # Renew/account existing work without making a new workload eligible.
            release[0].set()
            threads[0].join(5)
            with pytest.raises(Busy), writer.exclusive():
                pass
            release[1].set()
            threads[1].join(5)
            with writer.exclusive():
                assert not later.is_set()
        assert later.wait(5)
    finally:
        for event in release:
            event.set()
        for thread in threads:
            thread.join(5)
        newcomer.join(5)
    assert not any(thread.is_alive() for thread in [*threads, newcomer])


@pytest.mark.parametrize('attack', ['file-link', 'hardlink', 'directory-link', 'public-file'])
def test_maintenance_locks_cannot_be_redirected_or_shared(barrier, attack):
    folder = barrier / 'managed-code'
    folder.mkdir(mode=0o700)
    outside = barrier / 'outside'
    outside.write_bytes(b'private')
    if attack == 'directory-link':
        folder.rmdir()
        folder.symlink_to(barrier, target_is_directory=True)
    elif attack == 'file-link':
        (folder / 'intent.lock').symlink_to(outside)
    elif attack == 'hardlink':
        os.link(outside, folder / 'intent.lock')
    else:
        (folder / 'intent.lock').write_bytes(b'')
        (folder / 'intent.lock').chmod(0o644)
    with pytest.raises((PermissionError, OSError)), maintenance.read():
        pass
    assert outside.read_bytes() == b'private'


def test_writer_process_death_releases_real_os_locks_without_marker_adoption(barrier):
    source = Path(maintenance.__file__).parents[1]
    script = (f"import sys;sys.path.insert(0,{str(source)!r});from pathlib import Path;"
              "from poolhouse import home,maintenance;"
              f"home.state=lambda *parts:Path({str(barrier)!r}).joinpath(*parts);"
              "guard=maintenance.intent();writer=guard.__enter__();"
              "turn=writer.exclusive();turn.__enter__();print('locked',flush=True);"
              "import time;time.sleep(60)")
    child = subprocess.Popen([sys.executable, '-B', '-I', '-c', script], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'locked'
        with pytest.raises(Busy), maintenance.intent():
            pass
    finally:
        child.kill()
        child.wait(timeout=5)
        child.stdout.close()
    with maintenance.intent() as writer, writer.exclusive(), maintenance.read():
        pass


def test_windows_shared_exclusive_and_unlock_flags(monkeypatch):
    from poolhouse import maintenance_windows as windows
    calls = []
    class Kernel:
        def LockFileEx(self, *args):
            calls.append((*args[:-1], args[-1]._obj.Offset))
            return True
        def UnlockFileEx(self, handle, reserved, low, high, overlapped):
            calls.append(('unlock', handle, reserved, low, high, overlapped._obj.Offset))
            return True
    monkeypatch.setattr(windows, '_kernel', Kernel)
    monkeypatch.setattr(windows, '_handle', lambda fd: fd + 100)
    assert windows.take(5, shared=True) and windows.take(6, shared=False)
    windows.release(5)
    assert calls == [(105, 1, 0, 1, 0, 1 << 30), (106, 3, 0, 1, 0, 1 << 30),
                     ('unlock', 105, 0, 1, 0, 1 << 30)]



def test_owned_child_holds_runtime_while_launching_thread_is_not_forever_nested(barrier):
    release = barrier / 'release'
    program = f"import os,time\nwhile not os.path.exists({str(release)!r}):time.sleep(.01)"
    child = maintenance.spawn(subprocess.Popen, [sys.executable, '-c', program])
    try:
        with maintenance.intent() as writer:
            with pytest.raises(Busy), writer.exclusive():
                pass
            release.touch()
            child.wait(timeout=5)
        # A real process waiter may finish a moment after wait() in this thread.
        import time
        until = time.monotonic() + 5
        while True:
            try:
                with maintenance.intent() as writer, writer.exclusive():
                    break
            except Busy:
                assert time.monotonic() < until
                time.sleep(.01)
    finally:
        release.touch()
        child.wait(timeout=5)


def test_writer_to_child_spawn_handoff_has_no_import_window(barrier):
    launched, finished = threading.Event(), threading.Event()
    def starter():
        launched.set()
        return subprocess.Popen([sys.executable, '-c', 'pass'])
    def worker():
        process = maintenance.spawn(starter)
        process.wait(timeout=5)
        finished.set()
    thread = threading.Thread(target=worker)
    with maintenance.intent() as writer, writer.exclusive():
        thread.start()
        assert not launched.wait(.05)
    assert finished.wait(5)
    thread.join(5)
    assert not thread.is_alive()


def test_denial_survives_updater_process_death_without_becoming_a_grant(barrier):
    source = Path(maintenance.__file__).parents[1]
    script = (f"import sys;sys.path.insert(0,{str(source)!r});from pathlib import Path;"
              "from poolhouse import home,maintenance;"
              f"home.state=lambda *parts:Path({str(barrier)!r}).joinpath(*parts);"
              "intent=maintenance.intent();writer=intent.__enter__();"
              "exclusive=writer.exclusive();exclusive.__enter__();"
              "maintenance.mark_unverified({'version':1});import os;os._exit(0)")
    subprocess.run([sys.executable, '-B', '-I', '-c', script], check=True)
    with pytest.raises(OSError, match='repair is pending'), maintenance.read():
        pytest.fail('A killed updater must not expose unverified managed code')
    with pytest.raises(RuntimeError, match='exclusive'):
        maintenance.mark_verified()
    with maintenance.intent() as writer, writer.exclusive():
        assert maintenance.transaction_record() == {'version': 1}
        maintenance.mark_verified()
    with maintenance.read():
        pass


def test_nonregular_denial_record_cannot_block_the_repair_reader(barrier):
    if not hasattr(os, 'mkfifo'):
        pytest.skip('FIFO proof requires POSIX')
    with maintenance.intent() as writer, writer.exclusive():
        folder, _ = maintenance._place()
        os.mkfifo(folder / 'unverified.json', 0o600)
        with pytest.raises(PermissionError, match='regular'):
            maintenance.transaction_record()
