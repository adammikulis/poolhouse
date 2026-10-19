"""The machine-wide lock around the tests that read Seatbelt refusals and the ones that make the kernel cut a report off."""

from __future__ import annotations

import fcntl
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import pytest

from tests import sandbox_kit
from tests.sandbox_kit import kernel_log

FAILED = pytest.fail.Exception
TESTS = Path(__file__).parent
sys.path.insert(0, str(TESTS.parent / "scripts"))

import testreuse_key  # noqa: E402


@pytest.fixture
def short_wait(monkeypatch):
    """A lock wait of a fraction of a second."""
    monkeypatch.setattr(sandbox_kit, "LOCK_TIMEOUT_S", 0.4)


@pytest.fixture
def private_lock(short_wait, monkeypatch, tmp_path):
    """Lock files of this test's own, so that it does not hold up a test run that is reading refusals."""
    monkeypatch.setattr(sandbox_kit, "LOCK_BASE", str(tmp_path / "log"))


class Holder:
    """Takes lock files in a way ``kernel_log`` does not know of, as a test run in another process would."""

    def __init__(self) -> None:
        self.fds: list[int] = []

    def take(self, name: str, mode: int) -> None:
        fd = os.open(f"{sandbox_kit.LOCK_BASE}.{name}", os.O_RDWR | os.O_CREAT, 0o600)
        self.fds.append(fd)
        fcntl.flock(fd, mode)

    def release(self) -> None:
        while self.fds:
            os.close(self.fds.pop())


@pytest.fixture
def holder():
    """A `Holder` that lets go of everything it took when the test ends."""
    held = Holder()
    yield held
    held.release()


def test_the_lock_is_at_one_path_on_the_machine_whatever_the_temporary_directory_is(short_wait, holder, monkeypatch, tmp_path):
    assert sandbox_kit.LOCK_BASE.startswith("/private/tmp/")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    holder.take("lock", fcntl.LOCK_SH)
    with pytest.raises(FAILED, match="waited"), kernel_log(alone=True):
        pass


def test_a_test_that_wants_to_be_alone_fails_when_a_reader_holds_the_lock_for_too_long(private_lock, holder):
    holder.take("lock", fcntl.LOCK_SH)
    began = time.monotonic()
    with pytest.raises(FAILED, match=r"waited 0\.4 s.*another test run"), kernel_log(alone=True):
        pass
    assert time.monotonic() - began < 5


def test_a_reader_fails_when_a_test_that_wants_to_be_alone_holds_the_lock_for_too_long(private_lock, holder):
    holder.take("gate", fcntl.LOCK_EX)
    with pytest.raises(FAILED, match="waited"), kernel_log():
        pass


def test_readers_hold_the_lock_together(private_lock, holder):
    holder.take("lock", fcntl.LOCK_SH)
    with kernel_log():
        pass


def test_a_wait_that_failed_leaves_the_lock_free_for_the_next_test(private_lock, holder):
    holder.take("lock", fcntl.LOCK_SH)
    with pytest.raises(FAILED), kernel_log(alone=True):
        pass
    holder.release()
    with kernel_log(alone=True):
        pass


@pytest.mark.parametrize(("first", "second"), [(False, True), (True, False), (False, False), (True, True)],
                         ids=["reader-then-alone", "alone-then-reader", "reader-twice", "alone-twice"])
def test_a_test_that_takes_the_lock_twice_fails_instead_of_waiting_for_itself(first, second, private_lock):
    with kernel_log(first), pytest.raises(FAILED, match="already holds"), kernel_log(second):
        pass
    with kernel_log(True):
        pass


def test_a_lock_file_that_is_a_symbolic_link_fails_the_test_and_leaves_what_it_points_to_alone(private_lock, tmp_path):
    precious = tmp_path / "precious"
    precious.write_text("keep")
    Path(f"{sandbox_kit.LOCK_BASE}.lock").symlink_to(precious)
    with pytest.raises(FAILED, match="symbolic link"), kernel_log():
        pass
    assert precious.read_text() == "keep"


def test_a_lock_file_that_is_not_a_regular_file_fails_the_test(private_lock):
    os.mkfifo(f"{sandbox_kit.LOCK_BASE}.gate")
    with pytest.raises(FAILED, match="regular file"), kernel_log():
        pass


def test_a_lock_file_that_another_user_made_fails_the_test(private_lock, monkeypatch):
    with kernel_log():
        pass
    monkeypatch.setattr(sandbox_kit, "_uid", lambda: os.getuid() + 1)
    with pytest.raises(FAILED, match="owned by another user"), kernel_log():
        pass


def test_the_lock_files_are_made_for_their_owner_alone(private_lock):
    with kernel_log():
        pass
    assert {Path(f"{sandbox_kit.LOCK_BASE}.{name}").stat().st_mode & 0o777 for name in ("gate", "lock")} == {0o600}


def test_no_module_but_the_kit_defines_a_seatbelt_fixture_of_its_own():
    own = [path.name for path in TESTS.glob("*.py")
           if path.name not in {"sandbox_kit.py", Path(__file__).name} and re.search(r"def seatbelt(_alone)?\(", path.read_text())]
    assert own == []


def test_the_sandbox_tests_use_no_call_the_test_runner_cannot_observe():
    files = [*TESTS.glob("test_sandbox_*.py"), TESTS / "sandbox_kit.py", TESTS / "test_sentinel_sandbox_owner.py"]
    unseen = {path.name: found[0] for path in files if (found := testreuse_key.UNSEEN.findall(path.read_text()))}
    assert unseen == {}
