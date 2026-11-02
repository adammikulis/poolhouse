"""A process-held lock on a repository's runtime cutover lock file, for tests that need another holder."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from poolhouse import lock


class CutoverLock:
    """An exclusive lock on the runtime cutover lock file of a checkout, held by the process until released."""

    def __init__(self, root: Path) -> None:
        common = subprocess.run(["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, check=True).stdout.strip()
        self.path = Path(common) / "runtime-cutover.lock"
        self.fd: int | None = None

    def acquire(self) -> bool:
        """Take the lock; false when another process holds it."""
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        if not lock.take(fd):
            os.close(fd)
            return False
        os.write(fd, f"pid {os.getpid()}".encode())
        self.fd = fd
        return True

    def release(self) -> None:
        """Give the lock up."""
        if self.fd is not None:
            os.ftruncate(self.fd, 0)
            lock.release(self.fd)
            os.close(self.fd)
            self.fd = None
