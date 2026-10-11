"""The rig's end of `driver.py`: one long-lived process on a device, and the commands the rig runs through it.

A node lets a session token spend a grant only from a process that shares the lineage that registered it, and a command started with
`docker exec` or `wsl.exe` shares none with the next one (driver.py says why). Every command of such a device therefore runs as a child of
this one process, the way every command of a person's terminal is a child of its shell.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import tempfile
import threading


class Driver:
    """``argv`` started with pipes: `driver.py` on a device, asked to run commands one at a time."""

    def __init__(self, argv: list[str], env: dict[str, str] | None = None) -> None:
        self.argv, self.lock, self.count = argv, threading.Lock(), 0
        self.errors = tempfile.TemporaryFile()  # noqa: SIM115 - kept open for the life of the driver, closed in `close`
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors, env=env)

    def said(self) -> str:
        """What the driver wrote on its error stream so far."""
        self.errors.seek(0)
        return self.errors.read().decode(errors="replace")[-1500:]

    def reply(self, number: int) -> dict:
        """The answer to request ``number``: lines that are not JSON (a launcher's warnings) and the driver's ready line are skipped."""
        assert self.proc.stdout is not None
        while line := self.proc.stdout.readline():
            try:
                got = json.loads(line)
            except ValueError:
                continue
            if isinstance(got, dict) and got.get("id") == number:
                return got
        raise RuntimeError(f"the driver of {' '.join(self.argv[-3:])} ended (exit {self.proc.poll()}) before it answered:\n{self.said()}")

    def run(self, argv: list[str], env: dict[str, str], timeout: float) -> subprocess.CompletedProcess[str]:
        """``argv`` run on the device as a child of the driver, with ``env`` added to its environment."""
        assert self.proc.stdin is not None
        with self.lock:
            self.count += 1
            request = {"id": self.count, "argv": argv, "env": env, "timeout": timeout}
            try:
                self.proc.stdin.write(json.dumps(request).encode() + b"\n")
                self.proc.stdin.flush()
            except OSError as exc:
                raise RuntimeError(f"the driver of {' '.join(self.argv[-3:])} is gone ({exc}):\n{self.said()}") from None
            got = self.reply(self.count)
        return subprocess.CompletedProcess(argv, got["rc"], got["out"], got["err"])

    def close(self) -> None:
        """End the driver: its input closes, and it is killed when it does not end."""
        if self.proc.stdin:
            with contextlib.suppress(OSError):
                self.proc.stdin.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
        for stream in (self.proc.stdout, self.errors):
            if stream:
                stream.close()

