"""How the key Poolhouse keeps passes from the macOS program that reads it to the one about to replace it.

A keychain item is read only by the code that made it, so before an executable's signature changes the old one reads the master
and passes it through a pipe, which this process never reads, to the new one, which keeps an item of its own and reads it back.
Each step runs as the program it concerns (`keystore.handoff`); nothing is deleted before a later `retire`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from poolhouse import keystore
from poolhouse.platform import start_grouped

BOOT = ("import sys; action, *paths = sys.argv[1:]; sys.path[:0] = paths; from poolhouse import keystore; "
        "raise SystemExit(keystore.handoff(action))")
WAIT_S = 90.0


class HandoverError(OSError):
    """A step of the hand-off failed for a reason a later try may not meet."""


def _paths() -> list[str]:
    """Where a bare program imports this code and its libraries from."""
    return [str(Path(__file__).resolve().parents[1])]


def _step(program: Path, action: str) -> list[str]:
    return [str(program), "-I", "-S", "-c", BOOT, action, *_paths()]


def hand_over(old: Path, new: Path) -> bool:
    """Give the master from ``old`` to ``new``; False when ``old`` cannot read it, which no retry changes. Raises
    `HandoverError` when a step fails for another reason, and then ``old`` still reads what it read."""
    started: list[subprocess.Popen] = []
    try:
        giver = start_grouped(_step(old, "give"), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        started.append(giver)
        taker = start_grouped(_step(new, "take"), stdin=giver.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        started.append(taker)
        giver.stdout.close()  # the pipe is between the two
        taken = taker.communicate(timeout=WAIT_S)[1]
        giver.wait(timeout=WAIT_S)
        given = giver.stderr.read()
    except (OSError, subprocess.SubprocessError) as exc:
        raise HandoverError(f"the key could not be handed on: {exc}") from exc
    finally:
        for one in started:
            if one.poll() is None:
                one.kill()
            one.wait()
            for stream in (one.stdout, one.stderr):
                if stream is not None:
                    stream.close()
    if giver.returncode == keystore.CANNOT_READ:
        return False
    if giver.returncode or taker.returncode:
        raise HandoverError(f"the key could not be handed on: {(given + taken).decode('utf-8', 'replace').strip()[-400:]}")
    return True


def _runs(program: Path, action: str) -> bool:
    try:
        return subprocess.run(_step(program, action), stdin=subprocess.DEVNULL, capture_output=True, timeout=WAIT_S,
                              check=False).returncode == keystore.DONE
    except (OSError, subprocess.SubprocessError):
        return False


def reads(program: Path) -> bool:
    """Whether ``program`` reads, with no prompt, the master last handed on."""
    return _runs(program, "check")


def retire(program: Path) -> bool:
    """Have ``program`` delete the items of every signature but its own and the one before; whether it did."""
    return _runs(program, "retire")
