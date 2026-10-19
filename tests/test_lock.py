"""One runner at a time, so nobody hand-writes a waiter again.

The shell loop this replaces could not work: `until ! pgrep -f "...bench"` matches the
waiting shell's own command line, so the condition never goes false. Two sat spinning for an
afternoon and the draft-head comparison queued behind them never ran -- silently, with its
log file never created.
"""

import os
import subprocess
import sys
import textwrap

import pytest

from poolhouse.lock import Busy, held_by, only_one, release, take


def test_a_second_holder_is_refused_rather_than_allowed_to_overlap(tmp_path):
    with only_one(tmp_path / "l", wait=False), pytest.raises(Busy) as why, \
            only_one(tmp_path / "l", wait=False):
        raise AssertionError("two runs held the same lock at once")
    assert str(os.getpid()) in str(why.value), "says who has it, for a stalled machine"


def test_a_refusal_names_what_the_holder_is_doing(tmp_path):
    with only_one(tmp_path / "l", note="chat_quality mlx"), pytest.raises(Busy) as why, \
            only_one(tmp_path / "l", wait=False):
        pass
    assert "chat_quality mlx" in str(why.value)
    assert held_by(tmp_path / "l") == ""


def test_the_lock_is_released_when_the_block_ends(tmp_path):
    with only_one(tmp_path / "l"):
        pass
    with only_one(tmp_path / "l", wait=False):
        pass


def test_it_is_released_even_when_the_run_raises(tmp_path):
    with pytest.raises(ValueError), only_one(tmp_path / "l"):
        raise ValueError("a run that failed still has to let the next one in")
    with only_one(tmp_path / "l", wait=False):
        pass


def test_waiting_is_announced_rather_than_silent(tmp_path):
    """A wait nobody can see is indistinguishable from a hang -- which is exactly how the
    stuck waiters read."""
    said = []
    other = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import time
            from poolhouse.lock import only_one
            with only_one({str(tmp_path / 'l')!r}):
                print("held", flush=True)
                time.sleep(1.5)
        """)], stdout=subprocess.PIPE, text=True)
    try:
        assert other.stdout.readline().strip() == "held"
        with only_one(tmp_path / "l", timeout=10, announce=said.append):
            pass
    finally:
        other.wait(timeout=10)
    assert said and "waiting for" in said[0]


def test_a_bounded_wait_gives_up_and_says_so(tmp_path):
    with only_one(tmp_path / "l"), pytest.raises(Busy, match="still held"), \
            only_one(tmp_path / "l", timeout=0.2, announce=lambda _: None):
        pass


def test_only_the_measuring_subcommands_take_it():
    """`show` reads the store and touches no GPU; making it queue behind a run would be a new
    way to hang."""
    from poolhouse.bench import MEASURING

    assert set(MEASURING) == {"run", "sweep", "drafts", "concurrent", "extract", "speed"}
    assert "show" not in MEASURING and "prepare" not in MEASURING


def test_a_refused_attempt_leaves_the_holder_named(tmp_path):
    """The finally ran on the refused attempt too and truncated the holder's pid, so the
    next asker saw "held by somebody". Mutation: drop the `taken` guard."""
    import os

    from poolhouse.lock import Busy, only_one

    path = tmp_path / "measuring.lock"
    with only_one(path, wait=False):
        try:
            with only_one(path, wait=False):
                raise AssertionError("a second holder was allowed")
        except Busy as why:
            assert str(os.getpid()) in str(why)
        # the refusal must not have blanked the record
        assert path.read_text().strip().startswith("pid"), path.read_text()
        try:
            with only_one(path, wait=False):
                pass
        except Busy as why:
            assert "somebody" not in str(why), str(why)


def test_a_lock_taken_on_an_open_file_excludes_another_process(tmp_path):
    path = tmp_path / "store.lock"
    other = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import os, sys, time
            from poolhouse.lock import take
            handle = os.open({str(path)!r}, os.O_RDWR | os.O_CREAT, 0o644)
            print("held" if take(handle) else "refused", flush=True)
            time.sleep(3)
        """)], stdout=subprocess.PIPE, text=True)
    try:
        assert other.stdout.readline().strip() == "held"
        handle = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            assert take(handle) is False, "two processes held the same file at once"
        finally:
            os.close(handle)
    finally:
        other.kill()
        other.wait(timeout=10)
    handle = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        assert take(handle) is True, "a dead holder keeps nobody out"
        release(handle)
    finally:
        os.close(handle)


def test_asking_who_holds_it_neither_waits_nor_blanks_the_record(tmp_path):
    path = tmp_path / "measuring.lock"
    assert held_by(path) == ""
    with only_one(path, wait=False):
        assert held_by(path) == f"pid {os.getpid()}"
        assert held_by(path) == f"pid {os.getpid()}", "asking twice must not blank it"
    assert held_by(path) == ""


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks are POSIX")
@pytest.mark.parametrize("target_exists", [True, False])
def test_a_lock_file_that_is_a_symlink_is_refused_and_its_target_left_alone(tmp_path, target_exists):
    target = tmp_path / "precious"
    if target_exists:
        target.write_text("precious")
    link = tmp_path / "runner.lock"
    link.symlink_to(target)
    with pytest.raises(OSError), only_one(link, wait=False):
        pytest.fail("a symlinked lock file was taken")
    assert held_by(link) == ""
    assert (target.read_text() == "precious") if target_exists else not target.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="account ids are POSIX")
def test_a_lock_file_owned_by_another_account_is_refused_and_left_alone(tmp_path, monkeypatch):
    path = tmp_path / "runner.lock"
    path.write_text("someone else's")
    monkeypatch.setattr(os, "getuid", lambda: path.stat().st_uid + 1)
    with pytest.raises(PermissionError), only_one(path, wait=False):
        pytest.fail("a lock file of another account was taken")
    assert held_by(path) == "" and path.read_text() == "someone else's"


def test_asking_who_holds_a_lock_that_does_not_exist_creates_nothing(tmp_path):
    assert held_by(tmp_path / "state" / "runtimes" / "x.lock") == ""
    assert not (tmp_path / "state").exists()


def test_a_refusal_shows_the_holders_text_as_printable_characters_only(tmp_path):
    path = tmp_path / "l"
    with only_one(path, note="\x1b[31mred\x1b[0m\nnext"), pytest.raises(Busy) as why, only_one(path, wait=False):
        pytest.fail("two runs held the same lock at once")
    assert str(why.value).isprintable() and f"pid {os.getpid()} ?[31mred?[0m?next" in str(why.value)
    with only_one(path, note="\x1b[31mred"):
        assert held_by(path) == f"pid {os.getpid()} ?[31mred"
