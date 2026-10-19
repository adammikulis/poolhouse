"""A termination handler does not outlive the code or the test that installed it."""

from __future__ import annotations

import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from signal_guard import handlers_restored

from poolhouse import platform as plat

TESTS = Path(__file__).resolve().parent

LEAK = """\
import signal

FIRST = signal.getsignal(signal.SIGTERM)


def test_one_leaves_a_handler_that_interrupts():
    def interrupt(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)


def test_the_next_finds_the_handler_it_started_with():
    assert signal.getsignal(signal.SIGTERM) is FIRST
"""


def interrupt(*_: object) -> None:
    raise KeyboardInterrupt


def run_both(folder: Path, conftest: str) -> subprocess.CompletedProcess:
    folder.mkdir()
    (folder / "test_leak.py").write_text(LEAK, encoding="utf-8")
    (folder / "conftest.py").write_text(conftest, encoding="utf-8")
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "0"],
                          cwd=folder, capture_output=True, text=True, timeout=120)


def test_a_leaked_handler_reaches_the_next_test_without_the_fixture(tmp_path):
    done = run_both(tmp_path / "bare", "")
    assert done.returncode == 1 and "1 failed, 1 passed" in done.stdout


def test_the_fixture_restores_the_handler_a_test_leaves(tmp_path):
    conftest = (f"import sys\nsys.path.insert(0, {str(TESTS)!r})\n"
                "from signal_guard import signal_handlers_are_restored  # noqa: F401\n")
    done = run_both(tmp_path / "guarded", conftest)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_suite_imports_the_fixture():
    conftest = (TESTS / "conftest.py").read_text(encoding="utf-8")
    assert "from signal_guard import signal_handlers_are_restored" in conftest


def test_a_handler_set_inside_the_block_is_put_back():
    before = signal.getsignal(signal.SIGTERM)
    with handlers_restored():
        signal.signal(signal.SIGTERM, interrupt)
        assert signal.getsignal(signal.SIGTERM) is interrupt
    assert signal.getsignal(signal.SIGTERM) == before


def test_the_quit_hook_is_held_for_the_block_and_given_back():
    before = signal.getsignal(signal.SIGTERM)
    with plat.quitting(interrupt) as hooked:
        assert hooked == [signal.SIGTERM] and signal.getsignal(signal.SIGTERM) is interrupt
    assert signal.getsignal(signal.SIGTERM) == before


def test_the_quit_hook_is_given_back_when_the_block_raises():
    before = signal.getsignal(signal.SIGTERM)
    with pytest.raises(ValueError), plat.quitting(interrupt):
        raise ValueError
    assert signal.getsignal(signal.SIGTERM) == before


def test_the_quit_hook_from_a_worker_thread_hooks_nothing_and_changes_nothing():
    before = signal.getsignal(signal.SIGTERM)
    seen: list = []

    def worker():
        with plat.quitting(interrupt) as hooked:
            seen.append(hooked)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert seen == [[]] and signal.getsignal(signal.SIGTERM) == before


def test_the_daemon_gives_the_termination_handler_back(monkeypatch, tmp_path):
    from poolhouse.fleet import daemon

    class Stop(Exception):
        pass

    held: list = []

    def start(*_args: object, **_kwargs: object) -> None:
        held.append(signal.getsignal(signal.SIGTERM))
        raise Stop

    monkeypatch.setattr(daemon.sentinel, "armed", lambda: None)
    monkeypatch.setattr(daemon, "lease_file", lambda: tmp_path / "servers.json")
    monkeypatch.setattr(daemon.canaries, "lease_file_targets", lambda _path: ())
    monkeypatch.setattr(daemon.guarded, "start", start)
    runtime = object.__new__(daemon.DaemonRuntime)
    before = signal.getsignal(signal.SIGTERM)
    with pytest.raises(Stop):
        runtime.run()
    assert held == [runtime._quit], "the daemon hooked SIGTERM while it ran"
    assert signal.getsignal(signal.SIGTERM) == before
