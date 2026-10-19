"""What Seatbelt refused, read from one ``log stream`` started before the first confined command.

The kernel's refusal reports carry the tag of the profile that refused and are not kept for a
later ``log show``, so the stream runs from the first confined command to the end of the process.
``denials`` is None whenever the stream cannot say what a run was refused: it never started, did
not answer, ended or restarted after the run was watched, or never watched the run.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import logging
import os
import re
import secrets
import subprocess
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field

from poolhouse.platform import start_grouped

__all__ = ["DenialLog", "parse", "shared"]

logger = logging.getLogger("poolhouse.sandbox")

LOG = "/usr/bin/log"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
PROBE_PROGRAM = "/usr/bin/true"
PREFIX = "poolhouse-sandbox:"
PROBE = "probe~"
PREDICATE = f'eventMessage CONTAINS "{PREFIX}"'
KERNEL = "/kernel"
SANDBOX_KEXT = "/Sandbox.kext/Contents/MacOS/Sandbox"
NOT_STREAMED = -1

START_SECONDS = 5.0
SETTLE_SECONDS = 5.0
RETRY_SECONDS = 60.0
KEEP_TAGS = 256
KEEP_PER_TAG = 1000

DENIAL = re.compile(r"Sandbox: (?P<proc>[^(]+)\((?P<pid>\d+)\) deny\(\d+\) (?P<op>[\w*-]+)\s*(?P<target>.*)",
                    re.DOTALL)
NOISE = re.compile(
    r"\.CFUserTextEncoding$|/\.?GlobalPreferences[^/]*\.plist$|^com\.apple\.SystemConfiguration\.configd$"
    r"|^/Users/[^/]+$|^com\.apple\.analyticsd$|^com\.apple\.system\.opendirectoryd\.membership$"
    r"|^net\.routetable\.|^/private/etc/localtime$|^/Library/Preferences/com\.apple\.networkd\.plist$"
    r"|^/Library/Preferences/Logging/com\.apple\.diagnosticd\.filter\.plist$|^com\.apple\.diagnosticd$")
"""Targets every confined process probes at start-up and survives being refused."""


def parse(line: str) -> tuple[str, dict[str, str]] | None:
    """``(tag, refusal)`` for one ndjson event of the stream; None for any line that is not a refusal.

    Only an event the kernel's Sandbox extension sent counts, so another process's log line cannot
    pose as a refusal. The profile puts its tag on the last line of the message, so a target holding
    a newline cannot name the tag of another run.
    """
    if not line.startswith("{"):
        return None
    try:
        data = json.loads(line)
        message = data.get("eventMessage")
        sender = data.get("senderImagePath")
        process = data.get("processImagePath")
    except (ValueError, AttributeError):
        return None
    if not isinstance(message, str) or process != KERNEL or not (isinstance(sender, str) and sender.endswith(SANDBOX_KEXT)):
        return None
    body, _, last = message.rpartition("\n")
    found = DENIAL.fullmatch(body)
    if found is None or not last.startswith(PREFIX):
        return None
    return last[len(PREFIX):], {"process": found["proc"].strip(), "operation": found["op"],
                                "target": found["target"].strip()}


@dataclass(slots=True)
class _Run:
    """One watched run: the stream it was watched on, what that stream carried for it, and whether it has ended."""

    generation: int
    entries: list[dict[str, str]] = field(default_factory=list)
    ended: bool = False


class DenialLog:
    """A ``log stream`` of every refusal tagged by a poolhouse profile, and the refusals it carried per watched run."""

    def __init__(self) -> None:
        self._start_lock = threading.Lock()
        self._changed = threading.Condition()
        self._runs: OrderedDict[str, _Run] = OrderedDict()
        self._probes: dict[str, bool] = {}
        self._said: deque[str] = deque(maxlen=3)
        self._proc: subprocess.Popen[str] | None = None
        self._owner = 0
        self._generation = 0
        self._failed_at: float | None = None
        self._exit_hook = False

    def running(self) -> bool:
        """Whether this process started the stream and it is alive."""
        return self._proc is not None and self._owner == os.getpid() and self._proc.poll() is None

    def start(self) -> bool:
        """Start the stream unless it runs; True once it carries refusals."""
        return self._ensure() is not None

    def watch(self, tag: str) -> bool:
        """Start the stream unless it runs and register the run tagged ``tag`` on it; True when it can be read back."""
        generation = self._ensure()
        with self._changed:
            self._runs[tag] = _Run(NOT_STREAMED if generation is None else generation)
            self._trim()
        return generation is not None

    def finish(self, tag: str) -> None:
        """Mark the run tagged ``tag`` as ended; its refusals stay readable until the cap needs the room."""
        with self._changed:
            if tag in self._runs:
                self._runs[tag].ended = True
            self._trim()

    def settle(self) -> bool:
        """Wait until every refusal logged before this call has arrived; False when there is no stream."""
        if not self.start():
            return False
        if self._probe("settle", SETTLE_SECONDS):
            return True
        logger.warning("sandbox refusals cannot be reported: the system log stream did not answer in %s s", SETTLE_SECONDS)
        return False

    def denials(self, tag: str) -> list[dict[str, str]] | None:
        """The refusals the kernel logged for the run tagged ``tag``, without the start-up noise; None when they cannot be known."""
        with self._changed:
            run = self._runs.get(tag)
        if run is None or run.generation == NOT_STREAMED:
            logger.warning("sandbox refusals cannot be reported: no system log stream was running when the command started")
            return None
        if not self.settle():
            return None
        with self._changed:
            if self._generation != run.generation or not self.running():
                logger.warning("sandbox refusals cannot be reported: the system log stream restarted or ended after the command started")
                return None
            return [dict(entry) for entry in run.entries]

    def close(self) -> None:
        """Stop the stream this process started."""
        proc, self._proc = self._proc, None
        if proc is None or self._owner != os.getpid():
            return
        with contextlib.suppress(OSError):
            proc.terminate()
        try:
            proc.wait(2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        with contextlib.suppress(OSError):
            proc.stdout.close()

    def _ensure(self) -> int | None:
        """The generation of the running stream, starting one if needed; None when there is none."""
        with self._start_lock:
            if self.running():
                return self._generation
            if self._failed_at is not None and time.monotonic() - self._failed_at < RETRY_SECONDS:
                return None
            try:
                self._launch()
            except OSError as exc:
                self._fail(f"could not start {LOG}: {exc}")
                return None
            if not self._probe("ready", START_SECONDS):
                said = "; ".join(self._said)
                self.close()
                self._fail(f"the system log did not carry a test refusal ({said or 'it said nothing'})")
                return None
            self._failed_at = None
            return self._generation

    def _launch(self) -> None:
        self.close()
        self._generation += 1
        self._proc = start_grouped(
            [LOG, "stream", "--style", "ndjson", "--predicate", PREDICATE],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        self._owner = os.getpid()
        if not self._exit_hook:
            self._exit_hook = True
            atexit.register(self.close)
        threading.Thread(target=self._read, args=(self._proc,), daemon=True, name="denial-log").start()

    def _fail(self, reason: str) -> None:
        self._failed_at = time.monotonic()
        logger.warning("sandbox refusals cannot be reported: %s", reason)

    def _read(self, proc: subprocess.Popen[str]) -> None:
        with contextlib.suppress(ValueError, OSError):
            for line in proc.stdout:
                found = parse(line)
                if found is not None:
                    self._add(*found)
                elif not line.startswith("{") and line.strip():
                    self._said.append(line.strip()[:200])

    def _add(self, tag: str, entry: dict[str, str]) -> None:
        with self._changed:
            if tag.startswith(PROBE):
                if tag in self._probes:
                    self._probes[tag] = True
                    self._changed.notify_all()
                return
            run = self._runs.get(tag)
            if run is None:
                return
            if not NOISE.search(entry["target"]) and len(run.entries) < KEEP_PER_TAG:
                run.entries.append(entry)
            self._changed.notify_all()

    def _trim(self) -> None:
        """Drop the oldest ended runs while there are more than ``KEEP_TAGS``; a run still going is never dropped."""
        for tag in [tag for tag, run in self._runs.items() if run.ended]:
            if len(self._runs) <= KEEP_TAGS:
                break
            del self._runs[tag]

    def _probe(self, kind: str, seconds: float) -> bool:
        """Have the kernel refuse a profile tagged with a fresh token, again until the stream carries it."""
        token = f"{PROBE}{kind}-{secrets.token_hex(8)}"
        profile = f'(version 1)(deny default (with message "{PREFIX}{token}"))'
        deadline = time.monotonic() + seconds
        with self._changed:
            self._probes[token] = False
        try:
            while time.monotonic() < deadline and self.running():
                with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                    subprocess.run([SANDBOX_EXEC, "-p", profile, "--", PROBE_PROGRAM], stdin=subprocess.DEVNULL,
                                   capture_output=True, timeout=10, check=False)
                with self._changed:
                    if self._changed.wait_for(lambda: self._probes[token], 0.25):
                        return True
            return False
        finally:
            with self._changed:
                del self._probes[token]


_SHARED = DenialLog()


def shared() -> DenialLog:
    """The stream of this process."""
    return _SHARED
